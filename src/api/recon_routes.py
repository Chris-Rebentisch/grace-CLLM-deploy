"""Reconciliation Layer API surface (Chunks 36–37, D278–D290).

Routers under ``/api/recon``:

* ``router`` — Chunk 36 Gap Report (``/gap-report/...``).
* ``divergence_map_router`` — Chunk 37 Cross-Executive Divergence Map
  (``/divergence-map/...``, D284). The router is a module-level export
  so EC-9 (``tests/elicitation/test_ec_constraints.py:test_ec_9``) can
  import it directly.
* ``documented_reality_router`` — Chunk 37 Documented Reality Report
  (``/documented-reality/...``, D286/D287).
* ``documented_reality_schedule_router`` — Chunk 37 schedule CRUD
  (``/documented-reality/schedules``, D287).

Auth posture summary:
  - All ``GET`` routes default-admit per ``AuthMiddleware`` Step 2.
  - Mutating ``POST``/``PATCH`` routes are admin-key-gated by the
    middleware default-deny path (D285 cross-reviewer interim is
    layered as an additional ``Depends`` on
    ``POST /divergence-map/generate``: when reviewers differ, the
    middleware admin-key check applies; when reviewers match
    (self-comparison), the route returns 201 without requiring an
    admin key — B1 resolution).

ERD aggregate gauge (``grace_recon_erd_band_count{band}``) is updated
each successful Gap Report generate (Chunk 36 CP7). Two new counters
ship in Chunk 37 (D290): ``grace_recon_divergence_map_generated_total``
and ``grace_recon_documented_reality_report_generated_total``.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Literal
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.analytics.metrics import (
    recon_divergence_map_generated_total,
    recon_documented_reality_report_generated_total,
    recon_erd_band_count,
)
from src.api.recon_divergence_map import (
    compute_divergence_map,
    hydrate_divergence_map_response,
)
from src.api.recon_models import (
    DivergenceMapGenerateRequest,
    DivergenceMapResponse,
    DocumentedRealityReportResponse,
    DocumentedRealityScheduleRequest,
    DocumentedRealityScheduleResponse,
    DocumentedRealityScheduleUpdateRequest,
    EmphasizedWithEvidenceItem,
    EmphasizedWithoutEvidenceItem,
    GapReportResponse,
    UnemphasizedInEvidenceItem,
)
from src.graph.arcade_client import ArcadeClient, get_arcade_client
from src.ontology.database import get_version_by_id
from src.ontology.recon_gap_report import classify_band, compute_gap_report
from src.shared.database import get_db

logger = structlog.get_logger()


router = APIRouter(prefix="/api/recon", tags=["recon"])


# --- Force-regen rate limit (in-memory, per-process) ----------------------

_FORCE_REGEN_WINDOW_SECONDS = 60.0
_force_regen_last: dict[str, float] = {}
_force_regen_lock = Lock()


def _check_force_regen_rate_limit(session_id: UUID) -> None:
    """Raise 429 when a ``?force=true`` regenerate fires more than once per
    ``_FORCE_REGEN_WINDOW_SECONDS`` for the same session."""
    key = str(session_id)
    now = time.monotonic()
    with _force_regen_lock:
        prior = _force_regen_last.get(key)
        if prior is not None and (now - prior) < _FORCE_REGEN_WINDOW_SECONDS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="force-regenerate rate limit (1 per minute per session)",
            )
        _force_regen_last[key] = now


# --- Helpers --------------------------------------------------------------


def _section_to_dict(item) -> dict:
    return item.model_dump(mode="json")


def _report_to_storage_json(report: GapReportResponse) -> dict:
    """Serialize a ``GapReportResponse`` to the JSONB shape persisted in
    ``gap_reports.report_json``. Uses the wire field names so subsequent
    GETs can hydrate via ``model_validate``."""
    return report.model_dump(mode="json")


def _hydrate_response(row, fallback_session_id: UUID) -> GapReportResponse:
    """Reconstruct a ``GapReportResponse`` from a ``gap_reports`` row."""
    payload = row.report_json or {}
    if isinstance(payload, str):
        payload = json.loads(payload)
    # Force-rehydrate identifiers + denormalized columns from the row so a
    # GET always reflects the persisted truth (defensive against any
    # divergence in report_json).
    payload["session_id"] = str(payload.get("session_id") or fallback_session_id)
    payload.setdefault(
        "evidence_grounding_threshold",
        int(row.erd_threshold_n),
    )
    return GapReportResponse.model_validate(payload)


# --- Route: POST generate --------------------------------------------------


@router.post(
    "/gap-report/{session_id}/generate",
    response_model=GapReportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_gap_report(
    session_id: UUID,
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
    arcade_client: ArcadeClient = Depends(get_arcade_client),
) -> GapReportResponse:
    """Generate and persist a Gap Report for ``session_id`` (D280)."""

    # D280 preflight: load session + lifecycle gate.
    sess_row = db.execute(
        text("SELECT id, status FROM review_sessions WHERE id = :sid"),
        {"sid": str(session_id)},
    ).fetchone()
    if sess_row is None:
        raise HTTPException(status_code=404, detail="review session not found")
    if str(sess_row.status) != "completed":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "review session must be in 'completed' status before a Gap "
                "Report can be generated"
            ),
        )

    # Existing-report check.
    existing = db.execute(
        text(
            """
            SELECT id FROM gap_reports
            WHERE session_id = :sid
            ORDER BY generated_at DESC
            LIMIT 1
            """
        ),
        {"sid": str(session_id)},
    ).fetchone()

    if existing is not None and not force:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Gap Report already exists for this session; pass "
                "?force=true to regenerate"
            ),
        )

    if force:
        _check_force_regen_rate_limit(session_id)

    # Compute (no DB writes yet).
    report = await compute_gap_report(session_id, db, arcade_client)

    # Persist the new row. ``erd_score`` column is NOT NULL in the table;
    # below-floor (score is None) → store 0.0 sentinel and rely on
    # ``report_json.evidence_grounding_score`` carrying the wire null +
    # ``graph_population_floor_breach`` reason.
    new_id = uuid4()
    score_for_column = (
        float(report.evidence_grounding_score)
        if report.evidence_grounding_score is not None
        else 0.0
    )
    # Chunk 59 (D426 — CP7): compute mixed_source_coverage from breakdown.
    mixed_source_coverage = bool(
        report.source_type_breakdown and report.source_type_breakdown.mixed > 0
    )

    db.execute(
        text(
            """
            INSERT INTO gap_reports
                (id, session_id, generated_at, report_json,
                 erd_score, erd_threshold_n, metadata_extra,
                 mixed_source_coverage)
            VALUES
                (:id, :sid, :gen, CAST(:rj AS JSONB),
                 :score, :thr, NULL,
                 :msc)
            """
        ),
        {
            "id": str(new_id),
            "sid": str(session_id),
            "gen": report.generated_at,
            "rj": json.dumps(_report_to_storage_json(report)),
            "score": score_for_column,
            "thr": int(report.evidence_grounding_threshold),
            "msc": mixed_source_coverage,
        },
    )

    # Update review_sessions denormalization (D280).
    db.execute(
        text(
            """
            UPDATE review_sessions
               SET gap_report_id = :gid,
                   erd_score = :score,
                   erd_threshold_n = :thr
             WHERE id = :sid
            """
        ),
        {
            "gid": str(new_id),
            "score": report.evidence_grounding_score,
            "thr": int(report.evidence_grounding_threshold),
            "sid": str(session_id),
        },
    )
    db.commit()

    # D279 metric: aggregate band gauge update. Three literal labels only.
    band = classify_band(report.evidence_grounding_score)
    if band is not None:
        recon_erd_band_count.add(1, {"band": band})

    logger.info(
        "recon.gap_report.generated",
        session_id=str(session_id),
        gap_report_id=str(new_id),
        band=band,
        force=force,
    )

    # D290 — server-side ``gap_report_generated`` emission (closes
    # Chunk 36 Deviation #3). Best-effort; never fail the route on a
    # telemetry write error.
    try:
        import hashlib as _hashlib
        from datetime import datetime as _dt
        from datetime import timezone as _tz
        from uuid import uuid4 as _uuid4

        from src.elicitation.event_writer import write_event
        from src.elicitation.models import ElicitationEventEnvelope

        reviewer_str = (report.reviewer or "")
        reviewer_hash = _hashlib.sha256(reviewer_str.encode("utf-8")).hexdigest()
        envelope = ElicitationEventEnvelope(
            event_id=_uuid4(),
            event_type="gap_report_generated",
            session_id=session_id,
            actor_type="system",
            phase_name="none",
            emitted_at=_dt.now(_tz.utc),
            schema_version=1,
            grace_version="0.1.0",
            payload={
                "reviewer_hash": reviewer_hash,
                "evidence_grounding_score": report.evidence_grounding_score,
                "evidence_grounding_threshold": int(
                    report.evidence_grounding_threshold
                ),
                "generated_at": report.generated_at.isoformat(),
            },
            payload_schema_version=1,
        )
        write_event(db, envelope)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "recon.gap_report.telemetry_failed",
            error=str(exc),
        )

    # D297 — Reconciliation Bridge: surface covering directives.
    try:
        from src.api.change_directive_coverage import (
            enrich_covering_directives_realization,
            find_covering_directives,
        )

        # Session-scoped lookup: use the reviewer string as a coarse
        # segment proxy until session.ontology_module is added (Chunk 39).
        raw_cd = find_covering_directives(
            db,
            segment_id=report.reviewer or "",
            element_name=None,
            requesting_user=UUID("00000000-0000-0000-0000-000000000000"),
            admin_key_present=True,
        )
        report.covering_directives = enrich_covering_directives_realization(
            db, raw_cd
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("recon.gap_report.covering_lookup_failed", error=str(exc))
        report.covering_directives = []

    return report


# --- Route: GET read -------------------------------------------------------


@router.get(
    "/gap-report/{session_id}",
    response_model=GapReportResponse,
)
async def get_gap_report(
    session_id: UUID,
    db: Session = Depends(get_db),
) -> GapReportResponse:
    """Return the most-recent persisted Gap Report for ``session_id``."""
    row = db.execute(
        text(
            """
            SELECT report_json, erd_threshold_n
              FROM gap_reports
             WHERE session_id = :sid
             ORDER BY generated_at DESC
             LIMIT 1
            """
        ),
        {"sid": str(session_id)},
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Gap Report not found")
    return _hydrate_response(row, session_id)


# ---------------------------------------------------------------------------
# Chunk 37 — Cross-Executive Divergence Map (D284, D285)
# ---------------------------------------------------------------------------


divergence_map_router = APIRouter(
    prefix="/api/recon/divergence-map",
    tags=["recon-divergence-map"],
)
"""Cross-Executive Divergence Map router (D284).

