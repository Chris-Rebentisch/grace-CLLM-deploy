"""Permission Matrix API surface (Chunk 42, CP8, D328 mirror).

Ten route groups under ``/api/permissions``. Heavy work
(hypothesis generation + drift detection) stays CLI-only per D246
mirror: the two trigger routes spawn ``src.permissions.cli`` via
``subprocess.Popen`` and return 202 + ``run_id`` / ``job_id``.

The router does NOT import
``src.permissions.hypothesis_generator`` or
``src.permissions.drift_detector``; CI guard
``tests/api/test_permissions_routes_invocation_surface.py`` enforces
this at static-string level.

The two read-only POSTs (hypothesis generate + drift run) are added
to ``READONLY_ROUTES`` (D237 allowlist extension) — they trigger
out-of-process work and persist a placeholder run row but do not
otherwise mutate state from the request thread's perspective.

Auth posture summary:

* GET routes: handled by the AuthMiddleware GET/HEAD/OPTIONS exemption.
* ``POST .../matrix/hypothesis/generate``, ``POST .../drift/run``:
  read-only POST → D237 allowlist (loopback bypass / admin-key
  optional).
* ``POST .../matrix/ratify``: mutator → AuthMiddleware admin-key
  (loopback bypass when ``GRACE_ADMIN_KEY`` is unset; X-Admin-Key
  required otherwise).

Telemetry: emits four EventType envelopes (CP10 wires the names in
``src/elicitation/models.py``) and increments three counters via
``src.analytics.metrics`` (CP10 wires the registrations).
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from src.support.refused_routes import no_support_session
from src.permissions import repository as _matrix_repo
from src.permissions.enforcer import get_enforcer, rebuild_enforcer
from src.permissions.models import (
    Allow,
    EvidenceBundle,
    PermissionMatrix,
    PermissionMatrixVersion,
)
from src.permissions.principal_context import from_admission_tree
from src.shared.database import get_db


logger = structlog.get_logger()


router = APIRouter(prefix="/api/permissions", tags=["permissions"])


# Optional metric emitters / telemetry — CP10 wires the names. Best-effort
# imports keep route registration green during paired-PR ordering.
try:  # pragma: no cover — exercised in CP10 telemetry tests
    from src.analytics.metrics import (
        record_permission_matrix_hypothesis,
        record_permission_matrix_ratification,
        record_permission_drift_auto_assignment,
    )
except Exception:  # noqa: BLE001
    def record_permission_matrix_hypothesis(*_a, **_kw) -> None:  # type: ignore[misc]
        return None

    def record_permission_matrix_ratification(*_a, **_kw) -> None:  # type: ignore[misc]
        return None

    def record_permission_drift_auto_assignment(*_a, **_kw) -> None:  # type: ignore[misc]
        return None


def _emit_elicitation_event(event_type: str, payload: dict[str, Any]) -> None:
    """Best-effort telemetry append; never raises."""
    try:  # pragma: no cover — exercised in CP10 telemetry tests
        from src.elicitation.bridge import enqueue_event  # type: ignore

        enqueue_event(event_type=event_type, payload=payload)
    except Exception:  # noqa: BLE001
        logger.debug("permissions.telemetry.skipped", event_type=event_type)


# ---------- Request / response models ----------


class HypothesisTriggerRequest(BaseModel):
    """Body for ``POST /api/permissions/matrix/hypothesis/generate``."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: UUID
    operator: str | None = None
    dry_run: bool = False


class HypothesisTriggerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    evidence_id: UUID
    pid: int | None = None


class RatifyRequest(BaseModel):
    """Body for ``POST /api/permissions/matrix/ratify``."""

    model_config = ConfigDict(extra="forbid")

    matrix: PermissionMatrix
    created_by: str | None = None
    version_label: str | None = None


