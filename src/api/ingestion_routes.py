"""Ingestion API routes (Chunk 55, D419/D420/D427; Chunk 57, D424/D425).

Routes under ``/api/ingestion/``:

* Sources CRUD (list, get, create, patch, delete)
* Test connection, trigger run, run status
* Runs list
* Readiness gate, deployment-path PATCH, curate stub
* OAuth2 initiation + callback (Chunk 57)

The router does **not** import ``src.ingestion.pipeline`` (D246 mirror).
The run trigger route spawns the CLI via ``subprocess.Popen([..., start_new_session=True])``.
Route-isolation CI guard enforces this in ``test_route_invocation_surface.py``.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

import yaml

from src.graph.arcade_client import ArcadeClient, get_arcade_client
from src.ingestion.models import (
    ConnectionTestResult,
    IngestionRun,
    IngestionRunRead,
    IngestionRunStatus,
    IngestionSource,
    IngestionSourceRead,
    IngestionSourceStatus,
    ReadinessThresholds,
    SampleDateRange,
    _redact_credentials,
)
from src.ingestion.readiness import check_readiness
from src.shared.database import get_db

logger = structlog.get_logger()

ingestion_router = APIRouter(prefix="/api/ingestion", tags=["ingestion"])

# In-flight run tracking for 409 concurrent-trigger protection.
# Keys by source_id: UUID; stores the Popen handle.
# Mirrors ``src/api/connectors_routes.py:42–44`` (_IN_FLIGHT_SYNCS pattern).
_IN_FLIGHT_RUNS: dict[UUID, subprocess.Popen] = {}

# In-flight triage tracking (Chunk 56, D434).
# Separate from _IN_FLIGHT_RUNS — ingest and triage may run concurrently.
_IN_FLIGHT_TRIAGE: dict[UUID, subprocess.Popen] = {}

# --- File-based source types (for /test and /run routing) ---
_FILE_BASED_TYPES = frozenset({"mbox", "eml", "msg", "pst"})
_LIVE_TYPES = frozenset({"imap", "exchange", "gmail"})


# --- Request models ---

class IngestionSourceCreateRequest(BaseModel):
    """Body for POST /api/ingestion/sources."""
    name: str = Field(description="Source display name.")
    source_type: str = Field(description="Source type.")
    config_json: dict = Field(description="Source configuration JSON.")
    segment: str = Field(description="Ontology module / segment.")
    enabled: bool = Field(default=True, description="Whether source is enabled.")


class IngestionSourcePatchRequest(BaseModel):
    """Body for PATCH /api/ingestion/sources/{source_id}."""
    name: str | None = None
    source_type: str | None = None
    config_json: dict | None = None
    segment: str | None = None
    enabled: bool | None = None


class DeploymentPathPatchRequest(BaseModel):
    """Body for PATCH /api/ingestion/config/deployment-path."""
    deployment_path: str | None = Field(
        description="Deployment path (A, B, C, or null to reset)."
    )


class CurateRequest(BaseModel):
    """Body for POST /api/ingestion/curate (Chunk 56, D432)."""
    source_id: UUID = Field(description="Ingestion source ID.")
    selected_message_ids: list[str] = Field(description="List of RFC 5322 Message-IDs to curate.")
    deployment_path: str = Field(description="Deployment path (B or C).")


class OAuthCallbackRequest(BaseModel):
    """Body for POST /api/ingestion/oauth/callback (Chunk 57)."""
    provider: str = Field(description="Provider: exchange or gmail.")
    code: str = Field(description="Authorization code from OAuth2 redirect.")
    state: str = Field(description="CSRF state parameter.")
    source_id: UUID = Field(description="Ingestion source to bind the token to.")
    redirect_uri: str | None = Field(default=None, description="Redirect URI override.")


# OAuth2 CSRF state store — in-memory, TTL 10 min (D425, v1 acceptable).
# Keys: state UUID string; values: (source_id, expires_at_unix_ts).
_OAUTH_STATE: dict[str, tuple[UUID, float]] = {}
_OAUTH_STATE_TTL_SECONDS = 600  # 10 minutes


def _prune_oauth_state() -> None:
    """Lazy prune expired entries from _OAUTH_STATE."""
    now = time.time()
    expired = [k for k, (_, exp) in _OAUTH_STATE.items() if exp < now]
    for k in expired:
        _OAUTH_STATE.pop(k, None)


# --- Registration-time config validation (F-0031 / ISS-0047) ---

def _validate_source_config(source_type: str, config_json: dict | None) -> dict:
    """Validate ``config_json`` against the SourceConfig adapter models.

    F-0031 / ISS-0047 (validation run 2026-07-03) capture-the-why:
    ``POST /api/ingestion/sources`` previously persisted ``config_json``
    unvalidated. A config missing the tagged-union discriminator
    (``source_type`` *inside* config_json) — or missing required adapter
    fields — only failed at cycle time as a raw pydantic
    ``union_tag_not_found``. Validate at registration instead, defaulting
    the discriminator from the route-level ``source_type`` field when
    absent, and surface a clear 422 on invalid configs.

    Returns the normalized config dict (discriminator injected) so the
    persisted row is always cycle-loadable. Imports MODELS only — never
    the ingestion pipelines (D246 route-isolation preserved).
    """
    from pydantic import TypeAdapter, ValidationError

    from src.ingestion.models import SourceConfig

    cfg = dict(config_json or {})
    # Default the discriminator from the top-level source_type when absent.
    cfg.setdefault("source_type", source_type)
    if cfg["source_type"] != source_type:
        raise HTTPException(
            status_code=422,
            detail=(
                f"config_json.source_type ({cfg['source_type']!r}) does not match "
                f"the source's source_type ({source_type!r}). Omit source_type "
                "from config_json or make the two fields agree."
            ),
        )
    try:
        TypeAdapter(SourceConfig).validate_python(cfg)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'config_json'}: {err['msg']}"
            for err in exc.errors()
        )
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid config_json for source_type {source_type!r}: {problems}. "
                "Valid source types: mbox, eml, msg, pst, imap, exchange, gmail — "
                "see src/ingestion/models.py SourceConfig for the required fields "
                "of each."
            ),
        ) from exc
    return cfg


# --- Waiter helper ---

def _wait_and_clear_inflight(source_id: UUID, proc: subprocess.Popen) -> None:
    """Wait for spawned CLI to exit, then release per-source lock."""
    try:
        proc.wait()
    finally:
        _IN_FLIGHT_RUNS.pop(source_id, None)


def _wait_and_clear_triage_inflight(source_id: UUID, proc: subprocess.Popen) -> None:
    """Wait for spawned triage CLI to exit, then release per-source lock (Chunk 56)."""
    try:
        proc.wait()
    finally:
        _IN_FLIGHT_TRIAGE.pop(source_id, None)


# --- Routes ---

@ingestion_router.get("/sources")
async def list_sources(
    cursor: str | None = Query(None),
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Paginated list of ingestion sources (excludes soft-deleted)."""
    query = db.query(IngestionSource).filter(IngestionSource.deleted_at.is_(None))
    query = query.order_by(IngestionSource.created_at.desc())

    if cursor:
        # Simple offset-based cursor
        try:
            offset = int(cursor)
            query = query.offset(offset)
        except ValueError:
            pass

    rows = query.limit(limit + 1).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = str(int(cursor or 0) + limit) if has_more else None

    return {
        "items": [IngestionSourceRead.from_orm_row(r).model_dump(mode="json") for r in rows],
        "next_cursor": next_cursor,
    }


