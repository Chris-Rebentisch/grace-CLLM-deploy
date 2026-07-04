"""Sensitivity Gate Compliance Surface routes (Chunk 43, CP3 / D344).

Six routes under ``/api/sensitivity``:

1. ``POST /api/sensitivity/report/generate`` — run the report generator
   over the active matrix; 422 if no active matrix; 409 on duplicate
   without ``?force=true``; 429 on second ``?force=true`` within 60s
   per matrix.
2. ``GET /api/sensitivity/report/latest`` — most-recent report for the
   active matrix. 200 or 404.
3. ``GET /api/sensitivity/report/{report_id}`` — single report by UUID.
4. ``GET /api/sensitivity/report`` — paginated list keyed by
   ``matrix_id``.
5. ``GET /api/sensitivity/audit-trail`` — single-tag query-event filter.
   *Body wiring deferred to CP5* (the ArcadeDB ``Query_Event`` vertex
   property is added in CP5 per D349). The route ships in CP3 as a
   skeleton: it validates inputs, applies visibility-trimming via the
   existing ``Enforcer`` (no new admission primitive — D343), and
   returns an empty page until CP5 wires the underlying Cypher.
6. ``GET /api/sensitivity/audit-trail/{query_event_id}`` — single
   event view; 404 in v1 (CP5 lights it up).

D120/D217 discipline: ``coverage_score`` is server-side only. All five
read paths use ``_strip_coverage_score()`` to drop the field before
serialization.

R12 / D346: this module does NOT import ``SystemPrincipal`` (CP4 ships
the sentinel; route handlers resolve principals via
``from_admission_tree(request)``).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from threading import Lock
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from src.graph.arcade_client import ArcadeClient, ArcadeDBError
from src.graph.config import ArcadeConfig
from src.graph.cypher_utils import escape_cypher_string
from src.permissions import repository as _matrix_repo
from src.permissions import sensitivity_report as _report_engine
from src.permissions import sensitivity_repository as _report_repo
from src.permissions.enforcer import get_enforcer
from src.permissions.models import Allow, PermissionMatrix
from src.permissions.principal_context import from_admission_tree
from src.shared.database import get_db


logger = structlog.get_logger()


router = APIRouter(prefix="/api/sensitivity", tags=["sensitivity"])


# Best-effort metric emitters — CP7 wires the registrations.
try:  # pragma: no cover — exercised in CP7 telemetry tests
    from src.analytics.metrics import (
        record_sensitivity_coverage_band,
        record_sensitivity_report_generated,
    )
except Exception:  # noqa: BLE001
    def record_sensitivity_coverage_band(*_a, **_kw) -> None:  # type: ignore[misc]
        return None

    def record_sensitivity_report_generated(*_a, **_kw) -> None:  # type: ignore[misc]
        return None


def _emit_elicitation_event(event_type: str, payload: dict[str, Any]) -> None:
    """Best-effort telemetry append; never raises."""
    try:  # pragma: no cover — exercised in CP7 telemetry tests
        from src.elicitation.bridge import enqueue_event  # type: ignore

        enqueue_event(event_type=event_type, payload=payload)
    except Exception:  # noqa: BLE001
        logger.debug("sensitivity.telemetry.skipped", event_type=event_type)


# ----- Force-regen rate limit (in-memory, per-process) ----------------

_FORCE_REGEN_WINDOW_SECONDS = 60.0
_force_regen_last: dict[str, float] = {}
_force_regen_lock = Lock()


def _check_force_regen_rate_limit(matrix_id: UUID) -> None:
    """Raise 429 when a ``?force=true`` regenerate fires more than once
    per ``_FORCE_REGEN_WINDOW_SECONDS`` for the same matrix."""
    key = str(matrix_id)
    now = time.monotonic()
    with _force_regen_lock:
        prior = _force_regen_last.get(key)
        if prior is not None and (now - prior) < _FORCE_REGEN_WINDOW_SECONDS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    "force-regenerate rate limit (1 per minute per matrix)"
                ),
            )
        _force_regen_last[key] = now


# ----- Helpers --------------------------------------------------------


def _strip_coverage_score(report_dict: dict[str, Any]) -> dict[str, Any]:
    """Drop ``coverage_score`` before serializing (D120/D217)."""
    out = dict(report_dict)
    out.pop("coverage_score", None)
    return out


def _serialize_report_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    report = _report_repo.hydrate_report(row)
    payload = report.model_dump(mode="json")
    return _strip_coverage_score(payload)


def _decode_list_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        offset = int(cursor)
        if offset < 0:
            raise ValueError
        return offset
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid cursor") from exc


# ----- Request / response models --------------------------------------


class ReportListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reports: list[dict[str, Any]] = Field(default_factory=list)
    next_cursor: str | None = None


class AuditTrailRow(BaseModel):
    """Single audit-trail row. Body wired in CP5 (D349)."""

    model_config = ConfigDict(extra="forbid")

    query_event_id: UUID
    occurred_at: datetime
    sensitivity_tags: list[str] = Field(default_factory=list)


class AuditTrailListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[AuditTrailRow] = Field(default_factory=list)
    next_cursor: str | None = None


# ----- 1. POST /report/generate ---------------------------------------


@router.post(
    "/report/generate",
    status_code=status.HTTP_201_CREATED,
)
async def generate_sensitivity_report(
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Run the report generator over the active matrix and persist.

    422 when no active matrix is loaded; 409 on duplicate-without-force;
    429 on second force within 60s per matrix.
    """
    matrix_row = _matrix_repo.get_active_matrix(db)
    if matrix_row is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="no active matrix",
        )

    matrix_id: UUID = matrix_row["permission_matrix_id"]
    if not isinstance(matrix_id, UUID):
        matrix_id = UUID(str(matrix_id))

    existing = _report_repo.get_latest_for_matrix(db, matrix_id)
    if existing is not None and not force:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Sensitivity report already exists for the active matrix; "
                "pass ?force=true to regenerate"
            ),
        )

    if force:
        _check_force_regen_rate_limit(matrix_id)

    matrix_payload = matrix_row["payload"]
    if isinstance(matrix_payload, dict):
        matrix = PermissionMatrix.model_validate(matrix_payload)
    else:
        matrix = PermissionMatrix.model_validate_json(matrix_payload)

    generated_at = datetime.now(tz=timezone.utc)
    report = _report_engine.generate(
        matrix,
        permission_matrix_id=matrix_id,
        generated_at=generated_at,
    )

    inserted = _report_repo.insert_report(db, report=report)
    db.commit()

    record_sensitivity_report_generated()
    if report.coverage_band is not None:
        record_sensitivity_coverage_band(band=report.coverage_band)

    _emit_elicitation_event(
        "sensitivity_report_generated",
        {
            "report_id": str(report.report_id),
            "matrix_id": str(matrix_id),
            "coverage_band": report.coverage_band,
            "tag_count": len(report.tag_inventory),
            "untagged_rule_count": len(report.untagged_rules),
            "corpus_below_floor": report.corpus_below_floor,
        },
    )

    return _serialize_report_row(inserted) or {}


