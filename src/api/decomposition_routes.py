"""Decomposition module API surface (Chunk 41, D328).

Ten route groups (eleven concrete endpoints) under
``/api/decomposition``. Heavy work (Layers 1–4) stays CLI-only per
D246/D315: the trigger route spawns the existing CLI subprocess and
returns 202 + ``run_id``. Layers 5–7 are interactive sub-second
mutations that flow through the existing ``AuthMiddleware``.

The router does **not** import
``src.decomposition.pipeline.orchestrator``; CI guard
``tests/decomposition/test_pipeline_invocation_surface.py`` enforces
this at static-string level.

The Layer 6 sample-CQ POST is added to ``READONLY_ROUTES`` (D237) —
it performs only an LLM call + transient response with no DB writes.

Auth posture summary:

* GET routes: handled by the AuthMiddleware GET/HEAD/OPTIONS exemption.
* ``POST .../runs/trigger``, ``layer5/decision``, ``rerun``,
  ``layer6/validation``, ``segmentation-map/ratify``: mutators →
  AuthMiddleware admin-key (loopback bypass when ``GRACE_ADMIN_KEY``
  is unset; X-Admin-Key required otherwise).
* ``POST .../layer6/sample-cqs``: read-only POST → D237 allowlist.

Telemetry: emits four EventType envelopes (D330) and increments
three counters via ``src.analytics.metrics``.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from src.decomposition import (
    layer5_decision as _layer5_decision_mod,
    layer6_sample_cq as _layer6_sample_cq_mod,
    rerun_repository as _rerun_repository,
    run_repository as _run_repository,
    segmentation_map_repository as _seg_map_repository,
)
from src.decomposition.models import ProposedSegment
from src.decomposition.rerun_repository import (
    RERUN_HARD_CAP,
    RerunCapExceededError,
)
from src.decomposition.run_repository import ArchiveDriftError
from src.decomposition.segmentation_map_models import (
    Layer5DecisionPayload,
    Layer6ValidationPayload,
    SegmentationMap,
)
from src.shared.database import get_db


logger = structlog.get_logger()


router = APIRouter(prefix="/api/decomposition", tags=["decomposition"])


# Optional metric emitters — wired in CP10. Imported lazily / defensively
# so route registration does not fail if the counters are not yet present
# (paired-PR ordering during the chunk build).
try:  # pragma: no cover — exercised in CP10 telemetry tests
    from src.analytics.metrics import (
        record_decomposition_layer5_decision,
        record_decomposition_rerun,
        record_decomposition_segmentation_map_ratified,
    )
except Exception:  # noqa: BLE001
    def record_decomposition_layer5_decision(*_a, **_kw) -> None:  # type: ignore[misc]
        return None

    def record_decomposition_rerun(*_a, **_kw) -> None:  # type: ignore[misc]
        return None

    def record_decomposition_segmentation_map_ratified(*_a, **_kw) -> None:  # type: ignore[misc]
        return None


# Optional elicitation telemetry — degrades to no-op until CP10 lands.
def _emit_elicitation_event(event_type: str, payload: dict[str, Any]) -> None:
    """Best-effort telemetry append; never raises."""
    try:  # pragma: no cover — exercised in CP10 / telemetry tests
        from src.elicitation.bridge import enqueue_event  # type: ignore

        enqueue_event(event_type=event_type, payload=payload)
    except Exception:  # noqa: BLE001
        logger.debug("decomposition.telemetry.skipped", event=event_type)


# ---------- Request / response models ----------


class TriggerRequest(BaseModel):
    """Body for ``POST /api/decomposition/runs/trigger``."""

    model_config = ConfigDict(extra="forbid")

    archive_root: str = Field(min_length=1)
    operator: UUID | None = None
    limit: int | None = Field(default=None, ge=1)


class TriggerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    archive_root: str
    archive_root_canonical_hash: str
    pid: int | None = None


class RerunRequest(BaseModel):
    """Body for ``POST /api/decomposition/runs/{run_id}/rerun``."""

    model_config = ConfigDict(extra="forbid")

    direction: str = Field(pattern=r"^(finer|coarser)$")


class SampleCQRequest(BaseModel):
    """Body for the Layer 6 sample-CQ POST."""

    model_config = ConfigDict(extra="forbid")

    segment_name: str = Field(min_length=1)
    document_excerpts: list[str] = Field(default_factory=list)
    n: int | None = Field(default=None, ge=1, le=50)


# ---------- Helpers ----------


_VALID_RERUN_DIRECTIONS = frozenset({"finer", "coarser"})


def _serialize_run_row(row: dict | None) -> dict | None:
    """JSON-safe a run row: stringify UUIDs/datetimes."""
    if row is None:
        return None
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, UUID):
            out[k] = str(v)
        elif isinstance(v, datetime):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def _list_runs(
    session: Session,
    *,
    limit: int,
    cursor: str | None,
) -> tuple[list[dict], str | None]:
    """List runs newest-first via started_at DESC, run_id DESC tiebreak."""
    sql_pieces = [
        "SELECT run_id, archive_root, archive_root_canonical_hash,",
        "       started_at, completed_at, status, total_documents,",
        "       operator, resumed_from_run_id, created_at",
        "FROM decomposition_runs",
    ]
    params: dict[str, Any] = {"limit": limit + 1}
    if cursor:
        try:
            cursor_started, cursor_run_id = cursor.split("|", 1)
            params["cursor_started"] = cursor_started
            params["cursor_run_id"] = UUID(cursor_run_id)
            sql_pieces.append(
                "WHERE (started_at, run_id) < "
                "(CAST(:cursor_started AS TIMESTAMPTZ), :cursor_run_id)"
            )
        except (ValueError, AttributeError):
            raise HTTPException(status_code=422, detail="Invalid cursor")
    sql_pieces.append("ORDER BY started_at DESC, run_id DESC LIMIT :limit")

    rows = session.execute(text(" ".join(sql_pieces)), params).all()
    rows_dicts = [_serialize_run_row(dict(r._mapping)) for r in rows]
    next_cursor: str | None = None
    if len(rows_dicts) > limit:
        last_visible = rows_dicts[limit - 1]
        next_cursor = f"{last_visible['started_at']}|{last_visible['run_id']}"
        rows_dicts = rows_dicts[:limit]
    return rows_dicts, next_cursor


def _get_or_404(session: Session, run_id: UUID) -> dict:
    row = _run_repository.get_run(session, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return row


def _running_run_for_archive_hash(
    session: Session, canonical_hash: str
) -> dict | None:
    """Return the most recent non-terminal run for this archive hash."""
    sql = text(
        """
        SELECT run_id, archive_root, archive_root_canonical_hash,
               started_at, status
        FROM decomposition_runs
        WHERE archive_root_canonical_hash = :h
          AND status IN ('running', 'paused_pre_layer4',
                         'paused_pre_layer5', 'paused_pre_layer6',
                         'paused_pre_layer7')
        ORDER BY started_at DESC
        LIMIT 1
        """
    )
    row = session.execute(sql, {"h": canonical_hash}).one_or_none()
    return dict(row._mapping) if row is not None else None


def _resolve_proposed_segment(
    layer4_payload: dict | None, segment_name: str
) -> ProposedSegment | None:
    """Pull a ``ProposedSegment`` out of the persisted Layer 4 JSONB."""
    if not layer4_payload:
        return None
    hyps = layer4_payload.get("hypotheses") or []
    for hyp in hyps:
        for seg in hyp.get("segments") or []:
            if seg.get("name") == segment_name:
                try:
                    return ProposedSegment.model_validate(seg)
                except Exception:  # noqa: BLE001
                    return None
    return None


# ---------- 1. GET /runs ----------


@router.get("/runs")
async def list_runs(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    """List decomposition runs newest-first (D328)."""
    rows, next_cursor = _list_runs(db, limit=limit, cursor=cursor)
    return {"runs": rows, "next_cursor": next_cursor}


# ---------- 2. GET /runs/{run_id} ----------


@router.get("/runs/{run_id}")
async def get_run_detail(
    run_id: UUID, db: Session = Depends(get_db)
) -> dict:
    row = _get_or_404(db, run_id)
    return _serialize_run_row(row) or {}


# ---------- 3. GET /runs/{run_id}/layer4/hypotheses ----------


@router.get("/runs/{run_id}/layer4/hypotheses")
async def get_layer4_hypotheses(
    run_id: UUID, db: Session = Depends(get_db)
) -> dict:
    row = _get_or_404(db, run_id)
    layer4 = row.get("layer4_hypotheses")
    if not layer4:
        raise HTTPException(
            status_code=404, detail="Layer 4 not yet recorded for this run"
        )
    return {"run_id": str(run_id), "layer4_hypotheses": layer4}


# ---------- 4. POST /runs/trigger ----------


# F-030 / ISS-0014: per-run logfile directory (mirrors the D475
# extraction-jobs pattern in extraction_routes.py).
_LOG_DIR = Path.home() / ".grace" / "logs"


def _build_decomposition_argv(
    archive_root: str,
    *,
    run_id: UUID,
    limit: int | None,
    rerun_direction: str | None = None,
) -> list[str]:
    """Build the CLI argv for the trigger spawn (D476 contract-testable).

    F-030 / ISS-0014: ``--run-id`` carries the placeholder row id so the
    CLI adopts and UPDATEs the row the 202 response returned (D460
    API-INSERT-first pattern) instead of INSERTing a second, divergent
    run row that leaves the placeholder ``running`` forever.

    ISS-0024: ``--rerun-direction`` carries the ±1.5× resolution intent for
    rerun successors — the direction is not persisted on the append-only
    row, so the spawn argv is its transport into the pipeline.
    """
    cmd: list[str] = [
        sys.executable,
        "-m",
        "src.decomposition.pipeline",
        "run",
        "--archive-root",
        archive_root,
        "--run-id",
        str(run_id),
    ]
    if limit is not None:
        cmd.extend(["--limit", str(limit)])
    if rerun_direction is not None:
        cmd.extend(["--rerun-direction", rerun_direction])
    return cmd


def _spawn_decomposition_cli(
    archive_root: str,
    *,
    run_id: UUID,
    limit: int | None,
    rerun_direction: str | None = None,
) -> int:
    """``subprocess.Popen`` the CLI; return the child PID.

    ``start_new_session=True`` detaches the child from the FastAPI
    process group so a uvicorn reload does not signal the running
    pipeline (D246/D315 mirror).
    """
    cmd = _build_decomposition_argv(
        archive_root, run_id=run_id, limit=limit, rerun_direction=rerun_direction
    )
    # F-030 / ISS-0014: stderr was previously DEVNULLed, which made child
    # crashes invisible (the placeholder row just sat 'running'). Capture
    # stderr to a per-run logfile instead (D475 extraction-jobs pattern).
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    logfile_path = _LOG_DIR / f"decomposition-run-{run_id}.log"
    logfile = open(logfile_path, "w")  # noqa: SIM115 — intentional long-lived fd
    proc = subprocess.Popen(  # noqa: S603 — known argv; not user-editable shell
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=logfile,
        start_new_session=True,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    return proc.pid


@router.post("/runs/trigger", status_code=status.HTTP_202_ACCEPTED)
async def trigger_run(
    body: TriggerRequest, db: Session = Depends(get_db)
) -> TriggerResponse:
    """Spawn the CLI and INSERT a placeholder ``decomposition_runs`` row.

    The CLI is the long-running owner of the Layer 1–4 work. To make the
    202 response immediately addressable we INSERT the row here, return
    ``run_id``, and pass ``--run-id`` to the CLI so it picks up the same
    row. Concurrent-trigger races: 409 if a non-terminal run already
    exists for the same canonical hash.
    """
    archive_path = Path(body.archive_root)
    if not archive_path.exists() or not archive_path.is_dir():
        raise HTTPException(
            status_code=422, detail="archive_root does not resolve to a directory"
        )

    canonical_hash = _run_repository._canonical_hash(body.archive_root)
    in_progress = _running_run_for_archive_hash(db, canonical_hash)
    if in_progress is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "run already in progress for this archive",
                "run_id": str(in_progress["run_id"]),
                "status": in_progress["status"],
            },
        )

    row = _run_repository.create_run(
        db,
        archive_root=str(archive_path),
        operator=body.operator,
        canonical_hash=canonical_hash,
    )
    db.commit()

    pid: int | None
    try:
        # F-030 / ISS-0014: pass the placeholder run_id so the CLI adopts
        # this exact row — the id the operator polls IS the executing run.
        pid = _spawn_decomposition_cli(
            str(archive_path), run_id=row["run_id"], limit=body.limit
        )
    except (OSError, FileNotFoundError) as exc:
        logger.warning("decomposition.cli.spawn_failed", error=str(exc))
        pid = None

    return TriggerResponse(
        run_id=row["run_id"],
        archive_root=row["archive_root"],
        archive_root_canonical_hash=row["archive_root_canonical_hash"],
        pid=pid,
    )


# ---------- 5. POST /runs/{run_id}/layer5/decision ----------


@router.post("/runs/{run_id}/layer5/decision")
async def record_layer5(
    run_id: UUID,
    payload: Layer5DecisionPayload,
    db: Session = Depends(get_db),
) -> dict:
    _ = _get_or_404(db, run_id)
    try:
        out = _layer5_decision_mod.record_layer5_decision(
            db, run_id=run_id, payload=payload
        )
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        # First-write-only trigger raises; surface as 409.
        if "decomposition_runs_append_only" in str(exc) or "check_violation" in str(exc):
            raise HTTPException(
                status_code=409,
                detail="Layer 5 decision already recorded for this run",
            )
        raise

    record_decomposition_layer5_decision(decision_kind=payload.decision_kind)
    _emit_elicitation_event(
        "decomposition_layer5_decision_recorded",
        {
            "run_id": str(run_id),
            "decision_kind": payload.decision_kind,
            "modifications_count": len(payload.modifications),
            "rationale_length": len(payload.rationale or ""),
        },
    )
    return _serialize_run_row(out) or {}


# ---------- 6. POST /runs/{run_id}/rerun ----------


@router.post("/runs/{run_id}/rerun", status_code=status.HTTP_201_CREATED)
async def trigger_rerun(
    run_id: UUID,
    body: RerunRequest,
    db: Session = Depends(get_db),
) -> dict:
    _ = _get_or_404(db, run_id)
    try:
        new_row = _rerun_repository.create_rerun_run(
            db, predecessor_run_id=run_id, direction=body.direction
        )
        db.commit()
    except RerunCapExceededError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "error": "rerun cap exceeded",
                "hard_cap": RERUN_HARD_CAP,
                "message": str(exc),
            },
        )
    except ArchiveDriftError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))

    record_decomposition_rerun(direction=body.direction)
    _emit_elicitation_event(
        "decomposition_rerun_triggered",
        {
            "run_id": str(new_row["run_id"]),
            "predecessor_run_id": str(run_id),
            "direction": body.direction,
            "lineage_depth": new_row.get("lineage_depth"),
        },
    )

    # ISS-0024: this route previously INSERTed the successor row and
    # returned — nothing ever executed it, so it sat 'running' forever
    # (the exact stuck-placeholder shape F-030/ISS-0014 fixed for
    # trigger/resume). Spawn the CLI against the successor row; the
    # --rerun-direction flag carries the ±1.5x resolution intent.
    pid: int | None
    try:
        pid = _spawn_decomposition_cli(
            new_row["archive_root"],
            run_id=new_row["run_id"],
            limit=None,
            rerun_direction=body.direction,
        )
    except (OSError, FileNotFoundError) as exc:
        logger.warning("decomposition.rerun.spawn_failed", error=str(exc))
        pid = None

    out = _serialize_run_row(new_row) or {}
    out["pid"] = pid
    return out


# ---------- 7. POST /runs/{run_id}/layer6/sample-cqs ----------


@router.post("/runs/{run_id}/layer6/sample-cqs")
async def generate_layer6_sample_cqs(
    run_id: UUID,
    body: SampleCQRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Generate transient sample CQs (read-only POST → D237 allowlist).

    Synchronous; ~10–30s. No DB writes — the CQs live in the
    response body only. Sample CQs are persisted separately by the
    Layer 6 validation route (which writes ``layer6_validation``
    JSONB).
    """
    row = _get_or_404(db, run_id)
    seg = _resolve_proposed_segment(row.get("layer4_hypotheses"), body.segment_name)
    if seg is None:
        raise HTTPException(
            status_code=404,
            detail=f"Segment {body.segment_name!r} not found in Layer 4 hypotheses",
        )
    cqs = await _layer6_sample_cq_mod.generate_sample_cqs(
        seg, document_excerpts=body.document_excerpts, n=body.n
    )
    return {"cqs": [c.model_dump(mode="json") for c in cqs]}


