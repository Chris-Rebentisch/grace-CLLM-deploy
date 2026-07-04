"""Connector management API routes (Chunk 53, D409–D413).

Four routes under ``/api/connectors``:

* ``GET /api/connectors`` — list registered connector types (read path).
* ``GET /api/connectors/{connector_type}/health`` — delegated health check (read path).
* ``GET /api/connectors/{connector_type}/sync/status`` — last sync result (read path).
* ``POST /api/connectors/{connector_type}/sync`` — trigger sync (mutating).

The router does **not** import ``src.connectors.sync_pipeline`` (D246 mirror).
The sync trigger route spawns the CLI via ``subprocess.Popen([..., start_new_session=True])``.
Route-isolation CI guard enforces this in ``test_route_invocation_surface.py``.

Force-load registered connectors at module-import time so that
``_REGISTRY`` is populated when the app starts. Without this import,
``GET /api/connectors`` returns ``[]``. This is NOT a D246 violation —
the isolation guard targets ``sync_pipeline`` only.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path
from uuid import uuid4

import structlog
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from src.connectors.models import ConnectorSyncTriggerRequest
from src.connectors.registry import get_connector, list_registered, _REGISTRY
import src.connectors.synthetic_connector  # noqa: F401  # ensures @register_connector("synthetic") runs at app startup

logger = structlog.get_logger()

router = APIRouter(prefix="/api/connectors", tags=["connectors"])

# In-flight sync tracking for 409 concurrent-trigger protection.
_IN_FLIGHT_SYNCS: dict[str, str] = {}  # connector_type → job_id


@router.get("")
async def list_connectors():
    """List all registered connector types."""
    return list_registered()


@router.get("/{connector_type}/health")
async def connector_health(connector_type: str):
    """Delegated health_check for a connector type."""
    from src.connectors.models import ConnectorConfig

    if connector_type not in _REGISTRY:
        raise HTTPException(status_code=404, detail="Connector type not found")

    config = ConnectorConfig(
        connector_type=connector_type,
        namespace_id=uuid4(),  # placeholder for health check
    )
    connector = get_connector(connector_type, config)
    health = connector.health_check()
    return {"connector_type": connector_type, **health.model_dump()}


@router.get("/{connector_type}/sync/status")
async def sync_status(connector_type: str):
    """Last sync result from connector_sync_state + last_sync_at from graph_namespaces."""
    from src.shared.config import get_settings
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    settings = get_settings()
    engine = create_engine(str(settings.database_url))
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    try:
        row = db.execute(
            text("""
                SELECT cs.connector_type, cs.schema_hash, cs.record_count,
                       cs.last_error, cs.updated_at,
                       gn.last_sync_at, gn.sync_status
                FROM connector_sync_state cs
                JOIN graph_namespaces gn ON cs.namespace_id = gn.id
                WHERE cs.connector_type = :ct
                LIMIT 1
            """),
            {"ct": connector_type},
        ).fetchone()

        if not row:
            raise HTTPException(
                status_code=404,
                detail="No sync state for this connector",
            )

        return {
            "connector_type": row[0],
            "schema_hash": row[1],
            "record_count": row[2],
            "last_error": row[3],
            "updated_at": row[4].isoformat() if row[4] else None,
            "last_sync_at": row[5].isoformat() if row[5] else None,
            "sync_status": row[6],
            "deletions_supported": False,  # D411 deferred
        }
    finally:
        db.close()
        engine.dispose()


def _wait_and_clear_inflight(connector_type: str, proc: subprocess.Popen) -> None:
    """Wait for spawned CLI to exit, then release per-type lock (Chunk 53 §7 / R4)."""
    try:
        proc.wait()
    finally:
        _IN_FLIGHT_SYNCS.pop(connector_type, None)


@router.post("/{connector_type}/sync", status_code=status.HTTP_202_ACCEPTED)
async def trigger_sync(
    connector_type: str,
    body: ConnectorSyncTriggerRequest,
):
    """Trigger a connector sync via CLI subprocess (D246 mirror).

    Mutating; X-Admin-Key when GRACE_ADMIN_KEY is set; loopback bypass otherwise.
    409 concurrent-trigger race protection.
    """
    if connector_type not in _REGISTRY:
        raise HTTPException(status_code=404, detail="Connector type not found")

    # Concurrent-trigger protection
    if connector_type in _IN_FLIGHT_SYNCS:
        return JSONResponse(
            status_code=409,
            content={
                "detail": "Sync already in progress",
                "job_id": _IN_FLIGHT_SYNCS[connector_type],
            },
        )

    job_id = str(uuid4())
    _IN_FLIGHT_SYNCS[connector_type] = job_id

    cmd: list[str] = [
        sys.executable,
        "-m",
        "src.connectors.sync_pipeline",
        "run",
        "--connector-type", connector_type,
        "--namespace-id", str(body.namespace_id),
        "--batch-size", str(body.batch_size),
    ]
    if body.mode:
        cmd.extend(["--mode", body.mode])
    if body.dry_run:
        cmd.append("--dry-run")

    try:
        proc = subprocess.Popen(  # noqa: S603 — known argv
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            cwd=str(Path(__file__).resolve().parents[2]),
        )
        logger.info(
            "connector_sync_triggered",
            connector_type=connector_type,
            job_id=job_id,
            pid=proc.pid,
        )
        threading.Thread(
            target=_wait_and_clear_inflight,
            args=(connector_type, proc),
            daemon=True,
            name=f"grace-connector-sync-wait-{connector_type}",
        ).start()
    except Exception as exc:
        _IN_FLIGHT_SYNCS.pop(connector_type, None)
        logger.error(
            "connector_sync_trigger_failed",
            connector_type=connector_type,
            error=str(exc),
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail="Failed to trigger connector sync"
        ) from exc

    return {
        "job_id": job_id,
        "connector_type": connector_type,
        "namespace_id": str(body.namespace_id),
        "status": "started",
    }