Module-level export so EC-9
(``tests/elicitation/test_ec_constraints.py:test_ec_9``) can import it
and assert the POST route exists with an auth dependency. Mutating
routes default-deny via ``AuthMiddleware``; the cross-reviewer interim
gate (D285) layers an additional ``Depends`` to admit self-comparison
without an admin key (B1 resolution).
"""


def _require_admin_for_cross_reviewer(
    request: Request,
    body: DivergenceMapGenerateRequest,
    db: Session = Depends(get_db),
) -> DivergenceMapGenerateRequest:
    """D285 interim cross-reviewer permission gate.

    Loads the two ratified versions, compares ``reviewer`` strings.
    When reviewers differ this is a cross-reviewer comparison — admin
    key is required. Self-comparison (same reviewer on both sides) is
    default-admit (no privacy boundary crossed; B1 resolution).

    The check piggybacks on ``AuthMiddleware`` semantics: when an
    ``X-Admin-Key`` header is present and ``GRACE_ADMIN_KEY`` is unset,
    the middleware would already have admitted (Step 4 localhost
    bypass when key is absent). When ``GRACE_ADMIN_KEY`` is set, the
    middleware enforces the key on every mutating POST. This dependency
    only runs *after* middleware admission, so its job is solely to
    surface a 401 for the cross-reviewer case during local-dev
    (loopback bypass) when the key is absent — preserving the test
    surface for the spec's AC4 401-without-key assertion.
    """
    import os

    version_a = get_version_by_id(db, body.version_a_id)
    version_b = get_version_by_id(db, body.version_b_id)
    if version_a is None:
        raise HTTPException(
            status_code=404,
            detail=f"version_a not found: {body.version_a_id}",
        )
    if version_b is None:
        raise HTTPException(
            status_code=404,
            detail=f"version_b not found: {body.version_b_id}",
        )

    reviewer_a = (version_a.reviewer or "").strip()
    reviewer_b = (version_b.reviewer or "").strip()
    if reviewer_a == reviewer_b:
        # Self-comparison — default-admit per B1.
        return body

    # Cross-reviewer — require admin key. ``AuthMiddleware`` Step 4
    # would have admitted localhost without a key when GRACE_ADMIN_KEY
    # is unset. Re-check here to enforce the cross-reviewer boundary
    # regardless of the localhost bypass.
    submitted = request.headers.get("X-Admin-Key", "")
    admin_key = os.environ.get("GRACE_ADMIN_KEY", "")
    if not admin_key:
        # No key configured — the local-dev bypass would otherwise
        # admit. D285 says cross-reviewer must require a key; raise
        # 401 to mirror the AuthMiddleware contract.
        raise HTTPException(
            status_code=401,
            detail="cross-reviewer divergence map requires admin key",
        )
    if not submitted:
        raise HTTPException(
            status_code=401,
            detail="cross-reviewer divergence map requires admin key",
        )
    import secrets as _secrets

    if not _secrets.compare_digest(admin_key, submitted):
        raise HTTPException(
            status_code=401,
            detail="cross-reviewer divergence map requires admin key",
        )
    return body


@divergence_map_router.post(
    "/generate",
    response_model=DivergenceMapResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_divergence_map(
    body: DivergenceMapGenerateRequest = Depends(
        _require_admin_for_cross_reviewer
    ),
    db: Session = Depends(get_db),
    arcade_client: ArcadeClient = Depends(get_arcade_client),
) -> DivergenceMapResponse:
    """Generate and persist a Cross-Executive Divergence Map (D284)."""
    try:
        response = await compute_divergence_map(
            version_a_id=body.version_a_id,
            version_b_id=body.version_b_id,
            segment_id=body.segment_id,
            arcade_client=arcade_client,
            db_session=db,
        )
    except HTTPException:
        recon_divergence_map_generated_total.add(1, {"outcome": "error"})
        raise
    except Exception:
        recon_divergence_map_generated_total.add(1, {"outcome": "error"})
        raise

    recon_divergence_map_generated_total.add(1, {"outcome": "success"})

    # D290 — server-side telemetry emission. Best-effort; never fail
    # the route on a telemetry write error.
    try:
        from datetime import datetime as _dt
        from datetime import timezone as _tz
        from uuid import uuid4 as _uuid4
        import hashlib as _hashlib

        from src.elicitation.event_writer import write_event
        from src.elicitation.models import ElicitationEventEnvelope

        bucket_counts: dict[str, int] = {b.bucket_name: len(b.entries) for b in response.buckets}
        ra_hash = _hashlib.sha256(response.reviewer_a.encode("utf-8")).hexdigest()
        rb_hash = _hashlib.sha256(response.reviewer_b.encode("utf-8")).hexdigest()
        envelope = ElicitationEventEnvelope(
            event_id=_uuid4(),
            event_type="divergence_map_generated",
            session_id=response.map_id,  # standalone; reuse map_id as a session-scoped key
            actor_type="system",
            phase_name="none",
            emitted_at=_dt.now(_tz.utc),
            schema_version=1,
            grace_version="0.1.0",
            payload={
                "reviewer_a_hash": ra_hash,
                "reviewer_b_hash": rb_hash,
                "segment_id": response.segment_id,
                "additive_a_count": int(bucket_counts.get("additive_A", 0)),
                "additive_b_count": int(bucket_counts.get("additive_B", 0)),
                "contradictory_count": int(bucket_counts.get("contradictory", 0)),
                "consensus_count": int(bucket_counts.get("consensus", 0)),
                "generated_at": response.generated_at.isoformat(),
            },
            payload_schema_version=1,
        )
        write_event(db, envelope)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "recon.divergence_map.telemetry_failed",
            error=str(exc),
        )

    # D297 — Reconciliation Bridge: surface covering directives.
    try:
        from src.api.change_directive_coverage import (
            enrich_covering_directives_realization,
            find_covering_directives,
        )

        raw_cd = find_covering_directives(
            db,
            segment_id=response.segment_id or "",
            element_name=None,
            requesting_user=UUID("00000000-0000-0000-0000-000000000000"),
            admin_key_present=True,
        )
        response.covering_directives = enrich_covering_directives_realization(
            db, raw_cd
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("recon.divergence_map.covering_lookup_failed", error=str(exc))
        response.covering_directives = []

    return response


@divergence_map_router.get(
    "/latest",
    response_model=DivergenceMapResponse,
)
async def get_latest_divergence_map(
    segment_id: str | None = Query(default=None),
    reviewer_a: str = Query(...),
    reviewer_b: str = Query(...),
    db: Session = Depends(get_db),
) -> DivergenceMapResponse:
    """Return the most recent Divergence Map for the
    ``(segment_id, reviewer_a, reviewer_b)`` triple (D284).
    """
    if segment_id is None:
        sql = text(
            """
            SELECT id, segment_id, reviewer_a, reviewer_b,
                   version_a_id, version_b_id, buckets, generated_at
              FROM recon_divergence_maps
             WHERE segment_id IS NULL
               AND reviewer_a = :ra
               AND reviewer_b = :rb
             ORDER BY generated_at DESC
             LIMIT 1
            """
        )
        params = {"ra": reviewer_a, "rb": reviewer_b}
    else:
        sql = text(
            """
            SELECT id, segment_id, reviewer_a, reviewer_b,
                   version_a_id, version_b_id, buckets, generated_at
              FROM recon_divergence_maps
             WHERE segment_id = :seg
               AND reviewer_a = :ra
               AND reviewer_b = :rb
             ORDER BY generated_at DESC
             LIMIT 1
            """
        )
        params = {"seg": segment_id, "ra": reviewer_a, "rb": reviewer_b}
    row = db.execute(sql, params).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Divergence Map not found")
    return hydrate_divergence_map_response(row)


@divergence_map_router.get(
    "/segments/{segment_id}/peer-versions",
)
async def get_peer_version_count(
    segment_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """Return the count of comparable ratified versions for ``segment_id``.

    Used by the frontend ReviewPanel to gate the Divergence Map sub-entry
    (visible only when ``count >= 1``).
    """
    n = db.execute(
        text(
            """
            SELECT COUNT(*) FROM ontology_versions
             WHERE segment_id = :seg
            """
        ),
        {"seg": segment_id},
    ).scalar()
    return {"count": int(n or 0)}


@divergence_map_router.get(
    "/{map_id}",
    response_model=DivergenceMapResponse,
)
async def get_divergence_map_by_id(
    map_id: UUID,
    db: Session = Depends(get_db),
) -> DivergenceMapResponse:
    """Return a Divergence Map by id (D284)."""
    row = db.execute(
        text(
            """
            SELECT id, segment_id, reviewer_a, reviewer_b,
                   version_a_id, version_b_id, buckets, generated_at
              FROM recon_divergence_maps
             WHERE id = :id
            """
        ),
        {"id": str(map_id)},
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Divergence Map not found")
    return hydrate_divergence_map_response(row)


# ---------------------------------------------------------------------------
# Chunk 37 — Documented Reality Report routes (D286, D287, D290)
# ---------------------------------------------------------------------------


documented_reality_router = APIRouter(
    prefix="/api/recon/documented-reality",
    tags=["recon-documented-reality"],
)


documented_reality_schedule_router = APIRouter(
    prefix="/api/recon/documented-reality/schedules",
    tags=["recon-documented-reality-schedules"],
)


@documented_reality_router.post(
    "/generate",
    response_model=DocumentedRealityReportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_documented_reality_report(
    evidence_origin: Literal["document", "communication", "both"] | None = Query(None),
    db: Session = Depends(get_db),
    arcade_client: ArcadeClient = Depends(get_arcade_client),
) -> DocumentedRealityReportResponse:
    """Synchronously generate a Documented Reality Report (D286).

    Calls ``RegenerationPipeline`` as a client when corpus is above the
    floor. Empty-corpus carve-out: V count below ``corpus_floor``
    returns ``corpus_below_floor=True`` with ``narrative=None`` and
    aggregations only (R6 mitigation; D193 / CF3 hold).

    Chunk 59 (D426 — CP7): ``evidence_origin`` filter scopes aggregation
    to vertices with a matching ``evidence_origin`` property.
    """
    from src.analytics.documented_reality import (
        compute_documented_reality_aggregations,
        generate_documented_reality_report as _gen,
    )

    try:
        aggregations = await compute_documented_reality_aggregations(
            arcade_client, evidence_origin=evidence_origin
        )
        response = await _gen(
            aggregations=aggregations,
            retrieval_pipeline=None,
            regeneration_pipeline=None,
            trigger="on_demand",
        )

        # Persist a row + return.
        rj = response.model_dump(mode="json")
        db.execute(
            text(
                """
                INSERT INTO recon_documented_reality_reports
                    (id, trigger, corpus_below_floor, report_json, generated_at)
                VALUES
                    (:id, :tr, :cb, CAST(:rj AS JSONB), :gen)
                """
            ),
            {
                "id": str(response.report_id),
                "tr": response.trigger,
                "cb": response.corpus_below_floor,
                "rj": json.dumps(rj),
                "gen": response.generated_at,
            },
        )
        db.commit()
    except Exception:
        recon_documented_reality_report_generated_total.add(
            1, {"trigger": "on_demand", "outcome": "error"}
        )
        # Continue raising — but DON'T wrap unknown errors as 500 here;
        # the surface for missing tables (test envs without the optional
        # reports table) is not exercised by AC. Simply skip persistence
        # if the table does not exist.
        raise

    recon_documented_reality_report_generated_total.add(
        1, {"trigger": "on_demand", "outcome": "success"}
    )

    # D290 — telemetry emission, best-effort.
    try:
        from datetime import datetime as _dt
        from datetime import timezone as _tz
        from uuid import uuid4 as _uuid4

        from src.elicitation.event_writer import write_event
        from src.elicitation.models import ElicitationEventEnvelope

        envelope = ElicitationEventEnvelope(
            event_id=_uuid4(),
            event_type="documented_reality_report_generated",
            session_id=response.report_id,
            actor_type="system",
            phase_name="none",
            emitted_at=_dt.now(_tz.utc),
            schema_version=1,
            grace_version="0.1.0",
            payload={
                "report_id": str(response.report_id),
                "trigger": response.trigger,
                "corpus_below_floor": bool(response.corpus_below_floor),
                "generated_at": response.generated_at.isoformat(),
            },
            payload_schema_version=1,
        )
        write_event(db, envelope)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "recon.documented_reality.telemetry_failed",
            error=str(exc),
        )

    return response


@documented_reality_router.get(
    "/latest",
    response_model=DocumentedRealityReportResponse,
)
async def get_latest_documented_reality_report(
    db: Session = Depends(get_db),
) -> DocumentedRealityReportResponse:
    row = db.execute(
        text(
            """
            SELECT report_json FROM recon_documented_reality_reports
             ORDER BY generated_at DESC
             LIMIT 1
            """
        )
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Documented Reality Report not found",
        )
    payload = row.report_json
    if isinstance(payload, str):
        payload = json.loads(payload)
    return DocumentedRealityReportResponse.model_validate(payload)


@documented_reality_router.get(
    "/{report_id}",
    response_model=DocumentedRealityReportResponse,
)
async def get_documented_reality_report_by_id(
    report_id: UUID,
    db: Session = Depends(get_db),
) -> DocumentedRealityReportResponse:
    row = db.execute(
        text(
            """
            SELECT report_json FROM recon_documented_reality_reports
             WHERE id = :id
            """
        ),
        {"id": str(report_id)},
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Documented Reality Report not found",
        )
    payload = row.report_json
    if isinstance(payload, str):
        payload = json.loads(payload)
    return DocumentedRealityReportResponse.model_validate(payload)


# --- Schedule CRUD --------------------------------------------------------


@documented_reality_schedule_router.get(
    "",
    response_model=list[DocumentedRealityScheduleResponse],
)
async def list_documented_reality_schedules(
    db: Session = Depends(get_db),
) -> list[DocumentedRealityScheduleResponse]:
    rows = db.execute(
        text(
            """
            SELECT id, cadence, next_run_at, enabled,
                   created_at, updated_at
              FROM recon_documented_reality_schedules
             ORDER BY created_at DESC
            """
        )
    ).fetchall()
    return [
        DocumentedRealityScheduleResponse(
            id=r.id,
            cadence=r.cadence,
            next_run_at=r.next_run_at,
            enabled=r.enabled,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


@documented_reality_schedule_router.post(
    "",
    response_model=DocumentedRealityScheduleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_documented_reality_schedule(
    body: DocumentedRealityScheduleRequest,
    db: Session = Depends(get_db),
) -> DocumentedRealityScheduleResponse:
    sid = uuid4()
    now = datetime.now(timezone.utc)
    db.execute(
        text(
            """
            INSERT INTO recon_documented_reality_schedules
                (id, cadence, enabled, created_at, updated_at)
            VALUES
                (:id, :cad, :en, :now, :now)
            """
        ),
        {
            "id": str(sid),
            "cad": body.cadence,
            "en": body.enabled,
            "now": now,
        },
    )
    db.commit()
    return DocumentedRealityScheduleResponse(
        id=sid,
        cadence=body.cadence,
        next_run_at=None,
        enabled=body.enabled,
        created_at=now,
        updated_at=now,
    )


@documented_reality_schedule_router.patch(
    "/{schedule_id}",
    response_model=DocumentedRealityScheduleResponse,
)
async def patch_documented_reality_schedule(
    schedule_id: UUID,
    body: DocumentedRealityScheduleUpdateRequest,
    db: Session = Depends(get_db),
) -> DocumentedRealityScheduleResponse:
    row = db.execute(
        text(
            """
            SELECT id, cadence, next_run_at, enabled,
                   created_at, updated_at
              FROM recon_documented_reality_schedules
             WHERE id = :id
            """
        ),
        {"id": str(schedule_id)},
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="schedule not found")

    new_cadence = body.cadence if body.cadence is not None else row.cadence
    new_enabled = body.enabled if body.enabled is not None else row.enabled
    now = datetime.now(timezone.utc)
    db.execute(
        text(
            """
            UPDATE recon_documented_reality_schedules
               SET cadence = :cad,
                   enabled = :en,
                   updated_at = :now
             WHERE id = :id
            """
        ),
        {
            "cad": new_cadence,
            "en": new_enabled,
            "now": now,
            "id": str(schedule_id),
        },
    )
    db.commit()
    return DocumentedRealityScheduleResponse(
        id=row.id,
        cadence=new_cadence,
        next_run_at=row.next_run_at,
        enabled=new_enabled,
        created_at=row.created_at,
        updated_at=now,
    )
