"""Graph review-in-place write-back (review-protocol harness, 2026-06-10, Option A).

The Claude-as-LLM onboarding path writes straight to the graph
(``import_extraction``), so ``extraction_claims`` is empty for the tranche and the
native quarantine writer (:func:`claim_override_writer.promote_claim_to_graph`) has
nothing to operate on. This module is the *in-place* equivalent: a thin human-review
write path over the live graph that reuses the SAME proven primitives
``claim_override_writer`` uses — :func:`entity_ops.insert_entity` /
:func:`relationship_ops.insert_relationship` — so it inherits their guarantees:

  * **D106 idempotency** — ``insert_entity`` canonical-dedups; replaying the same
    enrichment returns the existing ``grace_id`` and does NOT double-insert.
  * **D452 decay stamps** — ``last_verified_at`` / ``confidence_at_verification`` /
    ``verdict='SUPPORTED'`` injected as properties at insert time (mirrors
    ``claim_override_writer`` L110-115 exactly).
  * **D514 supersession** — corrections set the ``superseded_by`` pointer (+ ``valid_to``)
    on the OLD vertex/edge while a NEW one carries the corrected fact. We supersede,
    never overwrite; the old row stays for the append-only audit.

``decision_source='human'`` rides as a graph property (there is no claim row to carry
it on this path), alongside the native ``human_validated=True`` field, so provenance is
self-contained in the graph. The human's rationale + the structural signal that
surfaced the item are stamped as ``review_rationale`` / ``review_signal`` /
``reviewed_by`` / ``review_session_id`` — this is the durable, queryable "deeper
context" the tranche review exists to capture.

**Domain-agnostic.** ``entity_type`` / ``relationship_type`` / ``properties`` are passed
in by the facilitator after reading them from the schema/graph at runtime. Nothing
legal-specific lives here — finance / projects tranches use the same two entry points.

R1 import boundary (mirrors ``claim_override_writer``): this module imports ONLY
``entity_ops`` / ``relationship_ops`` / ``cypher_utils`` from ``src.graph`` — never
``graph_writer`` (whose ``write_batch`` short-circuits on ``graph_written`` parents and
would break D106 at the per-fact boundary).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import structlog

from src.extraction.confidence_decay import DecayConfig
from src.graph.arcade_client import ArcadeClient
from src.graph.cypher_utils import escape_cypher_string
from src.graph.entity_models import EntityCreate, RelationshipCreate
from src.graph.entity_ops import insert_entity
from src.graph.relationship_ops import insert_relationship

log = structlog.get_logger()

_DECAY_CONFIG_PATH = "config/decay_config.yaml"


def _review_stamps(
    *, reviewer: str, rationale: str, structural_signal: str, session_id: str | None
) -> dict:
    """Build the property bundle stamped on every human-reviewed fact.

    Combines the D452 decay-verification stamps (so the confirmed fact is decay-
    eligible exactly like an accepted claim) with the review provenance that makes
    the human's judgment durable, queryable graph context.

    Invariant (D452): the decay batch eligibility predicate (``_VERIFIED_QUERY`` /
    ``_VERIFIED_EDGE_QUERY`` in ``confidence_decay.py``) requires ``last_verified_at``,
    ``confidence_at_verification`` and ``verdict`` all IS NOT NULL. Carve-out: stamps
    are merged into the same insert call (no second round-trip). Authorization: D452 +
    grace-review-protocol Write-back decision (Option A, 2026-06-10).
    """
    cfg = DecayConfig.from_yaml(_DECAY_CONFIG_PATH)
    stamps = {
        "last_verified_at": datetime.now(timezone.utc).isoformat(),
        "confidence_at_verification": cfg.default_confidence_at_verification,
        "verdict": "SUPPORTED",
        # Provenance — self-contained in the graph (no claim row on this path).
        "decision_source": "human",
        "reviewed_by": reviewer,
        "review_rationale": rationale,
        "review_signal": structural_signal,
    }
    if session_id:
        stamps["review_session_id"] = session_id
    return stamps


_SAFE_REL_TYPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


async def _existing_edge_grace_id(
    client: ArcadeClient, rel_type: str, source_grace_id: str, target_grace_id: str
) -> str | None:
    """Return the grace_id of an existing edge of ``rel_type`` between the two
    vertices, or None. Used to make enrichment edge-idempotent (D106 dedups
    vertices but NOT edges — re-applying a reviewed fact would otherwise double the
    edge). ``rel_type`` is a schema identifier; validated against a safe pattern
    before interpolation.
    """
    if not _SAFE_REL_TYPE.match(rel_type):
        return None
    s = escape_cypher_string(source_grace_id)
    t = escape_cypher_string(target_grace_id)
    query = (
        f"MATCH (a {{grace_id: '{s}'}})-[r:{rel_type}]->(b {{grace_id: '{t}'}}) "
        f"RETURN r.grace_id AS gid LIMIT 1"
    )
    res = await client.execute_cypher(query)
    rows = res.get("result", [])
    return rows[0].get("gid") if rows else None


async def _set_superseded_by(
    client: ArcadeClient, old_grace_id: str, new_grace_id: str
) -> None:
    """Stamp ``superseded_by`` (+ ``valid_to``=now) on the OLD vertex/edge in place.

    D514 capture-the-why: corrections supersede, never overwrite. The corrected fact
    is a new vertex/edge; the old one is closed by pointer + ``valid_to`` so the audit
    trail stays append-only. Mirrors ``communications/supersession._apply_supersession_write``
    (direct SQL UPDATE — ``entity_ops.update_entity`` only handles domain properties).
    Authorization: D514 + grace-review-protocol Write-back decision (Option A).
    """
    escaped_old = escape_cypher_string(old_grace_id)
    escaped_new = escape_cypher_string(new_grace_id)
    now_iso = datetime.now(timezone.utc).isoformat()
    query = (
        f"MATCH (n {{grace_id: '{escaped_old}'}}) "
        f"SET n.superseded_by = '{escaped_new}', n.valid_to = '{now_iso}' "
        f"RETURN n.grace_id"
    )
    await client.execute_cypher(query)


async def enrich_entity(
    client: ArcadeClient,
    *,
    entity_type: str,
    name: str,
    properties: dict | None = None,
    reviewer: str,
    rationale: str,
    structural_signal: str,
    ontology_module: str | None = None,
    schema_version: int | None = None,
    source_document_id: str | None = None,
    session_id: str | None = None,
) -> dict:
    """Add a MISSING entity the reviewer confirmed should exist.

    The common tranche case (e.g. an ``Obligation`` node the extractor never created).
    Pure insert — nothing to supersede. D106 dedup means a replay returns the existing
    ``grace_id`` rather than double-inserting.

    Returns ``{grace_id, created}``.
    """
    entity = EntityCreate(
        entity_type=entity_type,
        properties={
            "name": name,
            **(properties or {}),
            **_review_stamps(
                reviewer=reviewer,
                rationale=rationale,
                structural_signal=structural_signal,
                session_id=session_id,
            ),
        },
        ontology_module=ontology_module,
        schema_version=schema_version,
        source_document_id=source_document_id,
        human_validated=True,
    )
    resp = await insert_entity(client, entity)
    log.info(
        "graph_review.enriched_entity",
        entity_type=entity_type,
        name=name,
        grace_id=resp.grace_id,
        created=resp.created,
        reviewer=reviewer,
        signal=structural_signal,
    )
    return {"grace_id": resp.grace_id, "created": resp.created}


async def enrich_relationship(
    client: ArcadeClient,
    *,
    relationship_type: str,
    source_grace_id: str,
    target_grace_id: str,
    properties: dict | None = None,
    reviewer: str,
    rationale: str,
    structural_signal: str,
    ontology_module: str | None = None,
    schema_version: int | None = None,
    source_document_id: str | None = None,
    session_id: str | None = None,
    dedup: bool = True,
) -> dict:
    """Add a MISSING edge the reviewer confirmed (e.g. ``has_obligation``).

    Endpoints are passed by ``grace_id`` — the facilitator resolves them from the live
    graph (or from a just-created entity via :func:`enrich_entity`) before calling.
    Pure insert — nothing to supersede.

    Edge-idempotent: when ``dedup`` (default), an existing edge of the same type
    between the two vertices short-circuits the insert (D106 covers vertices, not
    edges — without this, re-running a sweep would double every edge).

    Returns ``{created: bool, grace_id, deduped?}``.
    """
    if dedup:
        existing = await _existing_edge_grace_id(
            client, relationship_type, source_grace_id, target_grace_id
        )
        if existing:
            log.info(
                "graph_review.edge_deduped",
                relationship_type=relationship_type,
                source_grace_id=source_grace_id,
                target_grace_id=target_grace_id,
                grace_id=existing,
            )
            return {"created": False, "deduped": True, "grace_id": existing}
    rel = RelationshipCreate(
        relationship_type=relationship_type,
        source_grace_id=source_grace_id,
        target_grace_id=target_grace_id,
        properties={
            **(properties or {}),
            **_review_stamps(
                reviewer=reviewer,
                rationale=rationale,
                structural_signal=structural_signal,
                session_id=session_id,
            ),
        },
        ontology_module=ontology_module,
        schema_version=schema_version,
        source_document_id=source_document_id,
    )
    resp = await insert_relationship(client, rel)
    log.info(
        "graph_review.enriched_relationship",
        relationship_type=relationship_type,
        source_grace_id=source_grace_id,
        target_grace_id=target_grace_id,
        grace_id=resp.grace_id,
        reviewer=reviewer,
        signal=structural_signal,
    )
    return {"created": True, "grace_id": resp.grace_id}


async def correct_entity(
    client: ArcadeClient,
    *,
    old_grace_id: str,
    entity_type: str,
    name: str,
    properties: dict | None = None,
    reviewer: str,
    rationale: str,
    structural_signal: str,
    ontology_module: str | None = None,
    schema_version: int | None = None,
    source_document_id: str | None = None,
    session_id: str | None = None,
) -> dict:
    """Replace a WRONG entity with a corrected one — supersede, never overwrite.

    Inserts the corrected entity (new ``grace_id``), then stamps ``superseded_by`` +
    ``valid_to`` on ``old_grace_id``. The old vertex is preserved for the append-only
    audit; queries that filter ``superseded_by IS NULL`` see only the live fact.

    Returns ``{new_grace_id, created, superseded}``.
    """
    new = await enrich_entity(
        client,
        entity_type=entity_type,
        name=name,
        properties=properties,
        reviewer=reviewer,
        rationale=rationale,
        structural_signal=structural_signal,
        ontology_module=ontology_module,
        schema_version=schema_version,
        source_document_id=source_document_id,
        session_id=session_id,
    )
    if new["grace_id"] == old_grace_id:
        # D106 returned the same canonical vertex — the "correction" matched the
        # existing fact. Nothing to supersede (superseding a row by itself would
        # orphan the live fact). Surface this rather than self-link.
        log.warning(
            "graph_review.correction_was_noop",
            grace_id=old_grace_id,
            reason="corrected entity canonically matched the original",
        )
        return {"new_grace_id": new["grace_id"], "created": new["created"], "superseded": False}
    await _set_superseded_by(client, old_grace_id, new["grace_id"])
    log.info(
        "graph_review.corrected_entity",
        old_grace_id=old_grace_id,
        new_grace_id=new["grace_id"],
        reviewer=reviewer,
        signal=structural_signal,
    )
    return {"new_grace_id": new["grace_id"], "created": new["created"], "superseded": True}


async def correct_relationship(
    client: ArcadeClient,
    *,
    old_grace_id: str,
    relationship_type: str,
    source_grace_id: str,
    target_grace_id: str,
    properties: dict | None = None,
    reviewer: str,
    rationale: str,
    structural_signal: str,
    ontology_module: str | None = None,
    schema_version: int | None = None,
    source_document_id: str | None = None,
    session_id: str | None = None,
) -> dict:
    """Replace a WRONG edge with a corrected one — supersede, never overwrite.

    Inserts the corrected edge, then stamps ``superseded_by`` + ``valid_to`` on the old
    edge ``old_grace_id``. Returns ``{created, superseded}``.
    """
    new = await enrich_relationship(
        client,
        relationship_type=relationship_type,
        source_grace_id=source_grace_id,
        target_grace_id=target_grace_id,
        properties=properties,
        reviewer=reviewer,
        rationale=rationale,
        structural_signal=structural_signal,
        ontology_module=ontology_module,
        schema_version=schema_version,
        source_document_id=source_document_id,
        session_id=session_id,
        dedup=False,  # a correction is an intentional new edge — never short-circuit
    )
    await _set_superseded_by(client, old_grace_id, new["grace_id"])
    log.info(
        "graph_review.corrected_relationship",
        old_grace_id=old_grace_id,
        relationship_type=relationship_type,
        reviewer=reviewer,
        signal=structural_signal,
    )
    return {"created": True, "superseded": True}