@ingestion_router.get("/sources/{source_id}")
async def get_source(source_id: UUID, db: Session = Depends(get_db)):
    """Get a single ingestion source by ID."""
    row = db.query(IngestionSource).filter_by(id=source_id).first()
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Source not found")
    return IngestionSourceRead.from_orm_row(row).model_dump(mode="json")


@ingestion_router.post("/sources", status_code=status.HTTP_201_CREATED)
async def create_source(
    body: IngestionSourceCreateRequest,
    db: Session = Depends(get_db),
):
    """Create an ingestion source. Credentials redacted in response.

    F-0031 / ISS-0047: config_json is validated against the SourceConfig
    adapter model at registration time (422 on invalid) so misconfigured
    sources can no longer be created and then fail opaquely at cycle time.
    """
    validated_config = _validate_source_config(body.source_type, body.config_json)
    source = IngestionSource(
        id=uuid4(),
        name=body.name,
        source_type=body.source_type,
        config_json=validated_config,
        segment=body.segment,
        enabled=body.enabled,
    )

    # Chunk 57: IMAP sources go pending->ready on create when credentials present
    if body.source_type == "imap":
        cfg = body.config_json or {}
        app_password_env = cfg.get("app_password_env")
        has_env_cred = bool(app_password_env and os.environ.get(app_password_env))
        has_inline_cred = bool(cfg.get("password"))
        if has_env_cred or has_inline_cred:
            source.status = IngestionSourceStatus.ready.value

    db.add(source)
    db.commit()
    db.refresh(source)
    return IngestionSourceRead.from_orm_row(source).model_dump(mode="json")


@ingestion_router.patch("/sources/{source_id}")
async def patch_source(
    source_id: UUID,
    body: IngestionSourcePatchRequest,
    db: Session = Depends(get_db),
):
    """Partial update of an ingestion source."""
    row = db.query(IngestionSource).filter_by(id=source_id).first()
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Source not found")

    updates = body.model_dump(exclude_unset=True)

    # F-0031 / ISS-0047: PATCH is the second registration surface — validate
    # the effective (source_type, config_json) pair whenever either changes,
    # so a patch can't reintroduce the cycle-time union_tag_not_found class.
    if "config_json" in updates or "source_type" in updates:
        effective_type = updates.get("source_type") or row.source_type
        effective_config = (
            updates["config_json"] if "config_json" in updates else row.config_json
        )
        normalized = _validate_source_config(effective_type, effective_config)
        if "config_json" in updates:
            updates["config_json"] = normalized

    for field_name, value in updates.items():
        setattr(row, field_name, value)

    db.commit()
    db.refresh(row)
    return IngestionSourceRead.from_orm_row(row).model_dump(mode="json")


@ingestion_router.delete("/sources/{source_id}")
async def delete_source(source_id: UUID, db: Session = Depends(get_db)):
    """Soft-delete an ingestion source."""
    row = db.query(IngestionSource).filter_by(id=source_id).first()
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Source not found")

    row.deleted_at = datetime.now(timezone.utc)
    db.commit()
    return {"detail": "Source deleted"}


@ingestion_router.post("/sources/{source_id}/test")
async def test_connection(source_id: UUID, db: Session = Depends(get_db)):
    """Test connectivity for a source.

    File-based: 200 + ok=true + sample stats.
    Live variants: 200 + ok=false + unified deferral message (AC-25).
    """
    row = db.query(IngestionSource).filter_by(id=source_id).first()
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Source not found")

    # File-based and live: try to connect and sample
    try:
        from pydantic import TypeAdapter
        from src.ingestion.models import SourceConfig

        config_adapter = TypeAdapter(SourceConfig)
        config = config_adapter.validate_python(row.config_json)

        from src.ingestion.adapter_registry import get_adapter

        adapter = get_adapter(row.source_type, config)
        await adapter.connect(config)

        count = 0
        oldest_date = None
        newest_date = None

        async for msg_id in adapter.list_messages(limit=10):
            result = await adapter.parse_message(msg_id)
            count += 1
            if result.event.sent_at:
                if oldest_date is None or result.event.sent_at < oldest_date:
                    oldest_date = result.event.sent_at
                if newest_date is None or result.event.sent_at > newest_date:
                    newest_date = result.event.sent_at

        await adapter.close()

        date_range = None
        if oldest_date and newest_date:
            date_range = SampleDateRange(oldest=oldest_date, newest=newest_date)

        return ConnectionTestResult(
            ok=True,
            sample_message_count=count,
            sample_date_range=date_range,
        ).model_dump(mode="json")

    except Exception as exc:
        return ConnectionTestResult(
            ok=False,
            error=str(exc),
        ).model_dump(mode="json")


