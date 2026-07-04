"""GrACE FastAPI application."""

import os
import sys

# D205 follow-up (Defect 6): enforce airgap before any import chain can
# pull in `sentence_transformers` or `huggingface_hub`. Both libraries
# check their respective env vars on first model load; without these,
# they contact HuggingFace (CloudFront/S3) to verify the cached model
# revision even when the model is already on disk. This violates EC-7.
# setdefault preserves any operator override (e.g., a bootstrap script
# that must populate the cache).
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest, make_asgi_app
from starlette.middleware.cors import CORSMiddleware

from src.analytics.graph_health_exporter import graph_health_exporter_task
from src.analytics.otel_setup import setup_otel
from src.api.analytics_routes import router as analytics_router
from src.api.calibration_routes import router as calibration_router
from src.api.decomposition_routes import router as decomposition_router
from src.api.discovery_routes import router as discovery_router
from src.api.elicitation_routes import router as elicitation_router
from src.api.extraction_routes import router as extraction_router
from src.api.connectors_routes import router as connectors_router
from src.api.federation_routes import router as federation_router
from src.api.feedback_routes import router as feedback_router
from src.api.llm_config_routes import router as llm_config_router
from src.api.merge_routes import router as merge_router
from src.api.schema_routes import router as schema_router
from src.api.ontology_routes import router as ontology_router
from src.api.claim_routes import router as claim_router
from src.api.cq_test_routes import router as cq_test_router
from src.api.review_routes import router as review_router
from src.api.graph_routes import router as graph_router
from src.api.management_routes import router as management_router
from src.api.recon_routes import (
    documented_reality_router,
    documented_reality_schedule_router,
    divergence_map_router,
    router as recon_router,
)
from src.api.permissions_routes import router as permissions_router
from src.api.proposal_routes import router as proposal_router
from src.api.regeneration_routes import router as regeneration_router
from src.api.retrieval_routes import router as retrieval_router
from src.api.sensitivity_routes import router as sensitivity_router
from src.api.support_routes import router as support_router
from src.api.daemon_routes import router as daemon_router
from src.api.auth_middleware import AuthMiddleware
from src.api.scope_middleware import GraphScopeMiddleware
from src.permissions.api_middleware import PermissionMatrixMiddleware
from src.api.communications_routes import communications_router
from src.api.ingestion_routes import ingestion_router
from src.api.seed_routes import router as seed_router
from src.api.session_routes import router as session_router
from src.change_directives.routes import router as change_directive_router
from src.graph.arcade_client import get_arcade_client

import structlog


logger = structlog.get_logger()


def _parse_cors_origins() -> list[str]:
    """Parse GRACE_CORS_ORIGINS env var into an allowlist (D238).

    Comma-separated, whitespace-tolerant, empty entries filtered.
    Returns the dev fallback (and emits a structlog WARN) when unset.
    """
    raw = os.environ.get("GRACE_CORS_ORIGINS", "")
    parsed = [o.strip() for o in raw.split(",") if o.strip()]
    if not parsed:
        logger.warning("cors.unset_using_dev_default")
        return ["http://localhost:3000", "http://127.0.0.1:3000"]
    return parsed


def _start_scheduler():
    """Construct + start the APScheduler ``BackgroundScheduler`` for
    Documented Reality Reports (D287) and Ingestion scheduling (D425).
    Returns the scheduler or ``None`` if APScheduler is unavailable /
    table is absent.

    D287 + D425 are the explicit, documented exceptions to D246
    (out-of-process CLI scheduling). Single-process invariant: see
    ``security-posture.md`` §21.2 — operator runs a single uvicorn
    worker per environment.
    """
    try:
        from apscheduler.executors.pool import ThreadPoolExecutor
        from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        logger.warning("apscheduler.unavailable")
        return None

    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg2://localhost/grace",
    )
    try:
        scheduler = BackgroundScheduler(
            jobstores={
                "default": SQLAlchemyJobStore(
                    url=db_url,
                    tablename="apscheduler_jobs",
                    engine_options={"pool_pre_ping": True},
                ),
            },
            executors={"default": ThreadPoolExecutor(max_workers=1)},
            job_defaults={
                "coalesce": True,
                "misfire_grace_time": 86400,
                "max_instances": 1,
            },
        )
        scheduler.start()
        logger.info("scheduler.started")
        return scheduler
    except Exception as exc:  # noqa: BLE001
        try:
            from sqlalchemy.engine.url import make_url
            db_host = make_url(db_url).host
        except Exception:  # noqa: BLE001
            db_host = None
        logger.error(
            "scheduler.start_failed",
            error=str(exc),
            db_host=db_host,
            exc_info=True,
        )
        return None