class DriftRunRequest(BaseModel):
    """Body for ``POST /api/permissions/drift/run``."""

    model_config = ConfigDict(extra="forbid")

    observation_time: datetime | None = None
    dry_run: bool = False


class DriftRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    pid: int | None = None
    observation_time: datetime


# ---------- Helpers ----------


def _serialize_matrix_row(row: dict | None) -> dict | None:
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


def _spawn_permissions_cli(argv_tail: list[str]) -> int | None:
    """``subprocess.Popen`` the CLI; return the child PID or None on failure.

    ``start_new_session=True`` detaches the child from the FastAPI
    process group so a uvicorn reload does not signal the running job
    (D246 mirror).
    """
    # The argv_tail's first token names the subcommand: "hypothesis" for the
    # unified ``src.permissions.cli`` path (which supports --job-id via
    # hypothesis_generator), and "drift" for the direct
    # ``src.permissions.drift_detector`` module (which is the only path that
    # accepts --job-id for D460 API-INSERT-first row UPDATE). The unified
    # ``src.permissions.cli drift run`` subparser does NOT accept --job-id.
    # Fixing here keeps `_spawn_permissions_cli` as the single spawn site.
    # D478 (D356 capture-the-why, TESTING_LOG R5-H1): drift route must use
    # module path "src.permissions.drift_detector" (not src.permissions.cli
    # for the drift subcommand). The drift_detector module is the only path
    # that accepts --job-id for D460 API-INSERT-first row UPDATE.
    # Authorization: D478.
    if argv_tail and argv_tail[0] == "drift":
        module = "src.permissions.drift_detector"
        argv_tail = argv_tail[1:]  # drop the "drift" subcommand token
    else:
        module = "src.permissions.cli"
    cmd: list[str] = [
        sys.executable,
        "-m",
        module,
        *argv_tail,
    ]
    try:
        proc = subprocess.Popen(  # noqa: S603 — known argv; not user-editable shell
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            cwd=str(Path(__file__).resolve().parents[2]),
        )
        return proc.pid
    except (OSError, FileNotFoundError) as exc:
        logger.warning("permissions.cli.spawn_failed", error=str(exc))
        return None


def _insert_hypothesis_run_placeholder(
    session: Session,
    *,
    evidence_id: UUID,
    operator: str | None,
) -> dict:
    """INSERT an append-only placeholder ``permission_hypothesis_runs`` row.

    Returns the new row including ``run_id`` so the route can return
    202 immediately. The CLI subprocess will UPDATE the row with the
    final ``hypothesis_set`` payload via the first-write-only trigger
    (the trigger permits exactly one transition from NULL → non-NULL).
    """
    sql = text(
        """
        INSERT INTO permission_hypothesis_runs (
            evidence_id, status, operator
        ) VALUES (
            :evidence_id, 'running', :operator
        )
        RETURNING run_id, evidence_id, status, operator, created_at,
                  completed_at
        """
    )
    row = session.execute(
        sql,
        {"evidence_id": evidence_id, "operator": operator},
    ).one()
    return dict(row._mapping)


def _get_hypothesis_run(
    session: Session, run_id: UUID
) -> dict | None:
    sql = text(
        """
        SELECT run_id, evidence_id, status, hypothesis_set, operator,
               created_at, completed_at
        FROM permission_hypothesis_runs
        WHERE run_id = :run_id
        """
    )
    row = session.execute(sql, {"run_id": run_id}).one_or_none()
    return dict(row._mapping) if row is not None else None


