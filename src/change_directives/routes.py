"""Change Directive API routes (D295 + Chunk 39 snapshots).

Mounts under ``/api/change-directives``. Routes:

- ``POST /``                         — create draft
- ``GET /``                          — list (visibility-filtered)
- ``GET /{directive_id}/snapshot``   — latest realization snapshot (Chunk 39)
- ``GET /{directive_id}/snapshots``   — snapshot history (Chunk 39)
- ``GET /{directive_id}``            — visibility-filtered read (+ optional ``latest_snapshot``)
- ``PATCH /{directive_id}``          — draft body-PATCH (allowlist)
- ``POST /{directive_id}/transition`` — sole status-writer route
- ``POST /{directive_id}/criteria``  — author EvidenceCriterion
- ``PATCH /{directive_id}/criteria/{cid}`` — approve / edit / manual-override

Mutating routes admit only ``requesting_user == directive.authored_by``
or ``admin_key_present`` (D296). Visibility resolution per row uses
:func:`src.permissions.change_directive_visibility.resolve_visibility`.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.analytics.metrics import (
    change_directive_created_total,
    change_directive_evidence_criterion_compiled_total,
    change_directive_metadata_edited_total,
    change_directive_transitioned_total,
)
from src.elicitation.event_writer import write_event
from src.elicitation.models import (
    ChangeDirectiveCreatedPayload,
    ChangeDirectiveFlaggedFromReviewPayload,
    ChangeDirectiveMetadataEditedPayload,
    ChangeDirectiveTransitionedPayload,
    ElicitationEventEnvelope,
    EvidenceCriterionAddedPayload,
)
from src.permissions.change_directive_visibility import resolve_visibility
from src.shared.database import get_db
from src.shared.llm_provider import LLMProvider, get_provider as _get_provider


def get_llm_provider() -> LLMProvider:
    """FastAPI dependency: return the configured ``LLMProvider``."""
    return _get_provider()

from . import repository
from .evidence_criterion import (
    compile_evidence_criterion,
    validate_operator_cypher,
    vocabulary_error_detail,
)
from .models import (
    ChangeDirectiveCreateRequest,
    ChangeDirectivePatchBody,
    CriterionCreateRequest,
    CriterionEvidenceResult,
    CriterionPatchRequest,
    DirectiveStatus,
    RealizationSnapshotPayload,
    VelocityBand,
    TransitionRequest,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/api/change-directives", tags=["change-directives"])


def _admin_key_present(request: Request) -> bool:
    """True iff the caller submitted a matching X-Admin-Key.

    Mirrors :class:`AuthMiddleware` Step 5. When ``GRACE_ADMIN_KEY`` is
    unset and the caller is loopback, treat the call as
    ``admin_key_present=False`` — the localhost bypass is for plumbing,
    not for visibility-override semantics.
    """
    admin_key = os.environ.get("GRACE_ADMIN_KEY", "")
    if not admin_key:
        return False
    submitted = request.headers.get("X-Admin-Key", "")
    if not submitted:
        return False
    import secrets as _secrets

    return _secrets.compare_digest(admin_key, submitted)


def _get_requesting_user(request: Request) -> UUID:
    """Pull ``X-Requesting-User`` from headers; default to a zero UUID.

    The frontend sends an actor uuid; tests pass the directive author so
    the visibility resolver admits them.
    """
    raw = request.headers.get("X-Requesting-User", "")
    if not raw:
        # Use a sentinel zero UUID — visibility resolver will reject
        # unless ``permission_matrix_default`` + admin override.
        return UUID("00000000-0000-0000-0000-000000000000")
    try:
        return UUID(raw)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_requesting_user_header"},
        )


def _require_author_or_admin(
    directive: dict[str, Any],
    requesting_user: UUID,
    admin_key_present: bool,
) -> None:
    if str(directive.get("authored_by")) == str(requesting_user):
        return
    if admin_key_present:
        return
    raise HTTPException(status_code=403, detail={"error": "forbidden"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _band_or_none(raw: str | None) -> VelocityBand | None:
    if raw in ("accelerating", "steady", "slowing", "stalled"):
        return raw  # type: ignore[return-value]
    return None


def _snapshot_payload_from_row(
    db: Session,
    directive_row: dict[str, Any],
    snap_row: dict[str, Any],
) -> RealizationSnapshotPayload:
    did = UUID(str(snap_row["directive_id"]))
    is_stalled = repository.compute_is_stalled_for_directive(
        db, did, directive_row
    )
    band_raw = repository.compute_velocity_band(snap_row, is_stalled)
    crit_raw = snap_row["criteria_results"]
    if isinstance(crit_raw, str):
        crit_raw = json.loads(crit_raw)
    crit_models = [
        CriterionEvidenceResult.model_validate(x) for x in crit_raw
    ]
    prog = snap_row.get("progress_percentage")
    return RealizationSnapshotPayload(
        id=snap_row["id"],
        directive_id=did,
        snapshot_at=snap_row["snapshot_at"],
        criteria_results=crit_models,
        progress_percentage=float(prog) if prog is not None else None,
        evidence_count_consistent=snap_row.get("evidence_count_consistent"),
        evidence_count_counter=snap_row.get("evidence_count_counter"),
        first_evidence_seen_at=snap_row.get("first_evidence_seen_at"),
        last_counter_evidence_seen_at=snap_row.get(
            "last_counter_evidence_seen_at"
        ),
        criteria_all_satisfied=snap_row.get("criteria_all_satisfied"),
        created_at=snap_row["created_at"],
        is_stalled=is_stalled,
        velocity_band=_band_or_none(band_raw),
    )


def _directive_list_enrichment(db: Session, d: dict[str, Any]) -> dict[str, Any]:
    did = UUID(str(d["directive_id"]))
    snap = repository.get_latest_snapshot(db, did)
    stalled = repository.compute_is_stalled_for_directive(db, did, d)
    band = repository.compute_velocity_band(snap, stalled)
    return {
        **d,
        "velocity_band": _band_or_none(band),
        "is_stalled": stalled,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_directive(
    body: ChangeDirectiveCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    requesting_user = _get_requesting_user(request)
    directive = repository.create(db, body, requesting_user)
    # Optionally seed initial evidence criteria as natural-language
    # placeholders (uncompiled). The author may PATCH them post-create.
    if body.tier == "Strategic_Initiative" and body.initial_evidence_criteria:
        for nl in body.initial_evidence_criteria:
            db.execute(
                text(
                    """
                    INSERT INTO change_directive_evidence_criteria (
                        criterion_id, directive_id, natural_language,
                        compiled_query, compilation_status, created_at, updated_at
                    ) VALUES (
                        :cid, :did, :nl, NULL, 'proposed', :now, :now
                    )
                    """
                ),
                {
                    "cid": str(uuid4()),
                    "did": str(directive["directive_id"]),
                    "nl": nl,
                    "now": _now(),
                },
            )
    db.commit()
    final = repository.get_by_id(db, directive["directive_id"])

    # D298 — telemetry emission (best-effort, never fail the route).
    try:
        change_directive_created_total.add(
            1, {"tier": body.tier, "outcome": "success"}
        )
        write_event(
            db,
            ElicitationEventEnvelope(
                event_id=uuid4(),
                event_type="change_directive_created",
                session_id=body.flagged_from_session_id or final["directive_id"],
                actor_type="human",
                phase_name="none",
                emitted_at=_now(),
                schema_version=1,
                grace_version="0.1.0",
                payload=ChangeDirectiveCreatedPayload(
                    directive_id=str(final["directive_id"]),
                    tier=body.tier,
                    visibility=str(final["visibility"]),
                    created_at=final["authored_at"],
                ).model_dump(),
                payload_schema_version=1,
            ),
        )
        if body.flagged_from_session_id is not None:
            write_event(
                db,
                ElicitationEventEnvelope(
                    event_id=uuid4(),
                    event_type="change_directive_flagged_from_review",
                    session_id=body.flagged_from_session_id,
                    actor_type="human",
                    phase_name="none",
                    emitted_at=_now(),
                    schema_version=1,
                    grace_version="0.1.0",
                    payload=ChangeDirectiveFlaggedFromReviewPayload(
                        directive_id=str(final["directive_id"]),
                        flagged_from_session_id=str(body.flagged_from_session_id),
                        flagged_from_element_name=body.flagged_from_element_name,
                        created_at=final["authored_at"],
                    ).model_dump(),
                    payload_schema_version=1,
                ),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("change_directive.telemetry_failed", error=str(exc))

    return final


@router.get("/{directive_id}/snapshot")
def get_latest_snapshot_route(
    directive_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> RealizationSnapshotPayload:
    requesting_user = _get_requesting_user(request)
    admin = _admin_key_present(request)
    directive = repository.get_by_id(db, directive_id)
    if directive is None or not resolve_visibility(
        directive, requesting_user, admin_key_present=admin
    ):
        raise HTTPException(status_code=404, detail="Not found")
    snap = repository.get_latest_snapshot(db, directive_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="Not found")
    return _snapshot_payload_from_row(db, directive, snap)


@router.get("/{directive_id}/snapshots")
def list_snapshots_route(
    directive_id: UUID,
    request: Request,
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[RealizationSnapshotPayload]:
    requesting_user = _get_requesting_user(request)
    admin = _admin_key_present(request)
    directive = repository.get_by_id(db, directive_id)
    if directive is None or not resolve_visibility(
        directive, requesting_user, admin_key_present=admin
    ):
        raise HTTPException(status_code=404, detail="Not found")
    hist = repository.list_snapshot_history(db, directive_id, limit=limit)
    return [
        _snapshot_payload_from_row(db, directive, row) for row in hist
    ]


@router.get("/{directive_id}")
def get_directive(
    directive_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    requesting_user = _get_requesting_user(request)
    admin = _admin_key_present(request)
    directive = repository.get_by_id(db, directive_id)
    if directive is None:
        raise HTTPException(
            status_code=404, detail={"error": "directive_not_found"}
        )
    if not resolve_visibility(directive, requesting_user, admin_key_present=admin):
        raise HTTPException(
            status_code=404, detail={"error": "directive_not_found"}
        )
    snap = repository.get_latest_snapshot(db, directive_id)
    latest = (
        _snapshot_payload_from_row(db, directive, snap).model_dump(mode="json")
        if snap
        else None
    )
    return {**directive, "latest_snapshot": latest}


@router.get("")
def list_change_directives(
    request: Request,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=200),
    tier: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    authored_by: UUID | None = Query(default=None),
    velocity_band: str | None = Query(default=None),
    is_stalled: bool | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    requesting_user = _get_requesting_user(request)
    admin = _admin_key_present(request)
    rows = repository.list_directives(
        db,
        cursor=cursor,
        limit=limit,
        tier=tier,
        status=status_filter,
        authored_by=authored_by,
    )
    visible = [
        r for r in rows
        if resolve_visibility(r, requesting_user, admin_key_present=admin)
    ]
    enriched = [_directive_list_enrichment(db, r) for r in visible]
    if velocity_band is not None:
        enriched = [
            r for r in enriched if r.get("velocity_band") == velocity_band
        ]
    if is_stalled is not None:
        enriched = [r for r in enriched if r.get("is_stalled") == is_stalled]
    return {"items": enriched, "count": len(enriched)}


@router.patch("/{directive_id}")
def patch_directive(
    directive_id: UUID,
    body: ChangeDirectivePatchBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    requesting_user = _get_requesting_user(request)
    admin = _admin_key_present(request)
    existing = repository.get_by_id(db, directive_id)
    if existing is None:
        raise HTTPException(
            status_code=404, detail={"error": "directive_not_found"}
        )
    _require_author_or_admin(existing, requesting_user, admin)
    incoming = body.model_dump(exclude_unset=True)
    before_snap = {
        k: existing.get(k)
        for k in incoming.keys()
        if k in incoming
    }

    def _norm(val: Any) -> Any:
        if isinstance(val, (dict, list)):
            return json.dumps(val, sort_keys=True, default=str)
        return val

    updated = repository.patch_draft_metadata(
        db, directive_id, body, requesting_user
    )
    fields_changed = [
        k
        for k in incoming.keys()
        if _norm(before_snap.get(k)) != _norm(updated.get(k))
    ]
    if fields_changed:
        try:
            change_directive_metadata_edited_total.add(1)
            b_vals = {k: before_snap[k] for k in fields_changed}
            a_vals = {k: updated.get(k) for k in fields_changed}
            write_event(
                db,
                ElicitationEventEnvelope(
                    event_id=uuid4(),
                    event_type="change_directive_metadata_edited",
                    session_id=directive_id,
                    actor_type="human",
                    phase_name="none",
                    emitted_at=_now(),
                    schema_version=1,
                    grace_version="0.1.0",
                    payload=ChangeDirectiveMetadataEditedPayload(
                        directive_id=directive_id,
                        editor_user_id=requesting_user,
                        fields_changed=fields_changed,
                        before_values=b_vals,
                        after_values=a_vals,
                        edited_at=_now(),
                    ).model_dump(mode="json"),
                    payload_schema_version=1,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("change_directive.telemetry_failed", error=str(exc))
    return updated


@router.post("/{directive_id}/transition")
def transition_directive(
    directive_id: UUID,
    body: TransitionRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    requesting_user = _get_requesting_user(request)
    admin = _admin_key_present(request)
    existing = repository.get_by_id(db, directive_id)
    if existing is None:
        raise HTTPException(
            status_code=404, detail={"error": "directive_not_found"}
        )
    _require_author_or_admin(existing, requesting_user, admin)
    from_state = str(existing["status"])
    result = repository.transition(
        db,
        directive_id,
        body.to_state,
        requesting_user,
        body.reason,
        superseded_by_directive_id=body.superseded_by_directive_id,
    )
    try:
        change_directive_transitioned_total.add(
            1, {"from_state": from_state, "to_state": body.to_state.value}
        )
        write_event(
            db,
            ElicitationEventEnvelope(
                event_id=uuid4(),
                event_type="change_directive_transitioned",
                session_id=directive_id,
                actor_type="human",
                phase_name="none",
                emitted_at=_now(),
                schema_version=1,
                grace_version="0.1.0",
                payload=ChangeDirectiveTransitionedPayload(
                    directive_id=str(directive_id),
                    from_state=from_state,
                    to_state=body.to_state.value,
                    transitioned_at=_now(),
                ).model_dump(),
                payload_schema_version=1,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("change_directive.telemetry_failed", error=str(exc))
    return result


def _ratified_segment_schema(db: Session) -> dict[str, Any]:
    """Compact vocabulary view of the active ratified ontology schema.

    Capture-the-why (F-0047c / ISS-0054, validation run 2026-07-03):
    ``create_criterion`` passed ``{}`` as the segment schema — the D293
    compiler had NO vocabulary to ground on and invented off-schema labels
    (`Zoning`) and edges (`has_zoning`) that EXPLAIN'd fine but could never
    be satisfied. This helper feeds the compiler the ratified entity-type
    and relationship names (with descriptions where cheap) so both the
    prompt legend and the post-compile vocabulary check are grounded.
    Read-only; returns ``{}`` when no active version exists (the compiler
    then skips vocabulary enforcement rather than flagging everything).
    """
    try:
        from src.ontology.database import get_active_version

        active = get_active_version(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("evidence_criterion.schema_lookup_failed", error=str(exc))
        return {}
    if active is None:
        return {}

    schema_json = active.schema_json or {}

    def _compact(section: Any) -> dict[str, Any]:
        if not isinstance(section, dict):
            return {}
        out: dict[str, Any] = {}
        for name, definition in section.items():
            description = ""
            if isinstance(definition, dict):
                description = str(definition.get("description") or "")
            out[str(name)] = {"description": description}
        return out

    return {
        "entity_types": _compact(schema_json.get("entity_types")),
        "relationships": _compact(schema_json.get("relationships")),
    }


async def _compile_or_record_failure(
    natural_language: str,
    segment_schema: dict[str, Any],
    llm_provider: LLMProvider,
) -> tuple[str | None, str, str | None]:
    """Run :func:`compile_evidence_criterion`; return (cypher, status, err)."""
    try:
        result = await compile_evidence_criterion(
            natural_language, segment_schema, llm_provider
        )
        return (
            result.compiled_query,
            result.compilation_status,
            result.error_detail,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("evidence_criterion.compile.error", error=str(exc))
        return (None, "proposed", f"compile_exception: {exc!s}")


@router.post("/{directive_id}/criteria", status_code=status.HTTP_201_CREATED)
async def create_criterion(
    directive_id: UUID,
    body: CriterionCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    llm_provider: LLMProvider = Depends(get_llm_provider),
) -> dict[str, Any]:
    requesting_user = _get_requesting_user(request)
    admin = _admin_key_present(request)
    existing = repository.get_by_id(db, directive_id)
    if existing is None:
        raise HTTPException(
            status_code=404, detail={"error": "directive_not_found"}
        )
    _require_author_or_admin(existing, requesting_user, admin)

    # F-0047c / ISS-0054: ground compilation on the ratified schema
    # vocabulary (was `{}` — the compiler had nothing to ground on).
    cypher, comp_status, err = await _compile_or_record_failure(
        body.natural_language, _ratified_segment_schema(db), llm_provider
    )
    criterion_id = uuid4()
    now = _now()
    db.execute(
        text(
            """
            INSERT INTO change_directive_evidence_criteria (
                criterion_id, directive_id, natural_language,
                measurement_kind, target_value, target_satisfied_when,
                compiled_query, compilation_status, error_detail,
                created_at, updated_at
            ) VALUES (
                :cid, :did, :nl,
                :mk, :tv, :tsw,
                :cq, :cs, :err,
                :now, :now
            )
            """
        ),
        {
            "cid": str(criterion_id),
            "did": str(directive_id),
            "nl": body.natural_language,
            "mk": body.measurement_kind,
            "tv": body.target_value,
            "tsw": body.target_satisfied_when,
            "cq": cypher,
            "cs": comp_status,
            "err": err,
            "now": now,
        },
    )
    db.commit()
    row = db.execute(
        text(
            "SELECT * FROM change_directive_evidence_criteria "
            "WHERE criterion_id = :cid"
        ),
        {"cid": str(criterion_id)},
    ).mappings().first()
    final_row = dict(row)
    try:
        change_directive_evidence_criterion_compiled_total.add(
            1,
            {
                "compilation_status": comp_status,
                "outcome": "success" if cypher else "error",
            },
        )
        write_event(
            db,
            ElicitationEventEnvelope(
                event_id=uuid4(),
                event_type="change_directive_evidence_criterion_added",
                session_id=directive_id,
                actor_type="human",
                phase_name="none",
                emitted_at=_now(),
                schema_version=1,
                grace_version="0.1.0",
                payload=EvidenceCriterionAddedPayload(
                    directive_id=str(directive_id),
                    criterion_id=str(criterion_id),
                    compilation_status=comp_status,
                    has_compiled_query=cypher is not None,
                    created_at=final_row["created_at"],
                ).model_dump(),
                payload_schema_version=1,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("change_directive.telemetry_failed", error=str(exc))
    return final_row


@router.patch("/{directive_id}/criteria/{criterion_id}")
async def patch_criterion(
    directive_id: UUID,
    criterion_id: UUID,
    body: CriterionPatchRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    requesting_user = _get_requesting_user(request)
    admin = _admin_key_present(request)
    existing = repository.get_by_id(db, directive_id)
    if existing is None:
        raise HTTPException(
            status_code=404, detail={"error": "directive_not_found"}
        )
    _require_author_or_admin(existing, requesting_user, admin)

    # Fetch the stored criterion up front: `approve` must re-check the
    # STORED query, and a missing row should 404 before any validation.
    stored_row = db.execute(
        text(
            "SELECT * FROM change_directive_evidence_criteria "
            "WHERE criterion_id = :cid AND directive_id = :did"
        ),
        {"cid": str(criterion_id), "did": str(directive_id)},
    ).mappings().first()
    if stored_row is None:
        raise HTTPException(
            status_code=404, detail={"error": "criterion_not_found"}
        )

    # Capture-the-why (F-0047c / ISS-0054 PATCH follow-up, 2026-07-03):
    # `edit` and `manual_override` previously accepted operator-supplied
    # Cypher with NO validation, and `approve` flipped to approved blindly
    # — an operator could hand-write off-schema or syntactically broken
    # Cypher into an approved criterion that silently never matched at
    # snapshot time. All three actions now run the create-path validation
    # stages; any failure lands/keeps the row `compilation_status=
    # "proposed"` with `error_detail` naming the failure (off-schema
    # tokens listed, or the EXPLAIN error) — never a silent
    # approved/manually_authored-as-valid.
    if body.action == "approve":
        stored_query = stored_row.get("compiled_query")
        if not stored_query:
            # Nothing to approve — the snapshot pipeline would have no
            # query to execute. Same 422 discipline as edit/override.
            raise HTTPException(
                status_code=422,
                detail={"error": "approve_requires_compiled_query"},
            )
        # Belt-and-braces (ISS-0054 PATCH follow-up): the stored query may
        # predate the vocabulary check (compiled before F-0047c landed) or
        # have been written by a pre-fix edit/manual_override — re-check
        # membership against the active ratified schema before flipping to
        # approved. DB-only; needs no ArcadeDB.
        vocab_err = vocabulary_error_detail(
            stored_query, _ratified_segment_schema(db)
        )
        if vocab_err is not None:
            new_status = "proposed"
            new_error: str | None = vocab_err
        else:
            new_status = "approved"
            new_error = None
        new_query = None  # leave existing
    elif body.action == "edit":
        if not body.compiled_query:
            raise HTTPException(
                status_code=422,
                detail={"error": "edit_requires_compiled_query"},
            )
        # Full create-path ladder: vocabulary + two-stage EXPLAIN
        # (ArcadeDB-unreachable degrades to a named failure, as at create).
        ok, err = await validate_operator_cypher(
            body.compiled_query, _ratified_segment_schema(db)
        )
        new_status = "manually_authored" if ok else "proposed"
        new_error = err
        new_query = body.compiled_query
    else:  # manual_override
        if not body.compiled_query:
            raise HTTPException(
                status_code=422,
                detail={"error": "manual_override_requires_compiled_query"},
            )
        ok, err = await validate_operator_cypher(
            body.compiled_query, _ratified_segment_schema(db)
        )
        new_status = "manually_authored" if ok else "proposed"
        new_error = err
        new_query = body.compiled_query

    # error_detail is always written: cleared on success, populated on
    # validation failure (a stale create-time error must not linger after
    # a successful edit — ISS-0054 PATCH follow-up).
    set_clauses = [
        "compilation_status = :cs",
        "error_detail = :err",
        "updated_at = :now",
    ]
    params: dict[str, Any] = {
        "cs": new_status,
        "err": new_error,
        "now": _now(),
        "cid": str(criterion_id),
        "did": str(directive_id),
    }
    if new_query is not None:
        set_clauses.append("compiled_query = :cq")
        params["cq"] = new_query

    db.execute(
        text(
            "UPDATE change_directive_evidence_criteria SET "
            + ", ".join(set_clauses)
            + " WHERE criterion_id = :cid AND directive_id = :did"
        ),
        params,
    )
    db.commit()
    row = db.execute(
        text(
            "SELECT * FROM change_directive_evidence_criteria "
            "WHERE criterion_id = :cid"
        ),
        {"cid": str(criterion_id)},
    ).mappings().first()
    return dict(row)