# ---------- 8. POST /runs/{run_id}/layer6/validation ----------


@router.post("/runs/{run_id}/layer6/validation")
async def record_layer6_validation(
    run_id: UUID,
    payload: Layer6ValidationPayload,
    db: Session = Depends(get_db),
) -> dict:
    _ = _get_or_404(db, run_id)
    try:
        out = _run_repository.update_layer6_validation(db, run_id, payload)
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        if "decomposition_runs_append_only" in str(exc) or "check_violation" in str(exc):
            raise HTTPException(
                status_code=409,
                detail="Layer 6 validation already recorded for this run",
            )
        raise

    approved = sum(s.approved_count for s in payload.segments)
    rejected = sum(s.rejected_count for s in payload.segments)
    _emit_elicitation_event(
        "decomposition_layer6_validation_recorded",
        {
            "run_id": str(run_id),
            "segment_count": len(payload.segments),
            "approved_count": approved,
            "rejected_count": rejected,
        },
    )
    return _serialize_run_row(out) or {}


# ---------- 9. POST /runs/{run_id}/segmentation-map/ratify ----------


@router.post(
    "/runs/{run_id}/segmentation-map/ratify",
    status_code=status.HTTP_201_CREATED,
)
async def ratify_segmentation_map(
    run_id: UUID,
    sm: SegmentationMap,
    db: Session = Depends(get_db),
) -> dict:
    _ = _get_or_404(db, run_id)
    if sm.decomposition_run_id != run_id:
        raise HTTPException(
            status_code=422,
            detail="SegmentationMap.decomposition_run_id must match path run_id",
        )
    try:
        row = _seg_map_repository.create_map(db, sm=sm)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc.orig))

    record_decomposition_segmentation_map_ratified(
        null_hypothesis_accepted=sm.null_hypothesis_accepted
    )
    _emit_elicitation_event(
        "segmentation_map_ratified",
        {
            "run_id": str(run_id),
            "map_id": str(row["segmentation_map_id"]),
            "payload_hash": row["payload_hash"],
            "previous_hash": row.get("previous_hash"),
            "null_hypothesis_accepted": sm.null_hypothesis_accepted,
        },
    )
    return {
        "segmentation_map_id": str(row["segmentation_map_id"]),
        "payload_hash": row["payload_hash"],
        "previous_hash": row.get("previous_hash"),
    }