def _register_ingestion_jobs(scheduler, db_session) -> None:
    """Idempotent re-registration of ingestion jobs from ready+enabled sources (D425).

    Job body: subprocess.Popen cycle CLI (D246 bridge — NOT in-process import).
    Job ID convention: ``ingestion_source:{source_id}``.
    """
    jobs_attempted = 0
    try:
        from src.ingestion.models import IngestionSource
        rows = (
            db_session.query(IngestionSource)
            .filter(
                IngestionSource.enabled.is_(True),
                IngestionSource.deleted_at.is_(None),
                IngestionSource.status == "ready",
            )
            .all()
        )
        for source in rows:
            config_json = source.config_json or {}
            if not config_json.get("schedule_enabled", False):
                continue

            jobs_attempted += 1
            job_id = f"ingestion_source:{source.id}"
            schedule_mode = config_json.get("schedule_mode", "interval")
            interval_hours = config_json.get("schedule_interval_hours", 1.0)

            if schedule_mode == "one_time":
                from apscheduler.triggers.date import DateTrigger
                from datetime import datetime, timezone
                trigger = DateTrigger(run_date=datetime.now(timezone.utc))
            else:
                from apscheduler.triggers.interval import IntervalTrigger
                trigger = IntervalTrigger(hours=interval_hours)

            scheduler.add_job(
                _run_ingestion_cycle,
                trigger=trigger,
                id=job_id,
                args=[str(source.id)],
                replace_existing=True,
            )
            logger.info("ingestion_job_registered", source_id=str(source.id), job_id=job_id)

    except Exception as exc:  # noqa: BLE001
        logger.error(
            "ingestion_job_registration_failed",
            error=str(exc),
            jobs_attempted=jobs_attempted,
            exc_info=True,
        )


def _warn_on_default_arcade_credentials() -> None:
    """Emit a startup WARNING when ArcadeDB is still on the shipped
    dev-default password. Never logs the password value itself."""
    try:
        from src.shared.config import get_settings

        if get_settings().arcade_password == "gracedev":
            logger.warning(
                "arcade_default_credentials_in_use",
                advice=(
                    "ArcadeDB is using the shipped dev-default credentials; "
                    "rotate ARCADE_PASSWORD before any non-localhost exposure."
                ),
            )
    except Exception:  # noqa: BLE001 — best-effort startup hygiene check
        pass


async def _probe_embeddings_backend() -> None:
    """Best-effort startup probe for the Ollama embeddings backend.

    Fast (2.5s) GET against ``{ollama_base_url}/api/tags``; logs a
    structlog ERROR when unreachable. Never raises — startup must not
    crash or be materially delayed by an absent Ollama.
    """
    base_url = None
    try:
        import httpx

        from src.shared.config import get_settings

        base_url = str(get_settings().ollama_base_url).rstrip("/")
        async with httpx.AsyncClient(timeout=2.5) as client:
            resp = await client.get(f"{base_url}/api/tags")
            resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — non-blocking best-effort probe
        try:
            logger.error(
                "embeddings_backend_unreachable",
                url=base_url,
                error=str(exc),
                advice=(
                    "Ollama is unreachable; retrieval and entity-resolution "
                    "embeddings require a running Ollama instance."
                ),
            )
        except Exception:  # noqa: BLE001
            pass
        return
    await _probe_embedding_degeneracy()


async def _probe_embedding_degeneracy() -> None:
    """Sanity-check that two distinct names do not embed identically.

    F-006 (validation run ledger, 2026-07-02): a local Ollama nomic-embed-text
    build returned byte-identical vectors for every proper-name-only input,
    silently false-merging 21 of 44 entities on import. The failure mode is
    catastrophic and invisible — this probe makes it loud at startup. Runs
    through the production ``embed_texts`` path (including its F-006
    lowercase normalization) so it validates what callers actually get.
    Best-effort: never raises, never blocks startup materially.
    """
    try:
        from src.shared.config import get_settings
        from src.shared.embeddings import embed_texts

        base_url = str(get_settings().ollama_base_url).rstrip("/")
        vec_a, vec_b = await embed_texts(
            ["Eleanor Vasquez", "Marcus Whitfield"], base_url, timeout=15
        )
        if vec_a and vec_a == vec_b:
            logger.error(
                "embeddings_backend_degenerate",
                advice=(
                    "The embeddings backend returned IDENTICAL vectors for two "
                    "distinct names — entity resolution and ANN dedup will "
                    "false-merge entities. Check the Ollama/nomic-embed-text "
                    "build before ingesting anything (F-006)."
                ),
            )
    except Exception:  # noqa: BLE001 — non-blocking best-effort probe
        pass