@ingestion_router.post("/sources/{source_id}/run", status_code=status.HTTP_202_ACCEPTED)
async def trigger_run(source_id: UUID, db: Session = Depends(get_db)):
    """Trigger an ingestion run via CLI subprocess (D246 mirror).

    Concurrent-trigger 409 protection per-source.
    Live variants return 409 with unified deferral.
    """
    row = db.query(IngestionSource).filter_by(id=source_id).first()
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Source not found")

    # Concurrent-trigger protection (shared for both file-based and live)
    if source_id in _IN_FLIGHT_RUNS:
        existing_proc = _IN_FLIGHT_RUNS[source_id]
        if existing_proc.poll() is None:
            # Create run_id for the response
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "Ingestion run already in progress for this source",
                    "in_flight_run_id": str(source_id),
                },
            )
        else:
            _IN_FLIGHT_RUNS.pop(source_id, None)

    # Create a run record
    run_id = uuid4()
    run = IngestionRun(
        id=run_id,
        source_id=source_id,
        status=IngestionRunStatus.pending.value,
    )
    db.add(run)
    db.commit()

    # Spawn CLI subprocess — live types use `cycle`, file-based use `run`
    if row.source_type in _LIVE_TYPES:
        cmd: list[str] = [
            sys.executable,
            "-m",
            "src.ingestion",
            "cycle",
            "--source-id",
            str(source_id),
        ]
    else:
        cmd = [
            sys.executable,
            "-m",
            "src.ingestion",
            "run",
            "--source-id",
            str(source_id),
        ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            cwd=str(Path(__file__).resolve().parents[2]),
        )
        _IN_FLIGHT_RUNS[source_id] = proc

        logger.info(
            "ingestion_run_triggered",
            source_id=str(source_id),
            run_id=str(run_id),
            pid=proc.pid,
        )

        threading.Thread(
            target=_wait_and_clear_inflight,
            args=(source_id, proc),
            daemon=True,
        ).start()

    except Exception as exc:
        run.status = IngestionRunStatus.failed.value
        run.error_text = str(exc)
        db.commit()
        logger.error(
            "ingestion_run_trigger_failed",
            source_id=str(source_id),
            error=str(exc),
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail="Failed to trigger ingestion run"
        ) from exc

    return {"run_id": str(run_id), "source_id": str(source_id), "pid": proc.pid}


@ingestion_router.get("/sources/{source_id}/status")
async def run_status(source_id: UUID, db: Session = Depends(get_db)):
    """Latest run + in-flight status for a source."""
    row = db.query(IngestionSource).filter_by(id=source_id).first()
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Source not found")

    latest_run = (
        db.query(IngestionRun)
        .filter_by(source_id=source_id)
        .order_by(IngestionRun.started_at.desc())
        .first()
    )

    in_flight = source_id in _IN_FLIGHT_RUNS and _IN_FLIGHT_RUNS[source_id].poll() is None

    return {
        "source_id": str(source_id),
        "source_status": row.status,
        "in_flight": in_flight,
        "latest_run": IngestionRunRead.model_validate(latest_run).model_dump(mode="json") if latest_run else None,
    }