def _list_drift_queue(
    session: Session,
    *,
    limit: int,
    cursor: str | None,
    drift_band: str | None = None,
    status_filter: str = "pending",
) -> tuple[list[dict], str | None]:
    sql_pieces = [
        "SELECT drift_queue_id, person_grace_id, proposed_cluster_id,",
        "       drift_band, status, operator_decision, rationale,",
        "       details, created_at, decided_at",
        "FROM permission_drift_queue",
        "WHERE status = :queue_status",
    ]
    params: dict[str, Any] = {
        "limit": limit + 1,
        "queue_status": status_filter,
    }
    if drift_band:
        sql_pieces.append("AND drift_band = :drift_band")
        params["drift_band"] = drift_band
    if cursor:
        try:
            cursor_created, cursor_id = cursor.split("|", 1)
            params["cursor_created"] = cursor_created
            params["cursor_id"] = UUID(cursor_id)
            sql_pieces.append(
                "AND (created_at, drift_queue_id) < "
                "(CAST(:cursor_created AS TIMESTAMPTZ), :cursor_id)"
            )
        except (ValueError, AttributeError):
            raise HTTPException(status_code=422, detail="Invalid cursor")
    sql_pieces.append(
        "ORDER BY created_at DESC, drift_queue_id DESC LIMIT :limit"
    )
    rows = session.execute(text(" ".join(sql_pieces)), params).all()
    serialized: list[dict] = []
    for r in rows:
        d = dict(r._mapping)
        out: dict[str, Any] = {}
        for k, v in d.items():
            if isinstance(v, UUID):
                out[k] = str(v)
            elif isinstance(v, datetime):
                out[k] = v.isoformat()
            else:
                out[k] = v
        serialized.append(out)
    next_cursor: str | None = None
    if len(serialized) > limit:
        last_visible = serialized[limit - 1]
        next_cursor = (
            f"{last_visible['created_at']}|{last_visible['drift_queue_id']}"
        )
        serialized = serialized[:limit]
    return serialized, next_cursor


def _evidence_bundle_visibility_trim(
    bundle: EvidenceBundle, request: Request
) -> EvidenceBundle:
    """Trim sections according to the active enforcer + principal.

    For v1 the enforcer is consulted at section granularity: each
    section is treated as a ``retrieval_query_event`` resource; if the
    enforcer denies ``view`` for the section's ``source`` label, the
    section's rows are stripped. Sections always remain present so the
    response shape is stable.
    """
    principal = from_admission_tree(request)
    enforcer = get_enforcer()
    if enforcer.matrix is None:
        # No active matrix → no trimming. Default-deny for mutating
        # routes is owned by the middleware; read paths return raw.
        return bundle
    trimmed_sections = []
    for section in bundle.sections:
        decision = enforcer.enforce(
            principal,
            "retrieval_query_event",
            section.source,
            "view",
        )
        if isinstance(decision, Allow):
            trimmed_sections.append(section)
        else:
            trimmed_sections.append(
                section.model_copy(
                    update={"rows": [], "is_empty_placeholder": True}
                )
            )
    return bundle.model_copy(update={"sections": trimmed_sections})


# ---------- 1. GET /matrix/active ----------


@router.get("/matrix/active")
async def get_active_matrix(db: Session = Depends(get_db)) -> dict:
    """Return the most recent ratified matrix, 404 if none."""
    row = _matrix_repo.get_active_matrix(db)
    if row is None:
        raise HTTPException(status_code=404, detail="No active matrix")
    return _serialize_matrix_row(row) or {}


# ---------- 2. GET /matrix/versions ----------


@router.get("/matrix/versions")
async def list_matrix_versions(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    """Paginated chain of ratified matrices (newest first)."""
    offset = 0
    if cursor:
        try:
            offset = int(cursor)
            if offset < 0:
                raise ValueError
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail="Invalid cursor")
    rows = _matrix_repo.get_matrix_versions(db, limit=limit + 1, offset=offset)
    next_cursor: str | None = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = str(offset + limit)
    serialized = [_serialize_matrix_row(r) for r in rows]
    return {"versions": serialized, "next_cursor": next_cursor}


# ---------- 4. GET /matrix/verify-chain ----------
# (registered before /matrix/{matrix_id} so the literal path matches first)