def _run_ingestion_cycle(source_id: str) -> None:
    """Spawn ingestion cycle CLI subprocess (D246 bridge — NOT in-process)."""
    import subprocess as _subprocess
    _subprocess.Popen(
        [sys.executable, "-m", "src.ingestion", "cycle", "--source-id", source_id],
        start_new_session=True,
        stdout=_subprocess.DEVNULL,
        stderr=_subprocess.DEVNULL,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup hygiene: warn on shipped dev-default ArcadeDB credentials
    # and probe the Ollama embeddings backend. Both are best-effort and
    # never block or crash startup.
    _warn_on_default_arcade_credentials()
    await _probe_embeddings_backend()

    # F-51 — hydrate the permission enforcer from permission_matrices at boot
    # so a ratified matrix survives a restart (the enforcer previously only
    # rehydrated inside the in-process ratify route — D528 known gap). Without
    # this, post-restart retrieval sensitivity enforcement silently reverted to
    # no-matrix behavior. Best-effort; never blocks startup.
    try:
        from src.permissions.enforcer import hydrate_enforcer_from_db

        hydrate_enforcer_from_db()
    except Exception as exc:  # noqa: BLE001
        logger.warning("enforcer_boot_hydration_failed", error=str(exc))

    stop_event = asyncio.Event()
    exporter_task = asyncio.create_task(
        graph_health_exporter_task(
            client_factory=get_arcade_client,
            interval_seconds=int(
                os.environ.get("GRAPH_HEALTH_EXPORTER_INTERVAL_SECONDS", "60")
            ),
            topn=int(os.environ.get("GRAPH_HEALTH_EXPORTER_TOPN", "20")),
            stop_event=stop_event,
        ),
        name="graph_health_exporter",
    )

    # D287 + D425 — APScheduler for Documented Reality Reports and
    # Ingestion scheduling. Uses ``create_tables=False``; migration
    # ``c37b`` is the schema authority. Best-effort: if the table is
    # missing or APScheduler is unavailable, lifespan continues without.
    scheduler = _start_scheduler()
    app.state.scheduler = scheduler
    # Backwards-compat alias (D287 consumers)
    app.state.documented_reality_scheduler = scheduler

    # D425 — register ingestion jobs from ready+enabled sources
    if scheduler is not None:
        try:
            from src.shared.database import get_session_factory
            db_session = get_session_factory()()
            try:
                _register_ingestion_jobs(scheduler, db_session)
            finally:
                db_session.close()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "ingestion_job_registration_startup_failed",
                error=str(exc),
                exc_info=True,
            )

    try:
        yield
    finally:
        stop_event.set()
        try:
            await asyncio.wait_for(exporter_task, timeout=5.0)
        except asyncio.TimeoutError:
            exporter_task.cancel()
        if scheduler is not None:
            try:
                scheduler.shutdown(wait=False)
                logger.info("scheduler.stopped")
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "scheduler.shutdown_failed",
                    error=str(exc),
                )


app = FastAPI(title="GrACE API", version="0.1.0", lifespan=lifespan)
# Middleware registration order matters. Starlette runs middleware in
# reverse-registration order on inbound (last-added is outermost). We
# need inbound order: CORS → Auth → Scope, so we register Scope first,
# Auth second, CORS last. Do not reorder these without updating
# tests/api/test_auth_middleware.py order assertions (Chunk 31, R3).
app.add_middleware(GraphScopeMiddleware)
# Chunk 42 (D334 / R7): PermissionMatrixMiddleware composes AFTER the
# admission tree on inbound. Starlette runs middleware in
# reverse-registration order on inbound, so registering Permission
# BEFORE Auth places it INSIDE Auth (i.e., Auth is consulted first;
# admission errors return 401; only admitted requests reach the
# permission middleware which owns 403 on the permission axis).
app.add_middleware(PermissionMatrixMiddleware)
app.add_middleware(AuthMiddleware)