# ----- 2. GET /report/latest ------------------------------------------


@router.get("/report/latest")
async def get_latest_sensitivity_report(
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    matrix_row = _matrix_repo.get_active_matrix(db)
    if matrix_row is None:
        raise HTTPException(status_code=404, detail="No active matrix")
    matrix_id = matrix_row["permission_matrix_id"]
    if not isinstance(matrix_id, UUID):
        matrix_id = UUID(str(matrix_id))
    row = _report_repo.get_latest_for_matrix(db, matrix_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No report for active matrix")
    serialized = _serialize_report_row(row)
    return serialized or {}


# ----- 4. GET /report (list — registered before /report/{report_id}) --


@router.get("/report")
async def list_sensitivity_reports(
    matrix_id: UUID = Query(..., description="Permission matrix UUID"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> ReportListResponse:
    offset = _decode_list_cursor(cursor)
    rows = _report_repo.list_reports_for_matrix(
        db, matrix_id=matrix_id, limit=limit + 1, offset=offset
    )
    next_cursor: str | None = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = str(offset + limit)
    serialized = [
        _serialize_report_row(r) or {} for r in rows
    ]
    return ReportListResponse(reports=serialized, next_cursor=next_cursor)


# ----- 3. GET /report/{report_id} -------------------------------------


@router.get("/report/{report_id}")
async def get_sensitivity_report_by_id(
    report_id: UUID,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = _report_repo.get_report_by_id(db, report_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Report not found")
    serialized = _serialize_report_row(row)
    return serialized or {}


# ----- ArcadeDB client singleton (CP5 / D349) -------------------------

_arcade_client: ArcadeClient | None = None


def _get_arcade_client() -> ArcadeClient:
    """Lazy ArcadeDB client for CP5 audit-trail reads."""
    global _arcade_client
    if _arcade_client is None:
        # Phase-9 fix: pull from settings (default ArcadeConfig() uses 30s).
        from src.shared.config import get_settings

        _arcade_client = ArcadeClient(
            config=ArcadeConfig.from_settings(get_settings())
        )
    return _arcade_client


def _decode_audit_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        offset = int(cursor)
        if offset < 0:
            raise ValueError
        return offset
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid cursor") from exc


def _decode_persisted_tags(raw: Any) -> list[str]:
    """Decode the bar-delimited STRING form back into a tag list.

    Persisted form: ``"|tag1|tag2|"``. Empty / None → ``[]``. Tags
    appear in canonical sort order (writer dedup-sorts at INSERT).
    """
    if not raw or not isinstance(raw, str):
        return []
    return [seg for seg in raw.split("|") if seg]


def _row_to_audit_trail(row: dict[str, Any]) -> AuditTrailRow | None:
    """Project an ArcadeDB Query_Event row into an :class:`AuditTrailRow`.

    Returns ``None`` when the row is missing required fields — those
    rows are silently dropped rather than raising (graph rows authored
    before D349 omit the tag property entirely).
    """
    qeid = row.get("query_event_id")
    occurred_at = row.get("query_timestamp")
    if not qeid or not occurred_at:
        return None
    try:
        qeid_uuid = UUID(str(qeid))
    except (ValueError, TypeError):
        return None
    if isinstance(occurred_at, str):
        try:
            ts = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        except ValueError:
            return None
    elif isinstance(occurred_at, datetime):
        ts = occurred_at
    else:
        return None
    return AuditTrailRow(
        query_event_id=qeid_uuid,
        occurred_at=ts,
        sensitivity_tags=_decode_persisted_tags(row.get("sensitivity_tags")),
    )


# ----- 5. GET /audit-trail (CP5 / D349 wired) -------------------------


@router.get("/audit-trail")
async def list_sensitivity_audit_trail(
    request: Request,
    tag: str = Query(..., min_length=1, description="Single sensitivity tag"),
    matrix_id: UUID | None = Query(default=None),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
) -> AuditTrailListResponse:
    """Single-tag audit-trail filter.

    D349 (Chunk 43 CP5): reads the ArcadeDB ``Query_Event`` vertex set
    where the bar-delimited ``sensitivity_tags`` property contains the
    requested tag; visibility-trimming is performed via
    ``Enforcer.enforce()`` per row BEFORE cursor encoding (D343 — no
    new admission primitive).

    R12 / D346: principal is resolved via
    :func:`from_admission_tree` (NOT ``SystemPrincipal``).
    """
    principal = from_admission_tree(request)
    enforcer = get_enforcer()

    offset = _decode_audit_cursor(cursor)
    needle = f"|{tag.strip()}|"

    # Build the OpenCypher with parameter binding so the tag literal
    # never enters the query string directly. The escape on
    # matrix_id is purely defense-in-depth — UUID validation has
    # already happened in the FastAPI signature.
    where_clauses = ["q.sensitivity_tags CONTAINS $tag_needle"]
    params: dict[str, Any] = {"tag_needle": needle}
    if matrix_id is not None:
        where_clauses.append("q.sensitivity_tags_matrix_id = $matrix_id")
        params["matrix_id"] = str(matrix_id)
    if from_ is not None:
        where_clauses.append("q.query_timestamp >= $ts_from")
        params["ts_from"] = from_.isoformat()
    if to is not None:
        where_clauses.append("q.query_timestamp <= $ts_to")
        params["ts_to"] = to.isoformat()

    where_sql = " AND ".join(where_clauses)
    cypher = (
        f"MATCH (q:Query_Event) WHERE {where_sql} "
        f"RETURN q.query_event_id AS query_event_id, "
        f"q.query_timestamp AS query_timestamp, "
        f"q.sensitivity_tags AS sensitivity_tags, "
        f"q.sensitivity_tags_matrix_id AS sensitivity_tags_matrix_id "
        f"ORDER BY q.query_timestamp DESC "
        f"SKIP {int(offset)} LIMIT {int(limit) + 1}"
    )

    client = _get_arcade_client()
    try:
        result = await client.execute_cypher(cypher, params=params)
    except (ConnectionError, ArcadeDBError) as exc:
        logger.warning(
            "sensitivity.audit_trail.arcade_unavailable",
            error=str(exc),
        )
        return AuditTrailListResponse(events=[], next_cursor=None)
    except Exception as exc:  # noqa: BLE001 — degrade quietly
        logger.warning(
            "sensitivity.audit_trail.query_failed",
            error=str(exc),
        )
        return AuditTrailListResponse(events=[], next_cursor=None)

    rows_raw = result.get("result", []) or []
    parsed: list[AuditTrailRow] = []
    for raw in rows_raw:
        if not isinstance(raw, dict):
            continue
        row = _row_to_audit_trail(raw)
        if row is None:
            continue
        parsed.append(row)

    # Visibility-trim per row through the existing Enforcer (D343 — no
    # new admission primitive). Trim happens BEFORE cursor encoding so
    # cursor stability holds across principal changes.
    trimmed: list[AuditTrailRow] = []
    for row in parsed:
        decision = enforcer.enforce(
            principal,
            "retrieval_query_event",
            str(row.query_event_id),
            "view",
        )
        if isinstance(decision, Allow):
            trimmed.append(row)

    next_cursor: str | None = None
    if len(trimmed) > limit:
        trimmed = trimmed[:limit]
        next_cursor = str(offset + limit)

    _emit_elicitation_event(
        "sensitivity_audit_trail_viewed",
        {
            "tag": tag,
            "matrix_id": str(matrix_id) if matrix_id is not None else None,
            "result_count": len(trimmed),
        },
    )

    return AuditTrailListResponse(events=trimmed, next_cursor=next_cursor)


# ----- 6. GET /audit-trail/{query_event_id} (CP5 / D349 wired) --------


@router.get("/audit-trail/{query_event_id}")
async def get_sensitivity_audit_trail_event(
    query_event_id: UUID,
    request: Request,
) -> dict[str, Any]:
    """Single audit-trail event detail (D349, Chunk 43 CP5).

    Reads the ArcadeDB ``Query_Event`` vertex by ``query_event_id``.
    Returns 404 when the vertex is missing OR when the principal lacks
    visibility through ``Enforcer.enforce()`` (intentional opacity —
    do not leak existence).
    """
    principal = from_admission_tree(request)
    enforcer = get_enforcer()

    decision = enforcer.enforce(
        principal,
        "retrieval_query_event",
        str(query_event_id),
        "view",
    )
    if not isinstance(decision, Allow):
        raise HTTPException(status_code=404, detail="Query event not found")

    escaped = escape_cypher_string(str(query_event_id))
    cypher = (
        f"MATCH (q:Query_Event {{query_event_id: '{escaped}'}}) "
        f"RETURN q.query_event_id AS query_event_id, "
        f"q.query_timestamp AS query_timestamp, "
        f"q.sensitivity_tags AS sensitivity_tags, "
        f"q.sensitivity_tags_matrix_id AS sensitivity_tags_matrix_id "
        f"LIMIT 1"
    )
    client = _get_arcade_client()
    try:
        result = await client.execute_cypher(cypher)
    except (ConnectionError, ArcadeDBError) as exc:
        logger.warning(
            "sensitivity.audit_trail_event.arcade_unavailable",
            error=str(exc),
        )
        raise HTTPException(
            status_code=503, detail="audit-trail backend unavailable"
        ) from exc
    rows_raw = result.get("result", []) or []
    for raw in rows_raw:
        if not isinstance(raw, dict):
            continue
        row = _row_to_audit_trail(raw)
        if row is None:
            continue
        return row.model_dump(mode="json")
    raise HTTPException(status_code=404, detail="Query event not found")


__all__ = ["router"]