@ingestion_router.get("/runs")
async def list_runs(
    source_id: UUID | None = Query(None),
    cursor: str | None = Query(None),
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Paginated list of ingestion runs. checkpoint_json excluded."""
    query = db.query(IngestionRun)
    if source_id:
        query = query.filter_by(source_id=source_id)
    query = query.order_by(IngestionRun.started_at.desc())

    if cursor:
        try:
            offset = int(cursor)
            query = query.offset(offset)
        except ValueError:
            pass

    rows = query.limit(limit + 1).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = str(int(cursor or 0) + limit) if has_more else None

    return {
        "items": [IngestionRunRead.model_validate(r).model_dump(mode="json") for r in rows],
        "next_cursor": next_cursor,
    }


@ingestion_router.get("/readiness")
async def readiness_gate(
    db: Session = Depends(get_db),
    arcade_client: ArcadeClient = Depends(get_arcade_client),
):
    """D274 readiness gate — hybrid Postgres+ArcadeDB.

    Segments derived from ``SELECT DISTINCT segment FROM ingestion_sources
    WHERE enabled = true AND deleted_at IS NULL`` (AC-26).
    """
    yaml_path = Path(__file__).resolve().parent.parent.parent / "config" / "discovery.yaml"
    with open(yaml_path) as f:
        config = yaml.safe_load(f) or {}

    ingestion_config = config.get("ingestion", {})
    deployment_path = ingestion_config.get("deployment_path")
    if not deployment_path:
        # 404 aligns with §3.2 default (`deployment_path: null`) + CP10 smoke
        # (`scripts/smoke-live-server.sh` accepts 200 or 404 for this probe).
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="deployment_path not configured — set via PATCH /api/ingestion/config/deployment-path",
        )

    # F-0030d / ISS-0047 (rider on F-0031): an invalid configured value (e.g.
    # hand-edited discovery.yaml) previously surfaced as a raw pydantic
    # literal_error 500 when ReadinessResult was constructed. Produce
    # guidance instead.
    if deployment_path not in ("A", "B", "C"):
        raise HTTPException(
            status_code=422,
            detail=(
                f"deployment_path {deployment_path!r} in config/discovery.yaml is "
                "invalid — must be 'A', 'B', or 'C'. Fix via PATCH "
                "/api/ingestion/config/deployment-path."
            ),
        )

    readiness_config = ingestion_config.get("readiness", {})
    thresholds = ReadinessThresholds(
        cq_mention_threshold=readiness_config.get("cq_mention_threshold", 3),
        confidence_threshold=readiness_config.get("confidence_threshold", 0.85),
    )

    # AC-26: segments auto-derive from active sources
    result = db.execute(
        text("SELECT DISTINCT segment FROM ingestion_sources WHERE enabled = true AND deleted_at IS NULL")
    )
    segments = [row[0] for row in result]

    # Chunk 56: bootstrap_complete for Path B/C — check curated_email_subsets
    bootstrap_complete = True
    if deployment_path in ("B", "C"):
        from src.ingestion.models import CuratedEmailSubsetRow

        ready_count = (
            db.query(CuratedEmailSubsetRow)
            .filter_by(sentinel_status="ready")
            .count()
        )
        bootstrap_complete = ready_count > 0

    readiness = await check_readiness(
        deployment_path,
        segments,
        arcade_client,
        db,
        thresholds=thresholds,
        bootstrap_complete=bootstrap_complete,
    )
    return readiness.model_dump(mode="json")


@ingestion_router.patch("/config/deployment-path")
async def patch_deployment_path(body: DeploymentPathPatchRequest):
    """Write deployment_path to config/discovery.yaml.

    Uses section-scoped merge helper mirroring ``src/api/seed_routes.py:162–176``
    ``_update_industry_profile()`` pattern. Does NOT use ``write_llm_config_to_yaml()``.
    (security-posture §39.7)
    """
    # F-0030d / ISS-0047 (rider on F-0031): reject invalid values at write
    # time with guidance, instead of letting a bad value land in
    # discovery.yaml and later explode as a raw pydantic literal_error.
    if body.deployment_path is not None and body.deployment_path not in ("A", "B", "C"):
        raise HTTPException(
            status_code=422,
            detail=(
                f"deployment_path {body.deployment_path!r} is invalid — must be "
                "'A', 'B', 'C', or null to reset."
            ),
        )
    _patch_discovery_ingestion(body.deployment_path)
    return {"deployment_path": body.deployment_path}


# ---------------------------------------------------------------------------
# GET /api/ingestion/config — read-only config snapshot (Chunk 60, CP1)
# ---------------------------------------------------------------------------

_TIER3_NUMERIC_TO_BAND: dict[str, str] = {
    "0.20": "stricter",
    "0.30": "balanced",
    "0.40": "looser",
}

_TIER3_BAND_TO_NUMERIC: dict[str, float] = {
    "stricter": 0.20,
    "balanced": 0.30,
    "looser": 0.40,
}


@ingestion_router.get("/config")
async def get_ingestion_config():
    """Read-only snapshot of ingestion configuration (Chunk 60, spec §7.1).

    Returns deployment_path, organization_domains, and tier3_band.
    Never returns raw cosine value (D120/D217).
    """
    yaml_path = Path(__file__).resolve().parent.parent.parent / "config"

    # deployment_path from discovery.yaml
    discovery_path = yaml_path / "discovery.yaml"
    deployment_path = None
    if discovery_path.exists():
        with open(discovery_path) as f:
            disc = yaml.safe_load(f) or {}
        deployment_path = disc.get("ingestion", {}).get("deployment_path")

    # organization_domains from voice_tone_config.yaml
    vt_path = yaml_path / "voice_tone_config.yaml"
    org_domains: list[str] = []
    if vt_path.exists():
        with open(vt_path) as f:
            vt = yaml.safe_load(f) or {}
        org_domains = vt.get("organization_domains", []) or []

    # tier3_band from triage_rules.yaml (inverse-map numeric → band)
    tr_path = yaml_path / "triage_rules.yaml"
    tier3_band = "balanced"
    if tr_path.exists():
        with open(tr_path) as f:
            tr = yaml.safe_load(f) or {}
        raw = tr.get("tier3", {}).get("threshold")
        if raw is not None:
            tier3_band = _TIER3_NUMERIC_TO_BAND.get(f"{float(raw):.2f}", "balanced")

    return {
        "deployment_path": deployment_path,
        "organization_domains": org_domains,
        "tier3_band": tier3_band,
    }


# ---------------------------------------------------------------------------
# PATCH /api/ingestion/config/organization-domains (Chunk 60, CP1)
# ---------------------------------------------------------------------------


class OrganizationDomainsPatchRequest(BaseModel):
    """Body for PATCH /api/ingestion/config/organization-domains."""
    organization_domains: list[str] = Field(description="List of org email domains.")


def _require_admin_key_ingestion(request: Request) -> None:
    """Mutating-route admin-key enforcement (mirrors communications_routes pattern)."""
    admin_key = os.environ.get("GRACE_ADMIN_KEY", "")
    if not admin_key:
        client_host = request.client.host if request.client else None
        if client_host in {"127.0.0.1", "::1", "testclient"}:
            return
        raise HTTPException(status_code=401, detail="admin key required")
    submitted = request.headers.get("X-Admin-Key", "")
    if not submitted or not secrets.compare_digest(admin_key, submitted):
        raise HTTPException(status_code=401, detail="admin key required")


@ingestion_router.patch("/config/organization-domains")
async def patch_organization_domains(
    body: OrganizationDomainsPatchRequest,
    request: Request,
):
    """Update organization_domains in config/voice_tone_config.yaml.

    Admin-key when GRACE_ADMIN_KEY set; loopback bypass otherwise.
    Validates each domain is non-empty and looks like a domain.
    """
    _require_admin_key_ingestion(request)

    import re

    domain_re = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?)+$")
    for d in body.organization_domains:
        if not d or not domain_re.match(d):
            raise HTTPException(status_code=422, detail=f"Invalid domain: {d!r}")

    yaml_path = Path(__file__).resolve().parent.parent.parent / "config" / "voice_tone_config.yaml"
    with open(yaml_path) as f:
        data = yaml.safe_load(f) or {}

    data["organization_domains"] = body.organization_domains

    with open(yaml_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    return {"organization_domains": body.organization_domains}


# ---------------------------------------------------------------------------
# PATCH /api/ingestion/config/tier3-threshold (Chunk 60, CP1)
# ---------------------------------------------------------------------------


class Tier3ThresholdPatchRequest(BaseModel):
    """Body for PATCH /api/ingestion/config/tier3-threshold."""
    tier3_band: str = Field(description="Band: stricter, balanced, or looser.")


@ingestion_router.patch("/config/tier3-threshold")
async def patch_tier3_threshold(
    body: Tier3ThresholdPatchRequest,
    request: Request,
):
    """Map band label → numeric threshold, write to config/triage_rules.yaml.

    Admin-key when GRACE_ADMIN_KEY set; loopback bypass otherwise.
    D120/D217: never expose raw cosine value in response.
    """
    _require_admin_key_ingestion(request)

    if body.tier3_band not in _TIER3_BAND_TO_NUMERIC:
        raise HTTPException(
            status_code=422,
            detail=f"tier3_band must be one of: {sorted(_TIER3_BAND_TO_NUMERIC.keys())}",
        )

    numeric = _TIER3_BAND_TO_NUMERIC[body.tier3_band]

    yaml_path = Path(__file__).resolve().parent.parent.parent / "config" / "triage_rules.yaml"
    with open(yaml_path) as f:
        data = yaml.safe_load(f) or {}

    if "tier3" not in data:
        data["tier3"] = {}
    data["tier3"]["threshold"] = numeric

    with open(yaml_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    return {"tier3_band": body.tier3_band}


@ingestion_router.post("/curate", status_code=status.HTTP_201_CREATED)
async def curate_emails(
    body: CurateRequest,
    db: Session = Depends(get_db),
):
    """Path B/C curation — create curated email subset (Chunk 56, D432)."""
    if not body.selected_message_ids:
        raise HTTPException(status_code=400, detail="selected_message_ids must not be empty")

    source = db.query(IngestionSource).filter_by(id=body.source_id).first()
    if source is None or source.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Source not found")

    # Validate all message IDs exist
    from src.ingestion.models import CommunicationEventRow

    events = (
        db.query(CommunicationEventRow)
        .filter(
            CommunicationEventRow.source_id == body.source_id,
            CommunicationEventRow.message_id.in_(body.selected_message_ids),
        )
        .all()
    )
    found_ids = {ev.message_id for ev in events}
    unknown = set(body.selected_message_ids) - found_ids
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown message IDs: {sorted(unknown)}")

    # Compute diversity metrics (D432)
    sender_count = len({ev.sender_email for ev in events})
    if sender_count < 5:
        sender_band = "narrow"
    elif sender_count <= 20:
        sender_band = "balanced"
    else:
        sender_band = "wide"

    # Thread depth: v1 constant "mostly_single" (thread_id always NULL)
    thread_groups: dict[str | None, int] = {}
    for ev in events:
        thread_groups.setdefault(ev.thread_id, 0)
        thread_groups[ev.thread_id] += 1
    mean_depth = sum(thread_groups.values()) / max(len(thread_groups), 1)
    if mean_depth <= 1.5:
        thread_depth_band = "mostly_single"
    elif mean_depth <= 3.0:
        thread_depth_band = "mixed"
    else:
        thread_depth_band = "deep_threaded"

    # Date range
    sent_dates = [ev.sent_at for ev in events if ev.sent_at is not None]
    if len(sent_dates) >= 2:
        span_days = (max(sent_dates) - min(sent_dates)).days
    else:
        span_days = 0
    if span_days < 30:
        date_range_band = "short"
    elif span_days <= 365:
        date_range_band = "quarter"
    else:
        date_range_band = "year_plus"

    diversity_metrics = {
        "sender_band": sender_band,
        "sender_count": sender_count,
        "thread_depth_band": thread_depth_band,
        "thread_count": len(thread_groups),
        "date_range_band": date_range_band,
        "date_span_days": span_days,
    }

    sentinel = "ready" if body.deployment_path == "B" else "pending"

    from src.ingestion.models import CuratedEmailSubsetRow

    subset = CuratedEmailSubsetRow(
        id=uuid4(),
        source_id=body.source_id,
        deployment_path=body.deployment_path,
        selected_message_ids=body.selected_message_ids,
        diversity_metrics=diversity_metrics,
        sentinel_status=sentinel,
    )
    db.add(subset)
    db.commit()

    return {
        "subset_id": str(subset.id),
        "message_count": len(body.selected_message_ids),
        "diversity_metrics": {
            "sender_band": sender_band,
            "thread_depth_band": thread_depth_band,
            "date_range_band": date_range_band,
        },
    }


@ingestion_router.get("/curate/{subset_id}")
async def get_curated_subset(subset_id: UUID, db: Session = Depends(get_db)):
    """Get curated subset detail with diversity metrics."""
    from src.ingestion.models import CuratedEmailSubsetRow

    row = db.query(CuratedEmailSubsetRow).filter_by(id=subset_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Curated subset not found")
    return {
        "id": str(row.id),
        "source_id": str(row.source_id),
        "deployment_path": row.deployment_path,
        "selected_message_ids": row.selected_message_ids,
        "diversity_metrics": row.diversity_metrics,
        "sentinel_status": row.sentinel_status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@ingestion_router.get("/sources/{source_id}/events")
async def list_events(
    source_id: UUID,
    triage_outcome: str | None = Query(None),
    cursor: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List communication events for a source — metadata only, no body/headers/attachments (D435 §40.10)."""
    row = db.query(IngestionSource).filter_by(id=source_id).first()
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Source not found")

    from src.ingestion.models import CommunicationEventRow, CommunicationEventListItem

    query = db.query(CommunicationEventRow).filter(
        CommunicationEventRow.source_id == source_id
    )
    if triage_outcome:
        query = query.filter(CommunicationEventRow.triage_tier_outcome == triage_outcome)
    query = query.order_by(CommunicationEventRow.id)

    if cursor:
        try:
            offset = int(cursor)
            query = query.offset(offset)
        except ValueError:
            pass

    rows = query.limit(limit + 1).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = str(int(cursor or 0) + limit) if has_more else None

    items = [
        {
            "event_id": str(r.id),
            "message_id": r.message_id,
            "sender_email": r.sender_email,
            "sender_display_name": r.sender_display_name,
            "subject": r.subject,
            "sent_at": r.sent_at.isoformat() if r.sent_at else None,
            "received_at": r.received_at.isoformat() if r.received_at else None,
            "triage_tier_outcome": r.triage_tier_outcome,
        }
        for r in rows
    ]
    return {"items": items, "next_cursor": next_cursor}


@ingestion_router.post("/sources/{source_id}/triage", status_code=status.HTTP_202_ACCEPTED)
async def trigger_triage(source_id: UUID, db: Session = Depends(get_db)):
    """Trigger triage pipeline via CLI subprocess (Chunk 56, D434 / D246 mirror).

    Uses ``_IN_FLIGHT_TRIAGE`` (not ``_IN_FLIGHT_RUNS``) for concurrent-trigger
    protection. Ingest and triage may run concurrently per source.
    """
    row = db.query(IngestionSource).filter_by(id=source_id).first()
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Source not found")

    # Readiness gate — overall_ready must be True
    # For Path B, bootstrap_complete must also be True
    yaml_path = Path(__file__).resolve().parent.parent.parent / "config" / "discovery.yaml"
    with open(yaml_path) as f:
        config = yaml.safe_load(f) or {}
    ingestion_config = config.get("ingestion", {})
    deployment_path = ingestion_config.get("deployment_path")

    if deployment_path == "B":
        # Check bootstrap_complete: curated_email_subsets with sentinel_status='ready' exists
        from src.ingestion.models import CuratedEmailSubsetRow

        ready_subset = (
            db.query(CuratedEmailSubsetRow)
            .filter_by(source_id=source_id, sentinel_status="ready")
            .first()
        )
        if ready_subset is None:
            raise HTTPException(
                status_code=422,
                detail="Path B bootstrap not complete: no curated subset with sentinel_status='ready'",
            )

    # Concurrent-trigger protection (against _IN_FLIGHT_TRIAGE only)
    if source_id in _IN_FLIGHT_TRIAGE:
        existing_proc = _IN_FLIGHT_TRIAGE[source_id]
        if existing_proc.poll() is None:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "Triage run already in progress for this source",
                    "source_id": str(source_id),
                },
            )
        else:
            _IN_FLIGHT_TRIAGE.pop(source_id, None)

    # Create run record
    run_id = uuid4()
    run = IngestionRun(
        id=run_id,
        source_id=source_id,
        status=IngestionRunStatus.pending.value,
    )
    db.add(run)
    db.commit()

    # Spawn CLI subprocess
    cmd: list[str] = [
        sys.executable,
        "-m",
        "src.ingestion",
        "triage",
        "--source-id",
        str(source_id),
        "--run-id",
        str(run_id),
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            cwd=str(Path(__file__).resolve().parents[2]),
        )
        _IN_FLIGHT_TRIAGE[source_id] = proc

        logger.info(
            "triage_run_triggered",
            source_id=str(source_id),
            run_id=str(run_id),
            pid=proc.pid,
        )

        threading.Thread(
            target=_wait_and_clear_triage_inflight,
            args=(source_id, proc),
            daemon=True,
        ).start()

    except Exception as exc:
        run.status = IngestionRunStatus.failed.value
        run.error_text = str(exc)
        db.commit()
        logger.error(
            "triage_run_trigger_failed",
            source_id=str(source_id),
            error=str(exc),
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail="Failed to trigger triage run"
        ) from exc

    return {"run_id": str(run_id), "status": "pending"}


