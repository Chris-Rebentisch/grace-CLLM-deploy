"""Per-claim writer: human override path that promotes one claim to graph.

Chunk 30, D230. Bridges PostgreSQL claim audit trail to ArcadeDB without
touching :func:`src.extraction.graph_writer.write_batch` or its D106
idempotency contract. The batch writer short-circuits when the parent
extraction event status is ``graph_written`` — exactly the state every
quarantined claim's parent batch is in. Reusing the batch writer would
either violate D106 or force reconstruction of artificial
``ExtractionBatch`` wrappers for one-claim operations.

Imports ONLY ``entity_ops`` and ``relationship_ops`` from ``src.graph``.
The static import boundary is the spec's R1 enforcement (do NOT import
``graph_writer``). PostgreSQL bookkeeping reuses
:func:`update_claim_status` and :func:`update_claim_resolved_endpoints`
from ``claim_database`` without modifying the library.

The new ``human_decided_at`` column (Alembic
``c30_extraction_claims_human_decided_at``) is set atomically with
``decision_source='human'`` on accept or reject via a raw UPDATE
(``claim_database.update_claim_status`` does not yet accept that
parameter and the spec keeps the library API unchanged).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import sqlalchemy as sa
import structlog
from sqlalchemy.orm import Session

from src.extraction.claim_database import (
    update_claim_resolved_endpoints,
    update_claim_status,
)
from src.extraction.claim_models import Claim, ClaimStatus
from src.extraction.confidence_decay import DecayConfig
from src.graph.arcade_client import ArcadeClient
from src.graph.entity_models import EntityCreate, RelationshipCreate
from src.graph.entity_ops import canonical_lookup, insert_entity
from src.graph.relationship_ops import insert_relationship

log = structlog.get_logger()


def _stamp_human_decided_at(session: Session, claim_id: str) -> None:
    """Set ``human_decided_at`` to now() (UTC) on the given claim row.

    Raw SQL because ``claim_database.update_claim_status`` does not yet
    accept a ``decided_at`` parameter (spec §18 #2 — minimal-diff;
    library public API is unchanged).
    """
    session.execute(
        sa.text(
            "UPDATE extraction_claims SET human_decided_at = :ts "
            "WHERE claim_id = :cid"
        ),
        {"ts": datetime.now(timezone.utc), "cid": UUID(claim_id)},
    )
    session.flush()


async def promote_claim_to_graph(
    claim: Claim,
    reviewer: str,
    notes: str | None,
    session: Session,
    arcade_client: ArcadeClient,
) -> dict:
    """Promote an accepted claim to ArcadeDB and update PostgreSQL bookkeeping.

    Behaviour matches D230:
      * Entity claims insert via ``entity_ops.insert_entity``. The
        ``insert_entity`` function performs canonical dedup, so replaying
        the same accept call returns the existing grace_id and does NOT
        double-insert (D106 idempotency preserved at the per-claim
        boundary).
      * Relationship claims insert via ``relationship_ops.insert_relationship``
        using the resolved subject/object grace_ids stamped on the claim.
      * After the graph write succeeds, the claim row is updated with
        ``status = AUTO_ACCEPTED``, ``decision_source = 'human'``, and
        ``human_decided_at = now()``.

    Returns a dict matching the shape consumed by the API
    ``AcceptClaimResponse.graph_write_result`` field.

    Reviewer / notes are accepted for symmetry with the API contract but
    are not persisted on the claim row in this chunk — the supersession
    chain captures them in higher-level audit (Chunk 31 admin-key
    middleware will surface reviewer identity).
    """
    _ = (reviewer, notes)  # accepted for API symmetry, not persisted here.

    write_result: dict = {
        "entities_created": 0,
        "entities_matched": 0,
        "relationships_created": 0,
    }

    if claim.entity_type:
        # D452 — Stamp decay-eligibility properties at accept time.
        # Invariant: decay batch eligibility predicate (_VERIFIED_QUERY /
        # _VERIFIED_EDGE_QUERY in confidence_decay.py L166-176) requires
        # last_verified_at, confidence_at_verification, and verdict all
        # IS NOT NULL; L295-309 is the redundant post-fetch Python guard.
        # Carve-out: stamps injected into same insert call (pre-insert
        # dict extension), no second round-trip.
        # Authorization: D452 + ratified outline §2.3.
        _decay_cfg = DecayConfig.from_yaml("config/decay_config.yaml")
        _verification_stamps = {
            "last_verified_at": datetime.now(timezone.utc).isoformat(),
            "confidence_at_verification": _decay_cfg.default_confidence_at_verification,
            "verdict": "SUPPORTED",
        }
        entity = EntityCreate(
            entity_type=claim.entity_type,
            properties={
                "name": claim.subject_name,
                **(claim.properties_json or {}),
                **_verification_stamps,
            },
            extraction_event_id=claim.extraction_event_id,
            schema_version=claim.schema_version,
            ontology_module=claim.ontology_module,
            source_document_id=claim.source_document_id or None,
            human_validated=True,
        )
        resp = await insert_entity(arcade_client, entity)
        if resp.created:
            write_result["entities_created"] += 1
        else:
            write_result["entities_matched"] += 1
        update_claim_resolved_endpoints(
            session,
            claim.claim_id,
            resolved_subject_grace_id=resp.grace_id,
        )

    elif claim.relationship_type:
        # Phase-8 fix: quarantined relationship claims are quarantined at
        # the *verifier* stage, BEFORE the entity-resolution pass that
        # would normally populate ``resolved_subject_grace_id`` /
        # ``resolved_object_grace_id``. When an operator overrides the
        # verifier verdict via accept, attempt name-based resolution
        # against the existing graph (subject_type / object_type +
        # subject_name / object_name). Falls back to the original strict
        # check when name-based lookup also fails — surfaced as a
        # structured 422 by the route layer (see claim_routes.py).
        subj_id = claim.resolved_subject_grace_id
        obj_id = claim.resolved_object_grace_id

        if not subj_id and claim.subject_type and claim.subject_name:
            subj_id = await canonical_lookup(
                arcade_client, claim.subject_type, claim.subject_name
            )
            if subj_id:
                update_claim_resolved_endpoints(
                    session,
                    claim.claim_id,
                    resolved_subject_grace_id=subj_id,
                )

        if not obj_id and claim.object_type and claim.object_name:
            obj_id = await canonical_lookup(
                arcade_client, claim.object_type, claim.object_name
            )
            if obj_id:
                update_claim_resolved_endpoints(
                    session,
                    claim.claim_id,
                    resolved_object_grace_id=obj_id,
                )

        if not (subj_id and obj_id):
            missing: list[str] = []
            if not subj_id:
                missing.append(
                    f"subject {claim.subject_type or '?'}:{claim.subject_name or '?'}"
                )
            if not obj_id:
                missing.append(
                    f"object {claim.object_type or '?'}:{claim.object_name or '?'}"
                )
            raise ValueError(
                f"Relationship claim {claim.claim_id} cannot be promoted: "
                f"could not resolve {' and '.join(missing)} against the "
                "current graph. Either create the missing entity first or "
                "use Edit-and-Accept to supply matching names."
            )

        # D452 — Stamp decay-eligibility properties at accept time.
        # (Same invariant/carve-out/authorization as entity path above.)
        _decay_cfg = DecayConfig.from_yaml("config/decay_config.yaml")
        _verification_stamps = {
            "last_verified_at": datetime.now(timezone.utc).isoformat(),
            "confidence_at_verification": _decay_cfg.default_confidence_at_verification,
            "verdict": "SUPPORTED",
        }
        rel = RelationshipCreate(
            relationship_type=claim.relationship_type,
            source_grace_id=subj_id,
            target_grace_id=obj_id,
            properties={**(claim.properties_json or {}), **_verification_stamps},
            extraction_event_id=claim.extraction_event_id,
            schema_version=claim.schema_version,
            ontology_module=claim.ontology_module,
            source_document_id=claim.source_document_id or None,
        )
        await insert_relationship(arcade_client, rel)
        write_result["relationships_created"] += 1

    else:
        raise ValueError(
            f"Claim {claim.claim_id} has neither entity_type nor "
            "relationship_type — cannot promote."
        )

    update_claim_status(
        session,
        claim.claim_id,
        ClaimStatus.AUTO_ACCEPTED,
        decision_source="human",
    )
    _stamp_human_decided_at(session, claim.claim_id)

    log.info(
        "claim_override.promoted",
        claim_id=claim.claim_id,
        entity_type=claim.entity_type,
        relationship_type=claim.relationship_type,
        write_result=write_result,
    )
    return write_result


def mark_claim_rejected(
    claim: Claim,
    reviewer: str,
    notes: str | None,
    session: Session,
) -> None:
    """Reject a quarantined claim. No graph write; PostgreSQL only.

    Sets ``status = REJECTED``, ``decision_source = 'human'``, and
    ``human_decided_at = now()``.
    """
    _ = (reviewer, notes)
    update_claim_status(
        session,
        claim.claim_id,
        ClaimStatus.REJECTED,
        decision_source="human",
    )
    _stamp_human_decided_at(session, claim.claim_id)
    log.info("claim_override.rejected", claim_id=claim.claim_id)