# ---------- 10. GET /runs/{run_id}/segmentation-maps[/{map_id}] ----------


def _yaml_render(payload: dict) -> str:
    """Render a SegmentationMap payload to YAML.

    Uses ``pydantic_yaml.to_yaml_str`` when available for stable
    round-trip; falls back to ``yaml.safe_dump`` otherwise.
    """
    try:  # pragma: no cover — exercised in CP9 frontend integration
        from pydantic_yaml import to_yaml_str

        sm = SegmentationMap.model_validate(payload)
        return to_yaml_str(sm)
    except Exception:  # noqa: BLE001
        import yaml as _yaml

        return _yaml.safe_dump(payload, sort_keys=False, default_flow_style=False)


def _wants_yaml(accept: str | None) -> bool:
    if not accept:
        return False
    parts = [p.strip().lower() for p in accept.split(",")]
    return any(p.startswith("application/yaml") or p.startswith("text/yaml") for p in parts)


@router.get("/runs/{run_id}/segmentation-maps")
async def list_segmentation_maps(
    run_id: UUID,
    accept: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Response:
    _ = _get_or_404(db, run_id)
    rows = _seg_map_repository.chain_for_run(db, run_id)
    serialized = [
        {
            "segmentation_map_id": str(r["segmentation_map_id"]),
            "decomposition_run_id": str(r["decomposition_run_id"]),
            "schema_version": r["schema_version"],
            "payload_hash": r["payload_hash"],
            "previous_hash": r.get("previous_hash"),
            "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            "null_hypothesis_accepted": r.get("null_hypothesis_accepted"),
            "payload": r.get("payload"),
        }
        for r in rows
    ]
    if _wants_yaml(accept):
        import yaml as _yaml

        body_yaml = _yaml.safe_dump(
            {"maps": serialized}, sort_keys=False, default_flow_style=False
        )
        return Response(content=body_yaml, media_type="application/yaml")
    return JSONResponse(content={"maps": serialized})


@router.get("/runs/{run_id}/segmentation-maps/{map_id}")
async def get_segmentation_map(
    run_id: UUID,
    map_id: UUID,
    accept: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Response:
    _ = _get_or_404(db, run_id)
    row = _seg_map_repository.get_map_by_id(db, map_id)
    if row is None or row["decomposition_run_id"] != run_id:
        raise HTTPException(status_code=404, detail="Segmentation map not found")
    serialized = {
        "segmentation_map_id": str(row["segmentation_map_id"]),
        "decomposition_run_id": str(row["decomposition_run_id"]),
        "schema_version": row["schema_version"],
        "payload_hash": row["payload_hash"],
        "previous_hash": row.get("previous_hash"),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "null_hypothesis_accepted": row.get("null_hypothesis_accepted"),
        "payload": row.get("payload"),
    }
    if _wants_yaml(accept):
        body_yaml = _yaml_render(row.get("payload") or {})
        return Response(content=body_yaml, media_type="application/yaml")
    return JSONResponse(content=serialized)