# --- Retriage stats (Chunk 59, CP9) ---


@ingestion_router.get("/retriage/stats")
async def retriage_stats(db: Session = Depends(get_db)):
    """Aggregate retriage statistics — read path.

    Returns latest cycle number, counts by retriage_state, and total
    events eligible for retriage (triage_tier_outcome LIKE 'filtered_%').
    """
    from src.ingestion.models import CommunicationEventRow

    # Latest cycle
    latest_cycle_row = db.execute(
        text("SELECT MAX(retriage_cycle) AS max_cycle FROM communication_events")
    ).fetchone()
    latest_cycle = latest_cycle_row.max_cycle if latest_cycle_row and latest_cycle_row.max_cycle is not None else 0

    # Counts by retriage_state
    state_rows = db.execute(
        text(
            "SELECT COALESCE(retriage_state, 'untriaged') AS state, count(*) AS cnt "
            "FROM communication_events "
            "WHERE triage_tier_outcome LIKE 'filtered_%' "
            "GROUP BY COALESCE(retriage_state, 'untriaged')"
        )
    ).fetchall()
    by_state = {row.state: row.cnt for row in state_rows}

    # Total filtered events (eligible for retriage)
    total_filtered_row = db.execute(
        text(
            "SELECT count(*) AS cnt FROM communication_events "
            "WHERE triage_tier_outcome LIKE 'filtered_%'"
        )
    ).fetchone()
    total_filtered = total_filtered_row.cnt if total_filtered_row else 0

    return {
        "latest_cycle": latest_cycle,
        "total_filtered": total_filtered,
        "by_state": by_state,
    }