# Surface the permission-enforcement posture at startup (opt-in; default OFF for
# single-operator/airgap onboarding — see permission_enforcement_enabled()).
from src.permissions.api_middleware import permission_enforcement_enabled as _perm_on

structlog.get_logger().info(
    "permission_enforcement_posture",
    enabled=_perm_on(),
    flag="GRACE_PERMISSION_ENFORCEMENT_ENABLED",
)
# Chunk 31 (D238): hardened CORS policy. Origins are env-driven via
# GRACE_CORS_ORIGINS; falls back to localhost dev origins when unset
# (with structlog WARN). X-Admin-Key is added to allow_headers so
# the frontend can present the admin key on mutating requests when
# GRACE_ADMIN_KEY is configured.
# Capture-the-why (D356):
# Invariant: default-deny via admin-key (X-Admin-Key header required on
#   mutating routes when GRACE_ADMIN_KEY set) + READONLY_ROUTES allowlist +
#   bearer-token admission (GRACE_REMOTE_ACCESS_ENABLED).
# Carve-out: browser preflight admission for PATCH/DELETE/PUT
#   (9 PATCH + 3 DELETE + 3 legacy PUT routes). D449.
# Authorization: D449 (Chunk 66, CORS allow_methods explicit six-element list).
# Note: the 3 PUT routes are semantically PATCH (partial updates); the
#   PUT→PATCH refactor is deferred housekeeping (spec §14).
app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Accept", "X-Graph-Scope", "X-Admin-Key", "Authorization"],
    max_age=600,
)
@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    """D458: explicit /metrics route bypasses Starlette mount's 307 redirect.
    Invariant: Prometheus scrape via the mount works transparently.
    Carve-out: explicit route for non-redirect-following tools.
    Authorization: D458.

    F-15: when PROMETHEUS_MULTIPROC_DIR is configured, CLI-subprocess counter
    families (D246 pipelines — vision, email extraction, voice, ...) are
    appended from the multiproc aggregation; those families never appear in
    the in-process registry, so the concatenation is collision-free."""
    from src.analytics.subprocess_metrics import multiproc_exposition

    return Response(
        content=generate_latest() + multiproc_exposition(),
        media_type=CONTENT_TYPE_LATEST,
    )

app.mount("/metrics", make_asgi_app())
app.include_router(analytics_router)
app.include_router(calibration_router)
app.include_router(change_directive_router)
app.include_router(connectors_router)
app.include_router(claim_router)
app.include_router(cq_test_router)
app.include_router(decomposition_router)
app.include_router(discovery_router)
app.include_router(elicitation_router)
app.include_router(extraction_router)
app.include_router(federation_router)
app.include_router(feedback_router)
app.include_router(graph_router)
app.include_router(llm_config_router)
app.include_router(management_router)
app.include_router(merge_router)
app.include_router(ontology_router)
app.include_router(permissions_router)
app.include_router(proposal_router)
app.include_router(recon_router)
app.include_router(divergence_map_router)
# Register schedule router BEFORE the parent report router so the more
# specific ``/documented-reality/schedules`` prefix matches before the
# parent ``/documented-reality/{report_id}`` catch-all (Chunk 37, D286).
app.include_router(documented_reality_schedule_router)
app.include_router(documented_reality_router)
app.include_router(regeneration_router)
app.include_router(retrieval_router)
app.include_router(review_router)
app.include_router(sensitivity_router)
app.include_router(support_router)
app.include_router(daemon_router)
app.include_router(schema_router)
app.include_router(communications_router)
app.include_router(ingestion_router)
app.include_router(seed_router)
app.include_router(session_router)

# Defect 1 fix: `FastAPIInstrumentor.instrument_app()` monkey-patches
# `app.build_middleware_stack`, but Starlette builds (and caches) the
# stack lazily on the first ASGI `__call__` --- and under uvicorn the
# lifespan startup event IS the first ASGI call. Running setup_otel
# inside `lifespan()` therefore patches a method that the cached
# stack no longer references, so HTTP metrics never materialize and
# `/metrics/` returns 0 bytes. Call it here, at module top, where it
# runs before any ASGI event. Idempotent; the contract test fixture
# still re-invokes it safely.
setup_otel(app)
