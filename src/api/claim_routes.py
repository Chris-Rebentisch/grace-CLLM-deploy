"""Quarantined claim review HTTP surface (Chunk 30, D230; Chunk 72a, D470).

Four endpoints:

- ``GET  /api/claims``                 — paged list with status/verdict/module filters.
- ``GET  /api/claims/{claim_id}``      — single claim detail by UUID (D470).
- ``POST /api/claims/{id}/accept``     — promote a claim (with optional Edit-and-Accept).
- ``POST /api/claims/{id}/reject``     — confirm rejection; PostgreSQL only.

Mutating routes ship naked per the Chunks 26–29 precedent. Chunk 31 lands
the ``GRACE_ADMIN_KEY`` middleware over them; the security-posture §14
appendix records the forward guarantee.

The list endpoint wraps :func:`src.extraction.claim_database.list_claims`
in a thin opaque-cursor adapter that mirrors the Chunk 28 graph-paging
pattern: cursor encodes ``(offset, filter_fingerprint)``; if a caller
re-enters the next page with different filters the offset resets to 0.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from datetime import datetime
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.extraction.claim_database import (
    get_claim,
    insert_claim,
    list_claims,
    update_claim_status,
)
from src.extraction.claim_models import Claim, ClaimStatus, ClaimVerdict
from src.extraction.claim_override_writer import (
    mark_claim_rejected,
    promote_claim_to_graph,
)
from src.graph.arcade_client import ArcadeClient, get_arcade_client
from src.shared.database import get_db

log = structlog.get_logger()

router = APIRouter(prefix="/api/claims", tags=["claims"])


def _emit_claim_disposition_event(
    *,
    event_type: str,
    claim: Claim,
    reviewer: str,
    was_modified: bool | None = None,
) -> None:
    """F-014 / ISS-0012: server-side audit emission for claim dispositions.

    Driving ``/api/claims/{id}/accept|reject`` directly (curl, scripts,
    third-party UI) previously left ``elicitation_events`` empty — event
    capture was entirely client-side. Reuses the existing D234 EventTypes
    ``claim_disposition_accepted`` / ``claim_disposition_rejected`` (no new
    enum members invented); payload fields are hashed per the D234 payload
    contract. Best-effort: an audit-event failure must never break the route.
    Server-emitted rows carry ``actor_type="system"`` (set by the bridge) so
    they stay distinguishable from any client-emitted duplicate (append-only
    double emission is acceptable); ``reviewer`` rides along as envelope
    ``agent_id``.
    """
    try:
        from src.elicitation.bridge import enqueue_event

        payload: dict[str, Any] = {
            "claim_id_hash": hashlib.sha256(claim.claim_id.encode("utf-8")).hexdigest(),
            "reviewer_hash": hashlib.sha256(reviewer.encode("utf-8")).hexdigest(),
            "ontology_module": claim.ontology_module or "unknown",
        }
        if was_modified is not None:
            payload["was_modified"] = was_modified
        enqueue_event(
            event_type=event_type,
            payload=payload,
            agent_id=reviewer,
            delegation_source="user_direct",
        )
    except Exception as exc:  # noqa: BLE001 — log-and-continue by design
        log.warning(
            "claims.audit_event_failed",
            event_type=event_type,
            claim_id=claim.claim_id,
            error=str(exc),
        )


# --- Resolve-suggestion helper (F-0025c / ISS-0051) -------------------------

_TYPE_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_RESOLVE_HINT = (
    "None of the claim's endpoint names resolved to an existing graph entity. "
    "Use Edit-and-Accept with one of the suggested canonical names (or create "
    "the missing entity first, then re-accept)."
)


async def _suggest_resolution_candidates(
    arcade_client: ArcadeClient,
    claim: Claim,
    limit: int = 5,
) -> dict[str, list[dict[str, str]]]:
    """Best-effort nearest-entity suggestions for a failed plain accept.

    F-0025c / ISS-0051 (validation run 2026-07-03) capture-the-why:
    a plain accept whose object name did not resolve returned an opaque 422,
    forcing operators into edit-and-accept archaeology. This helper runs a
    cheap token-CONTAINS lookup (same ArcadeDB client the accept path already
    holds) per unresolved endpoint and returns up to ``limit`` candidate
    ``{name, grace_id, entity_type}`` rows so the 422 detail can point at the
    canonical spelling. Strictly best-effort: any lookup failure yields an
    empty suggestion list and never masks the original 422.
    """
    suggestions: dict[str, list[dict[str, str]]] = {}
    if not claim.relationship_type:
        return suggestions

    from src.graph.cypher_utils import escape_cypher_string

    endpoints = [
        ("subject", claim.subject_type, claim.subject_name),
        ("object", claim.object_type, claim.object_name),
    ]
    for role, entity_type, name in endpoints:
        if not name:
            continue
        found: list[dict[str, str]] = []
        try:
            tokens = sorted(
                {t for t in re.split(r"\W+", name) if len(t) >= 3},
                key=len,
                reverse=True,
            )
            # Type-scoped label only when it is a safe identifier (values come
            # from the claims table, but never interpolate unvetted text into
            # a Cypher label).
            label = (
                f":{entity_type}"
                if entity_type and _TYPE_LABEL_RE.match(entity_type)
                else ""
            )
            for token in tokens[:3]:
                esc = escape_cypher_string(token)
                query = (
                    f"MATCH (n{label}) "
                    f"WHERE toLower(n.name) CONTAINS toLower('{esc}') "
                    f"RETURN n.name AS name, n.grace_id AS grace_id LIMIT {limit}"
                )
                result = await arcade_client.execute_cypher(query)
                for row in result.get("result", []) or []:
                    if not isinstance(row, dict):
                        continue
                    cand_name = row.get("name")
                    if not cand_name or any(c["name"] == cand_name for c in found):
                        continue
                    found.append(
                        {
                            "name": str(cand_name),
                            "grace_id": str(row.get("grace_id") or ""),
                            "entity_type": entity_type or "",
                        }
                    )
                if found:
                    break
        except Exception as exc:  # noqa: BLE001 — suggestions must never break the 422
            log.warning(
                "claims.resolve_suggestion_failed",
                claim_id=claim.claim_id,
                endpoint=role,
                error=str(exc),
            )
        suggestions[role] = found[:limit]
    return suggestions


# --- Cursor adapter --------------------------------------------------------


def _filter_fingerprint(
    status: str | None,
    verdict: str | None,
    ontology_module: str | None,
    source_document_id: str | None,
) -> str:
    """Stable hash of the active filter set; embedded in the cursor."""
    payload = json.dumps(
        {
            "status": status,
            "verdict": verdict,
            "ontology_module": ontology_module,
            "source_document_id": source_document_id,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _encode_cursor(offset: int, fp: str) -> str:
    return base64.urlsafe_b64encode(
        json.dumps({"o": offset, "f": fp}, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[int, str]:
    """Return ``(offset, filter_fingerprint)``. Malformed cursors raise 422."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        data = json.loads(raw)
        return int(data["o"]), str(data["f"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Malformed cursor: {exc}")


# --- Response models -------------------------------------------------------


class EvidenceSpanRecord(BaseModel):
    """Evidence span as exposed to the UI."""

    text: str
    start_char: int
    end_char: int


class ClaimRecord(BaseModel):
    """Wire-shape Claim used by the UI; mirrors :class:`Claim` minus extraction internals."""

    claim_id: str
    extraction_event_id: str | None
    entity_type: str | None
    relationship_type: str | None
    subject_name: str
    predicate: str | None
    object_name: str | None
    evidence_spans: list[EvidenceSpanRecord]
    status: str
    verdict: str | None
    decision_source: str | None
    human_decided_at: str | None
    ontology_module: str | None
    source_document_id: str | None
    constraint_violations: list[dict[str, Any]] | None
    verifier_contradiction_reason: str | None
    supersedes_claim_id: str | None
    created_at: str


class ClaimListResponse(BaseModel):
    items: list[ClaimRecord]
    next_cursor: str | None
    total_count: int


class ModifiedClaimPayload(BaseModel):
    subject_name: str
    predicate: str | None = None
    object_name: str | None = None
    properties_json: dict[str, Any] | None = None
    # F-16 (validation run, 2026-07-01): the payload previously exposed only
    # name/predicate/object/properties, so a type-error quarantine (invalid
    # entity type, domain/range violation, deprecated type) had NO supersession
    # cure — reviewers could only reject. These optional type fields let an
    # Edit-and-Accept correct the type; the corrected claim is re-validated
    # against the active ontology so a fix into another invalid type is caught.
    entity_type: str | None = None
    subject_type: str | None = None
    object_type: str | None = None


def _revalidate_against_active(db: Session, claim_fields: dict) -> list:
    """Re-run the constraint validator on an edited claim against the active
    ontology schema (F-16). Returns the list of violations (empty when valid).
    Best-effort: if no active schema is available, returns no violations."""
    try:
        from src.extraction.claim_models import Claim as _Claim
        from src.extraction.constraint_validator import validate_claim
        from src.ontology.database import get_active_version

        active = get_active_version(db)
        if active is None or not getattr(active, "schema_json", None):
            return []
        candidate = _Claim(**claim_fields)
        # validate_claim returns list[ConstraintViolation]; the Claim model's
        # constraint_violations field holds those objects directly.
        return validate_claim(candidate, active.schema_json)
    except Exception:  # noqa: BLE001 — re-validation must not break accept
        # ISS-0051 rider (2026-07-03): this said ``logger`` but the module
        # binds ``log`` — the NameError turned the best-effort except path
        # into a 500 on every re-validation failure.
        log.warning("claim.revalidate_failed", exc_info=True)
        return []


class AcceptClaimRequest(BaseModel):
    reviewer: str = Field(min_length=1)
    notes: str | None = None
    modified_claim: ModifiedClaimPayload | None = None


class AcceptClaimResponse(BaseModel):
    claim_id: str
    status: str
    graph_write_result: dict[str, Any]
    superseded_claim_id: str | None = None


class RejectClaimRequest(BaseModel):
    reviewer: str = Field(min_length=1)
    notes: str | None = None


class RejectClaimResponse(BaseModel):
    claim_id: str
    status: str


# --- Helpers ---------------------------------------------------------------


def _claim_to_record(claim: Claim) -> ClaimRecord:
    """Convert a :class:`Claim` to its UI wire shape."""
    return ClaimRecord(
        claim_id=claim.claim_id,
        extraction_event_id=claim.extraction_event_id,
        entity_type=claim.entity_type,
        relationship_type=claim.relationship_type,
        subject_name=claim.subject_name,
        predicate=claim.predicate or None,
        object_name=claim.object_name,
        evidence_spans=[
            EvidenceSpanRecord(
                text=es.text,
                start_char=es.char_start,
                end_char=es.char_end,
            )
            for es in claim.evidence_spans
        ],
        status=claim.status.value,
        verdict=claim.verdict.value if claim.verdict else None,
        decision_source=claim.decision_source,
        human_decided_at=None,  # populated below from the row directly when available
        ontology_module=claim.ontology_module,
        source_document_id=claim.source_document_id or None,
        constraint_violations=(
            [cv.model_dump() for cv in claim.constraint_violations]
            if claim.constraint_violations
            else None
        ),
        verifier_contradiction_reason=claim.contradiction_reason or None,
        supersedes_claim_id=claim.supersedes_claim_id,
        created_at=claim.created_at.isoformat() if claim.created_at else "",
    )


def _fetch_human_decided_at(db: Session, claim_id: str) -> str | None:
    """Read the new D230 column directly (not exposed on the Claim model)."""
    from sqlalchemy import text

    row = db.execute(
        text("SELECT human_decided_at FROM extraction_claims WHERE claim_id = :cid"),
        {"cid": UUID(claim_id)},
    ).first()
    if not row or row.human_decided_at is None:
        return None
    decided: datetime = row.human_decided_at
    return decided.isoformat()


# --- Routes ----------------------------------------------------------------


@router.get("", response_model=ClaimListResponse)
def list_claims_route(
    status: str | None = Query(default=None),
    verdict: str | None = Query(default=None),
    ontology_module: str | None = Query(default=None),
    source_document_id: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> ClaimListResponse:
    """Return one page of claims; `cursor` carries an opaque offset+filter fingerprint."""
    # Validate enum filters early so the 422 surface is consistent with FastAPI.
    if status is not None:
        try:
            status_enum: ClaimStatus | None = ClaimStatus(status)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Unknown status: {status}")
    else:
        status_enum = None

    if verdict is not None:
        try:
            verdict_enum: ClaimVerdict | None = ClaimVerdict(verdict)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Unknown verdict: {verdict}")
    else:
        verdict_enum = None

    fp_now = _filter_fingerprint(status, verdict, ontology_module, source_document_id)
    if cursor:
        offset, fp_in = _decode_cursor(cursor)
        if fp_in != fp_now:
            # Filter changed mid-pagination → reset to first page.
            offset = 0
    else:
        offset = 0

    page = list_claims(
        db,
        status=status_enum,
        verdict=verdict_enum,
        source_document_id=source_document_id,
        ontology_module=ontology_module,
        limit=limit + 1,  # peek one ahead to know whether another page exists
        offset=offset,
    )

    has_more = len(page) > limit
    items_slice = page[:limit]

    records: list[ClaimRecord] = []
    for c in items_slice:
        rec = _claim_to_record(c)
        rec.human_decided_at = _fetch_human_decided_at(db, c.claim_id)
        records.append(rec)

    next_cursor = _encode_cursor(offset + limit, fp_now) if has_more else None

    # Total count: cheap upper bound — return current offset + items + (1 if more).
    # A precise total requires a second SELECT COUNT — out of scope for the thin
    # adapter; the UI uses next_cursor presence for pagination, and total_count
    # exposes a best-effort floor for badge rendering.
    total_count = offset + len(items_slice) + (1 if has_more else 0)

    return ClaimListResponse(
        items=records,
        next_cursor=next_cursor,
        total_count=total_count,
    )


@router.get("/{claim_id}", response_model=ClaimRecord)
def get_claim_detail(
    claim_id: str,
    db: Session = Depends(get_db),
) -> ClaimRecord:
    """Return a single claim by UUID (D470). 404 if not found.

    Reads from ``extraction_claims`` via the existing ``get_claim()`` helper
    at ``claim_database.py:201``. Does NOT read from ``extraction_events_pg``.
    """
    claim = get_claim(db, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    rec = _claim_to_record(claim)
    rec.human_decided_at = _fetch_human_decided_at(db, claim.claim_id)
    return rec


@router.post("/{claim_id}/accept", response_model=AcceptClaimResponse)
async def accept_claim_route(
    claim_id: str,
    request: AcceptClaimRequest,
    db: Session = Depends(get_db),
    arcade_client: ArcadeClient = Depends(get_arcade_client),
) -> AcceptClaimResponse:
    """Accept a quarantined claim, optionally as an Edit-and-Accept supersession."""
    original = get_claim(db, claim_id)
    if original is None:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")

    if request.modified_claim is None:
        # Plain accept: promote the original claim as-is.
        # Phase-8 fix: convert the writer's "cannot resolve endpoints"
        # ValueError into a structured 422 so the operator UI can show
        # the resolution failure instead of "Internal Server Error".
        try:
            write_result = await promote_claim_to_graph(
                claim=original,
                reviewer=request.reviewer,
                notes=request.notes,
                session=db,
                arcade_client=arcade_client,
            )
        except ValueError as exc:
            # F-0025c / ISS-0051: enrich the previously opaque 422 with
            # nearest-entity suggestions + an actionable hint so the operator
            # can jump straight to Edit-and-Accept with the canonical name.
            suggestions = await _suggest_resolution_candidates(
                arcade_client, original
            )
            raise HTTPException(
                status_code=422,
                detail={
                    "message": str(exc),
                    "suggestions": suggestions,
                    "hint": _RESOLVE_HINT,
                },
            ) from exc
        db.commit()
        # F-014 / ISS-0012: server-side audit trail (plain accept).
        _emit_claim_disposition_event(
            event_type="claim_disposition_accepted",
            claim=original,
            reviewer=request.reviewer,
            was_modified=False,
        )
        return AcceptClaimResponse(
            claim_id=original.claim_id,
            status=ClaimStatus.AUTO_ACCEPTED.value,
            graph_write_result=write_result,
            superseded_claim_id=None,
        )

    # Edit-and-Accept: write a new claim with the modified fields,
    # set supersedes_claim_id on the new claim, flip the original to
    # SUPERSEDED, then promote the new claim.
    mod = request.modified_claim
    new_claim_id = str(uuid.uuid4())
    # Phase-8 fix: ``extraction_unit_id`` is VARCHAR(64). The original
    # ID is a 64-char SHA-256 hex hash, so appending ``:h<8-hex>`` blew
    # the column width (74 chars). Truncate the original to 53 chars +
    # ``:h<8-hex>`` (11 chars) = exactly 64 chars. The supersession
    # relationship is captured separately via ``supersedes_claim_id``.
    new_unit = original.extraction_unit_id[:53] + ":h" + new_claim_id[:8]

    # F-16: allow type corrections; fall back to the original type when a field
    # is not supplied.
    new_entity_type = mod.entity_type or original.entity_type
    new_subject_type = mod.subject_type or original.subject_type
    new_object_type = mod.object_type or original.object_type
    new_props = mod.properties_json or original.properties_json

    new_claim = Claim(
        claim_id=new_claim_id,
        claim_fingerprint=original.claim_fingerprint,
        extraction_unit_id=new_unit,
        entity_type=new_entity_type,
        relationship_type=original.relationship_type,
        subject_name=mod.subject_name,
        predicate=mod.predicate or original.predicate,
        object_name=mod.object_name if mod.object_name is not None else original.object_name,
        subject_type=new_subject_type,
        object_type=new_object_type,
        properties_json=new_props,
        evidence_spans=original.evidence_spans,
        verdict=ClaimVerdict.PENDING,
        confidence=None,
        status=ClaimStatus.QUARANTINED,
        decision_source="human",
        # F-16: re-validate the corrected claim against the active ontology so a
        # correction into another invalid type is caught rather than silently
        # promoted. Empty list when the corrected type is valid.
        constraint_violations=_revalidate_against_active(db, {
            "entity_type": new_entity_type,
            "relationship_type": original.relationship_type,
            "subject_name": mod.subject_name,
            "predicate": mod.predicate or original.predicate,
            "object_name": mod.object_name if mod.object_name is not None else original.object_name,
            "subject_type": new_subject_type,
            "object_type": new_object_type,
            "properties_json": new_props,
            "confidence": original.confidence or 0.85,
            "schema_version": original.schema_version,
        }),
        supersedes_claim_id=original.claim_id,
        source_document_id=original.source_document_id,
        source_chunk_id=original.source_chunk_id,
        ontology_module=original.ontology_module,
        schema_version=original.schema_version,
        prompt_template_id=original.prompt_template_id,
        model_name=original.model_name,
        model_temperature=original.model_temperature,
        model_max_tokens=original.model_max_tokens,
        extraction_event_id=original.extraction_event_id,
        resolved_subject_grace_id=original.resolved_subject_grace_id,
        resolved_object_grace_id=original.resolved_object_grace_id,
    )
    insert_claim(db, new_claim)

    update_claim_status(
        db,
        original.claim_id,
        ClaimStatus.SUPERSEDED,
        decision_source="human",
    )

    # F-0025c / ISS-0051 (deferral closed 2026-07-03): the Edit-and-Accept
    # promote path still let the writer's "cannot resolve endpoints"
    # ValueError surface as an opaque 500 after the plain-accept path got
    # resolve suggestions. Mirror the same structured 422
    # ``{message, suggestions, hint}`` here so a correction whose canonical
    # name still doesn't resolve points the operator at candidates instead
    # of "Internal Server Error". Genuine unexpected errors still 500. The
    # raise fires before ``db.commit()``, so the inserted superseding claim
    # and the SUPERSEDED status flip are rolled back with the session.
    try:
        write_result = await promote_claim_to_graph(
            claim=new_claim,
            reviewer=request.reviewer,
            notes=request.notes,
            session=db,
            arcade_client=arcade_client,
        )
    except ValueError as exc:
        suggestions = await _suggest_resolution_candidates(
            arcade_client, new_claim
        )
        raise HTTPException(
            status_code=422,
            detail={
                "message": str(exc),
                "suggestions": suggestions,
                "hint": _RESOLVE_HINT,
            },
        ) from exc
    db.commit()

    # F-014 / ISS-0012: server-side audit trail (Edit-and-Accept supersession).
    _emit_claim_disposition_event(
        event_type="claim_disposition_accepted",
        claim=new_claim,
        reviewer=request.reviewer,
        was_modified=True,
    )

    return AcceptClaimResponse(
        claim_id=new_claim.claim_id,
        status=ClaimStatus.AUTO_ACCEPTED.value,
        graph_write_result=write_result,
        superseded_claim_id=original.claim_id,
    )


@router.post("/{claim_id}/reject", response_model=RejectClaimResponse)
def reject_claim_route(
    claim_id: str,
    request: RejectClaimRequest,
    db: Session = Depends(get_db),
) -> RejectClaimResponse:
    """Reject a quarantined claim. PostgreSQL only — no graph write."""
    claim = get_claim(db, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")
    mark_claim_rejected(
        claim=claim,
        reviewer=request.reviewer,
        notes=request.notes,
        session=db,
    )
    db.commit()
    # F-014 / ISS-0012: server-side audit trail (reject).
    _emit_claim_disposition_event(
        event_type="claim_disposition_rejected",
        claim=claim,
        reviewer=request.reviewer,
    )
    return RejectClaimResponse(
        claim_id=claim.claim_id,
        status=ClaimStatus.REJECTED.value,
    )