# --- OAuth2 routes (Chunk 57, D425) ---

@ingestion_router.get("/oauth/init/{provider}")
async def oauth_init(
    provider: str,
    source_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Generate OAuth2 authorize URL with CSRF state parameter.

    Read path — no admin-key required. Provider must be 'exchange' or 'gmail'.
    """
    if provider not in ("exchange", "gmail"):
        raise HTTPException(status_code=422, detail=f"Unsupported OAuth provider: {provider}")

    source = db.query(IngestionSource).filter_by(id=source_id).first()
    if source is None or source.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Source not found")

    # Prune expired state entries
    _prune_oauth_state()

    # Generate CSRF state token
    state = str(uuid4())
    _OAUTH_STATE[state] = (source_id, time.time() + _OAUTH_STATE_TTL_SECONDS)

    # Build authorize URL
    redirect_uri = "http://localhost:8000/api/ingestion/oauth/callback"

    if provider == "exchange":
        client_id = os.environ.get("INGESTION_PROVIDER_microsoft_CLIENT_ID", "")
        config_json = source.config_json or {}
        tenant_id = config_json.get("tenant_id", "common")
        authorize_url = (
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize"
            f"?client_id={client_id}"
            f"&response_type=code"
            f"&redirect_uri={redirect_uri}"
            f"&scope=offline_access+Mail.Read"
            f"&state={state}"
        )
    else:  # gmail
        client_id = os.environ.get("INGESTION_PROVIDER_google_CLIENT_ID", "")
        authorize_url = (
            f"https://accounts.google.com/o/oauth2/v2/auth"
            f"?client_id={client_id}"
            f"&response_type=code"
            f"&redirect_uri={redirect_uri}"
            f"&scope=https://www.googleapis.com/auth/gmail.readonly"
            f"&access_type=offline"
            f"&prompt=consent"
            f"&state={state}"
        )

    return {"authorize_url": authorize_url, "state": state}


@ingestion_router.post("/oauth/callback")
async def oauth_callback(
    body: OAuthCallbackRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Exchange OAuth2 authorization code for tokens. Mutating — admin-key gated.

    CSRF state validation (RFC 6749 section 10.12): rejects 400 if state
    missing from _OAUTH_STATE or mismatched source_id.
    """
    # Prune expired state entries
    _prune_oauth_state()

    # CSRF state validation
    if body.state not in _OAUTH_STATE:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    stored_source_id, expires_at = _OAUTH_STATE[body.state]
    if stored_source_id != body.source_id:
        raise HTTPException(status_code=400, detail="OAuth state source_id mismatch")

    # One-shot consumption
    _OAUTH_STATE.pop(body.state, None)

    source = db.query(IngestionSource).filter_by(id=body.source_id).first()
    if source is None or source.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Source not found")

    if body.provider not in ("exchange", "gmail"):
        raise HTTPException(status_code=422, detail=f"Unsupported provider: {body.provider}")

    redirect_uri = body.redirect_uri or "http://localhost:8000/api/ingestion/oauth/callback"

    # Exchange code for tokens
    if body.provider == "exchange":
        client_id = os.environ.get("INGESTION_PROVIDER_microsoft_CLIENT_ID", "")
        client_secret = os.environ.get("INGESTION_PROVIDER_microsoft_CLIENT_SECRET", "")
        config_json = source.config_json or {}
        tenant_id = config_json.get("tenant_id", "common")

        async with httpx.AsyncClient() as client:
            token_resp = await client.post(
                f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": body.code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                    "scope": "offline_access Mail.Read",
                },
            )
    else:  # gmail
        client_id = os.environ.get("INGESTION_PROVIDER_google_CLIENT_ID", "")
        client_secret = os.environ.get("INGESTION_PROVIDER_google_CLIENT_SECRET", "")

        async with httpx.AsyncClient() as client:
            token_resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": body.code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
            )

    if token_resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Token exchange failed: {token_resp.status_code}",
        )

    token_data = token_resp.json()
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        raise HTTPException(
            status_code=502,
            detail="No refresh_token in token response",
        )

    # Atomic side effects (Chunk 57 CP9 step 2)
    name_upper = source.name.upper().replace("-", "_").replace(" ", "_")
    env_key = f"INGESTION_SOURCE_{name_upper}_REFRESH_TOKEN"

    # 1. Persist refresh token to .env
    _persist_env_key(env_key, refresh_token)

    # 2. Stamp refresh_token_env onto config_json
    config_json = dict(source.config_json or {})
    config_json["refresh_token_env"] = env_key
    source.config_json = config_json

    # 3. Flip status to ready
    source.status = IngestionSourceStatus.ready.value

    db.commit()

    # 4. Register APScheduler job if schedule_enabled
    if config_json.get("schedule_enabled", False):
        try:
            scheduler = getattr(request.app.state, "scheduler", None)
            if scheduler is not None:
                from src.api.main import _run_ingestion_cycle

                schedule_mode = config_json.get("schedule_mode", "interval")
                interval_hours = config_json.get("schedule_interval_hours", 1.0)

                if schedule_mode == "one_time":
                    from apscheduler.triggers.date import DateTrigger
                    trigger = DateTrigger(run_date=datetime.now(timezone.utc))
                else:
                    from apscheduler.triggers.interval import IntervalTrigger
                    trigger = IntervalTrigger(hours=interval_hours)

                scheduler.add_job(
                    _run_ingestion_cycle,
                    trigger=trigger,
                    id=f"ingestion_source:{source.id}",
                    args=[str(source.id)],
                    replace_existing=True,
                )
        except Exception as exc:
            logger.warning("oauth_callback_scheduler_failed", error=str(exc))

    return {
        "source_id": str(source.id),
        "status": source.status,
        "refresh_token_env": env_key,
    }