@router.get("/matrix/verify-chain")
async def verify_chain(db: Session = Depends(get_db)) -> dict:
    """Walk the chain newest->oldest, verifying ``previous_hash`` linkage."""
    return _matrix_repo.verify_chain(db)


# ---------- 3. GET /matrix/{matrix_id} ----------


@router.get("/matrix/{matrix_id}")
async def get_matrix_by_id(
    matrix_id: UUID, db: Session = Depends(get_db)
) -> dict:
    row = _matrix_repo.get_matrix_by_id(db, matrix_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Matrix not found")
    return _serialize_matrix_row(row) or {}


# ---------- 5. POST /matrix/hypothesis/generate ----------


@router.post(
    "/matrix/hypothesis/generate",
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_hypothesis_generate(
    body: HypothesisTriggerRequest,
    db: Session = Depends(get_db),
) -> HypothesisTriggerResponse:
    """Spawn the hypothesis CLI; INSERT placeholder run row; return 202.

    The CLI is the long-running owner of the Leiden + LLM narration
    work. To make the 202 response immediately addressable we INSERT
    the row here, return ``run_id``, and pass ``--evidence-id`` plus
    ``--run-id`` so the CLI can persist the hypothesis artifact and
    emit CF1 telemetry when generation completes. The route does NOT
    import the hypothesis generator (D246 mirror).

    Concurrent triggers for the same ``evidence_id`` while a row is
    still ``running`` return 409 (partial unique index, DV4).
    """
    try:
        row = _insert_hypothesis_run_placeholder(
            db, evidence_id=body.evidence_id, operator=body.operator
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "Hypothesis generation already in progress for this evidence"
            ),
        ) from None

    argv_tail: list[str] = [
        "hypothesis",
        "generate",
        "--evidence-id",
        str(body.evidence_id),
        "--run-id",
        str(row["run_id"]),
    ]
    if body.dry_run:
        argv_tail.append("--dry-run")
    pid = _spawn_permissions_cli(argv_tail)

    record_permission_matrix_hypothesis()

    return HypothesisTriggerResponse(
        run_id=row["run_id"],
        evidence_id=row["evidence_id"],
        pid=pid,
    )


# ---------- 6. GET /hypothesis/{run_id} ----------


@router.get("/hypothesis/{run_id}")
async def get_hypothesis_run(
    run_id: UUID, db: Session = Depends(get_db)
) -> dict:
    row = _get_hypothesis_run(db, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Hypothesis run not found")
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, UUID):
            out[k] = str(v)
        elif isinstance(v, datetime):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


# ---------- 7. POST /matrix/ratify ----------


@router.post(
    "/matrix/ratify",
    status_code=status.HTTP_201_CREATED,
)
@no_support_session("POST", "/api/permissions/matrix/ratify")
async def ratify_matrix(
    body: RatifyRequest, db: Session = Depends(get_db)
) -> dict:
    """Sole writer of ``permission_matrices``. Hash is computed
    server-side under ``SELECT ... FOR UPDATE`` on the prior chain
    head; client-supplied hashes are ignored (D331)."""
    try:
        row = _matrix_repo.insert_matrix(
            db,
            matrix=body.matrix,
            created_by=body.created_by,
            version_label=body.version_label,
        )
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        logger.warning("permissions.ratify.insert_failed", error=str(exc))
        raise HTTPException(status_code=409, detail="ratify failed")

    # Atomic enforcer rebuild after a successful insert.
    rebuild_enforcer(body.matrix)

    record_permission_matrix_ratification()
    _emit_elicitation_event(
        "permission_matrix_ratified",
        {
            "matrix_id": str(row["permission_matrix_id"]),
            "version_label": body.version_label,
            "payload_hash": row["payload_hash"],
            "cluster_count": len(body.matrix.role_clusters),
        },
    )
    # D378.b / D340 sub-event — permission_cluster_decision_recorded fires once
    # per ratification as a sub-event of permission_matrix_ratified (N2 4:3
    # mapping per CLAUDE.md).  Append-only telemetry INSERT; does not modify
    # the ratification transaction or its return value.
    # Authorization: D378.b, spec §6 CP2.
    _emit_elicitation_event(
        "permission_cluster_decision_recorded",
        {
            "matrix_id": str(row["permission_matrix_id"]),
            "cluster_id": "ratification-aggregate",
            "decision_kind": "accept_cluster",
        },
    )

    version = PermissionMatrixVersion(
        permission_matrix_id=row["permission_matrix_id"],
        payload=body.matrix,
        payload_hash=row["payload_hash"],
        previous_hash=row.get("previous_hash"),
        created_at=row["created_at"],
        created_by=row.get("created_by"),
        version_label=row.get("version_label"),
    )
    return version.model_dump(mode="json")


# ---------- 8. GET /evidence/{evidence_id} ----------


@router.get("/evidence/{evidence_id}")
async def get_evidence_bundle(
    evidence_id: UUID, request: Request
) -> dict:
    """Return a previously-collected ``EvidenceBundle`` filtered through
    the active enforcer.

    v1 keeps evidence bundles transient (re-collection is cheap per
    D332). The route returns a minimal stable shape for the matrix
    inspector UI: a deterministic empty bundle stamped with the
    requested ``evidence_id``, then visibility-trimmed by the
    enforcer. Once Chunk 43 persists evidence bundles, this route
    will resolve them by id without changing its return shape.
    """
    bundle = EvidenceBundle(evidence_id=evidence_id, sections=[])
    trimmed = _evidence_bundle_visibility_trim(bundle, request)
    return trimmed.model_dump(mode="json")


# ---------- 9. POST /drift/run ----------


@router.post(
    "/drift/run",
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_drift_run(
    body: DriftRunRequest,
    db: Session = Depends(get_db),
) -> DriftRunResponse:
    """Spawn the drift CLI; return 202 + ``job_id``.

    Inserts a drift_runs row with status='running' before spawning
    the CLI subprocess. The CLI receives --job-id and UPDATEs the
    row on completion (D460).
    """
    obs = body.observation_time or datetime.now(tz=timezone.utc)
    job_id = uuid4()

    # D460: INSERT-first — persist the run row before spawning CLI.
    db.execute(text("""
        INSERT INTO drift_runs (id, run_id, observation_time, dry_run, started_at, status, triggered_by, summary_json)
        VALUES (:id, :run_id, :observation_time, :dry_run, now(), 'running', 'api', '{}')
    """), {"id": str(job_id), "run_id": str(job_id), "observation_time": obs, "dry_run": body.dry_run})
    db.commit()

    argv_tail: list[str] = [
        "drift",
        "run",
        "--observation-time",
        obs.isoformat(),
        "--job-id",
        str(job_id),
    ]
    if body.dry_run:
        argv_tail.append("--dry-run")
    pid = _spawn_permissions_cli(argv_tail)

    return DriftRunResponse(
        job_id=job_id,
        pid=pid,
        observation_time=obs,
    )


# ---------- 10. GET /drift/queue ----------


@router.get("/drift/queue")
async def get_drift_queue(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    drift_band: str | None = Query(default=None),
    status: str | None = Query(
        default="pending",
        description="One of pending, decided, ignored",
    ),
    db: Session = Depends(get_db),
) -> dict:
    if drift_band is not None and drift_band not in (
        "high",
        "medium",
        "low",
    ):
        raise HTTPException(status_code=422, detail="Invalid drift_band")
    if status not in ("pending", "decided", "ignored"):
        raise HTTPException(status_code=422, detail="Invalid status")
    rows, next_cursor = _list_drift_queue(
        db,
        limit=limit,
        cursor=cursor,
        drift_band=drift_band,
        status_filter=status,
    )
    return {"queue": rows, "next_cursor": next_cursor}


__all__ = ["router"]