# --- Internal helpers ---

def _persist_env_key(key: str, value: str) -> None:
    """Persist a key=value pair to .env file.

    Precedent: ``src/shared/llm_provider.py:update_env_api_key()``.
    """
    env_path = Path(__file__).resolve().parents[2] / ".env"
    lines: list[str] = []
    replaced = False

    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                if line.startswith(f"{key}="):
                    lines.append(f"{key}={value}\n")
                    replaced = True
                else:
                    lines.append(line)

    if not replaced:
        lines.append(f"{key}={value}\n")

    with open(env_path, "w") as f:
        f.writelines(lines)

    # Also set in current process environment
    os.environ[key] = value


def _patch_discovery_ingestion(deployment_path: str | None) -> None:
    """Update the ingestion.deployment_path in discovery.yaml.

    Section-scoped merge helper mirroring ``src/api/seed_routes.py:162–176``
    ``_update_industry_profile()`` pattern: ``yaml.safe_load()`` → mutate →
    ``yaml.dump()``. Does NOT use ``write_llm_config_to_yaml()``.
    (security-posture §39.7)
    """
    yaml_path = Path(__file__).resolve().parent.parent.parent / "config" / "discovery.yaml"
    with open(yaml_path) as f:
        data = yaml.safe_load(f) or {}

    if "ingestion" not in data:
        data["ingestion"] = {}
    data["ingestion"]["deployment_path"] = deployment_path

    with open(yaml_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


# ---------------------------------------------------------------------------
# Email Extraction Bridge spawn route (Chunk 79, D508 / D246 mirror)
# MUST NOT import src.extraction.extraction_bridge — spawn via subprocess.
# D356 capture-the-why: invariant = D246 CLI-only; carve-out = spawn route;
# authorization = D508.
# ---------------------------------------------------------------------------

# In-flight extraction bridge tracking for 409 concurrent-trigger protection.
_IN_FLIGHT_EXTRACT: dict[str, subprocess.Popen] = {}


def _wait_and_clear_extract_inflight(key: str, proc: subprocess.Popen) -> None:
    """Background thread: wait for bridge subprocess to exit, then clear lock."""
    proc.wait()
    _IN_FLIGHT_EXTRACT.pop(key, None)


class ExtractBridgeRequest(BaseModel):
    """Request body for POST /api/ingestion/extract (D508)."""

    source_id: UUID | None = Field(default=None, description="Filter to a specific ingestion source.")
    limit: int | None = Field(default=None, description="Max emails to process.")
    skip_privileged: bool = Field(default=False, description="Skip emails with |privileged| tag.")


def _build_extract_bridge_argv(
    source_id: UUID | None = None,
    limit: int | None = None,
    skip_privileged: bool = False,
) -> list[str]:
    """Build subprocess argv for the extraction bridge CLI (D476 contract-testable)."""
    cmd = [sys.executable, "-m", "src.extraction.extraction_bridge", "run"]
    if source_id:
        cmd.extend(["--source-id", str(source_id)])
    if limit is not None:
        cmd.extend(["--limit", str(limit)])
    if skip_privileged:
        cmd.append("--skip-privileged")
    return cmd


@ingestion_router.post("/extract", status_code=202)
async def trigger_extract(
    body: ExtractBridgeRequest,
    request: Request,
):
    """Spawn the email extraction bridge as a CLI subprocess (D508, D246 mirror).

    Returns 202 + {job_id, pid}. 409 if a bridge subprocess is already in flight.
    Mutating; admin-key when GRACE_ADMIN_KEY set; loopback bypass otherwise.
    """
    _require_admin_key_ingestion(request)

    lock_key = f"extract:{body.source_id or 'all'}"

    # Check in-flight
    existing = _IN_FLIGHT_EXTRACT.get(lock_key)
    if existing and existing.poll() is None:
        raise HTTPException(
            status_code=409,
            detail=f"Extraction bridge already in flight for {lock_key}",
        )

    job_id = str(uuid4())
    cmd = _build_extract_bridge_argv(
        source_id=body.source_id,
        limit=body.limit,
        skip_privileged=body.skip_privileged,
    )

    try:
        proc = subprocess.Popen(  # noqa: S603 — known argv; not user-editable shell
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to spawn bridge: {exc}")

    _IN_FLIGHT_EXTRACT[lock_key] = proc

    # Background thread to release lock when child exits
    t = threading.Thread(
        target=_wait_and_clear_extract_inflight,
        args=(lock_key, proc),
        daemon=True,
    )
    t.start()

    return JSONResponse(
        status_code=202,
        content={"job_id": job_id, "pid": proc.pid},
    )


# ---------------------------------------------------------------------------
# Thread Reconstruction spawn route (Chunk 80a, D513 / D246 mirror)
# MUST NOT import src.ingestion.communications.thread_reconstructor or
# src.ingestion.communications.supersession — spawn via subprocess.
# D356 capture-the-why: invariant = D246 CLI-only; carve-out = spawn route;
# authorization = D513.
# ---------------------------------------------------------------------------

_IN_FLIGHT_RECONSTRUCT: dict[str, subprocess.Popen] = {}


def _wait_and_clear_reconstruct_inflight(key: str, proc: subprocess.Popen) -> None:
    """Background thread: wait for thread reconstruction subprocess to exit, then clear lock."""
    proc.wait()
    _IN_FLIGHT_RECONSTRUCT.pop(key, None)


class ReconstructThreadsRequest(BaseModel):
    """Request body for POST /api/ingestion/reconstruct-threads (D513)."""

    source_id: UUID | None = Field(default=None, description="Filter to a specific ingestion source.")
    limit: int | None = Field(default=None, description="Max events to process.")
    reprocess: bool = Field(default=False, description="Reprocess already-threaded events.")


def _build_reconstruct_threads_argv(
    source_id: UUID | None = None,
    limit: int | None = None,
    reprocess: bool = False,
) -> list[str]:
    """Build subprocess argv for the thread reconstructor CLI (D476 contract-testable)."""
    cmd = [sys.executable, "-m", "src.ingestion.communications.thread_reconstructor", "run"]
    if source_id:
        cmd.extend(["--source-id", str(source_id)])
    if limit is not None:
        cmd.extend(["--limit", str(limit)])
    if reprocess:
        cmd.append("--reprocess")
    return cmd


@ingestion_router.post("/reconstruct-threads", status_code=202)
async def trigger_reconstruct_threads(
    body: ReconstructThreadsRequest,
    request: Request,
):
    """Spawn the thread reconstructor as a CLI subprocess (D513, D246 mirror).

    Returns 202 + {job_id, pid}. 409 if a reconstruction subprocess is already in flight.
    Mutating; admin-key when GRACE_ADMIN_KEY set; loopback bypass otherwise.
    """
    _require_admin_key_ingestion(request)

    lock_key = f"reconstruct:{body.source_id or 'all'}"

    # Check in-flight
    existing = _IN_FLIGHT_RECONSTRUCT.get(lock_key)
    if existing and existing.poll() is None:
        raise HTTPException(
            status_code=409,
            detail=f"Thread reconstruction already in flight for {lock_key}",
        )

    job_id = str(uuid4())
    cmd = _build_reconstruct_threads_argv(
        source_id=body.source_id,
        limit=body.limit,
        reprocess=body.reprocess,
    )

    try:
        proc = subprocess.Popen(  # noqa: S603 — known argv; not user-editable shell
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to spawn thread reconstructor: {exc}")

    _IN_FLIGHT_RECONSTRUCT[lock_key] = proc

    # Background thread to release lock when child exits
    t = threading.Thread(
        target=_wait_and_clear_reconstruct_inflight,
        args=(lock_key, proc),
        daemon=True,
    )
    t.start()

    return JSONResponse(
        status_code=202,
        content={"job_id": job_id, "pid": proc.pid},
    )


# ---------------------------------------------------------------------------
# Bootstrap pipe spawn route (Chunk 80b, D518 / D246 mirror)
# MUST NOT import src.ingestion.communications.corroboration_scorer or
# src.ingestion.communications.bootstrap_pipe — spawn via subprocess.
# D356 capture-the-why: invariant = D246 CLI-only; carve-out = spawn route;
# authorization = D518.
# ---------------------------------------------------------------------------

_IN_FLIGHT_BOOTSTRAP: dict[str, subprocess.Popen] = {}


def _wait_and_clear_bootstrap_inflight(key: str, proc: subprocess.Popen) -> None:
    """Background thread: wait for bootstrap subprocess to exit, then clear lock."""
    proc.wait()
    _IN_FLIGHT_BOOTSTRAP.pop(key, None)


class BootstrapRequest(BaseModel):
    """Request body for POST /api/ingestion/bootstrap (D518)."""

    subset_id: UUID = Field(description="UUID of the curated_email_subsets row to consume.")


def _build_bootstrap_argv(subset_id: UUID) -> list[str]:
    """Build subprocess argv for the bootstrap pipe CLI (D476 contract-testable)."""
    return [
        sys.executable,
        "-m",
        "src.ingestion.communications.bootstrap_pipe",
        "run",
        "--subset-id",
        str(subset_id),
    ]


@ingestion_router.post("/bootstrap", status_code=202)
async def trigger_bootstrap(
    body: BootstrapRequest,
    request: Request,
):
    """Spawn the bootstrap pipe as a CLI subprocess (D518, D246 mirror).

    Returns 202 + {job_id, pid}. 409 if a bootstrap subprocess is already in flight.
    Mutating; admin-key when GRACE_ADMIN_KEY set; loopback bypass otherwise.
    """
    _require_admin_key_ingestion(request)

    lock_key = f"bootstrap:{body.subset_id}"

    # Check in-flight
    existing = _IN_FLIGHT_BOOTSTRAP.get(lock_key)
    if existing and existing.poll() is None:
        raise HTTPException(
            status_code=409,
            detail="Bootstrap already in progress",
        )

    job_id = str(uuid4())
    cmd = _build_bootstrap_argv(body.subset_id)

    try:
        proc = subprocess.Popen(  # noqa: S603 — known argv; not user-editable shell
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to spawn bootstrap pipe: {exc}")

    _IN_FLIGHT_BOOTSTRAP[lock_key] = proc

    # Background thread to release lock when child exits
    t = threading.Thread(
        target=_wait_and_clear_bootstrap_inflight,
        args=(lock_key, proc),
        daemon=True,
    )
    t.start()

    return JSONResponse(
        status_code=202,
        content={"job_id": job_id, "pid": proc.pid},
    )
