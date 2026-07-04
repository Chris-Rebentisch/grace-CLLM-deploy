"""FastAPI async endpoints for Discovery module."""

from pathlib import Path
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel, Field

logger = structlog.get_logger()

from src.discovery.batch_runner import run_batch
from src.discovery.cq_context import generate_context_summary, generate_domain_context
from src.discovery.cq_database import (
    bulk_create_cqs,
    create_cq,
    get_cluster_members,
    get_cq_summary,
    list_clusters,
    list_cqs,
    update_cq,
    update_cq_status,
)
from src.discovery.cq_models import (
    CQSource,
    CQStatus,
    CQType,
    CompetencyQuestion,
)
from src.discovery.cq_templates import (
    get_templates_by_type,
    get_templates_for_domain,
    load_templates,
    render_template,
    suggest_templates,
)
from src.discovery.cq_generator import (
    get_generation_run,
    request_cancellation,
    run_generation_pipeline,
)
from src.discovery.database import get_processing_summary
from src.discovery.ollama_client import check_ollama_health
from src.discovery.source_scanner import browse_path, configure_sources, scan_sources
from src.shared.database import get_db

router = APIRouter(prefix="/api/discovery", tags=["discovery"])


class ConfigureSourcesRequest(BaseModel):
    """Request body for the configure-sources endpoint."""

    selected_paths: list[str] = Field(description="Directory paths the user selected")
    file_type_filters: list[str] | None = Field(
        default=None,
        description="File extensions to include. Defaults to discovery.yaml supported_extensions",
    )


def _run_batch_sync(manifest_path: str | None, source_dir: str | None) -> None:
    """Wrapper to run batch processing synchronously (for BackgroundTasks)."""
    run_batch(
        manifest_path=Path(manifest_path) if manifest_path else None,
        source_dir=Path(source_dir) if source_dir else None,
    )


@router.get("/scan-sources")
async def scan_sources_endpoint(root_dir: str | None = None) -> list[dict]:
    """Scan filesystem and return available source directories with metadata."""
    root = Path(root_dir) if root_dir else None
    return scan_sources(root_dir=root)


@router.get("/browse")
async def browse_path_endpoint(path: str | None = None) -> dict:
    """List immediate folders + files under ``path`` for the in-app file browser.

    Read-only directory listing of the local filesystem (single-user local app).
    Defaults to the user's home directory when ``path`` is omitted.
    """
    return browse_path(path)


@router.post("/configure-sources")
async def configure_sources_endpoint(request: ConfigureSourcesRequest) -> dict:
    """Receive selected directories, generate manifest, return summary."""
    return configure_sources(
        selected_paths=request.selected_paths,
        supported_extensions=request.file_type_filters,
    )


@router.post("/process")
async def process_documents_endpoint(
    background_tasks: BackgroundTasks,
    manifest_path: str | None = None,
    source_dir: str | None = None,
) -> dict:
    """Trigger document processing as a background task. Returns immediately."""
    background_tasks.add_task(_run_batch_sync, manifest_path, source_dir)
    return {
        "status": "started",
        "message": "Processing started. Check GET /api/discovery/status for progress.",
    }


@router.get("/status")
async def processing_status_endpoint() -> dict:
    """Return the current processing summary from the database."""
    db_gen = get_db()
    db = next(db_gen)
    try:
        return get_processing_summary(db)
    finally:
        try:
            next(db_gen)
        except StopIteration:
            pass


# --- CQ Endpoints ---


class UpdateCQStatusRequest(BaseModel):
    """Request body for updating CQ status."""

    status: str = Field(description="New lifecycle status")


class RenderTemplateRequest(BaseModel):
    """Request body for rendering a CQ template."""

    template_id: str = Field(description="Template ID to render")
    values: dict[str, str] = Field(description="Placeholder values")


class SuggestTemplatesRequest(BaseModel):
    """Request body for suggesting templates from raw input."""

    raw_input: str = Field(description="Raw user brainstorm text")


def _get_db_session():
    """Helper to get a db session and return (session, generator)."""
    db_gen = get_db()
    return next(db_gen), db_gen


def _close_db(db_gen):
    """Helper to close a db generator."""
    try:
        next(db_gen)
    except StopIteration:
        pass


@router.get("/cqs")
async def list_cqs_endpoint(
    status: str | None = None,
    domain: str | None = None,
    source: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """List competency questions with optional filters."""
    db, db_gen = _get_db_session()
    try:
        cq_status = CQStatus(status) if status else None
        cq_source = CQSource(source) if source else None
        cqs = list_cqs(db, status=cq_status, domain=domain, source=cq_source, limit=limit)
        return [cq.model_dump(mode="json") for cq in cqs]
    finally:
        _close_db(db_gen)


@router.post("/cqs")
async def create_cq_endpoint(cq: CompetencyQuestion) -> dict:
    """Create a new competency question."""
    db, db_gen = _get_db_session()
    try:
        created = create_cq(db, cq)
        return created.model_dump(mode="json")
    finally:
        _close_db(db_gen)


@router.put("/cqs/{cq_id}")
async def update_cq_endpoint(cq_id: str, updates: dict) -> dict:
    """Update specific fields of a CQ."""
    db, db_gen = _get_db_session()
    try:
        updated = update_cq(db, UUID(cq_id), updates)
        if updated is None:
            return {"error": "CQ not found"}
        return updated.model_dump(mode="json")
    finally:
        _close_db(db_gen)


@router.put("/cqs/{cq_id}/status")
async def update_cq_status_endpoint(cq_id: str, request: UpdateCQStatusRequest) -> dict:
    """Update the lifecycle status of a CQ."""
    db, db_gen = _get_db_session()
    try:
        updated = update_cq_status(db, UUID(cq_id), CQStatus(request.status))
        if updated is None:
            return {"error": "CQ not found"}
        return updated.model_dump(mode="json")
    finally:
        _close_db(db_gen)


@router.post("/cqs/bulk")
async def bulk_create_cqs_endpoint(cqs: list[CompetencyQuestion]) -> dict:
    """Bulk insert competency questions."""
    db, db_gen = _get_db_session()
    try:
        created = bulk_create_cqs(db, cqs)
        return {"created": len(created)}
    finally:
        _close_db(db_gen)


@router.get("/cqs/summary")
async def cq_summary_endpoint() -> dict:
    """Return CQ summary counts."""
    db, db_gen = _get_db_session()
    try:
        return get_cq_summary(db)
    finally:
        _close_db(db_gen)


# --- Context Reinstatement Endpoints ---


@router.get("/context")
async def context_summary_endpoint() -> dict:
    """Return the document corpus summary for CQ priming (cognitive reinstatement)."""
    db, db_gen = _get_db_session()
    try:
        return generate_context_summary(db)
    finally:
        _close_db(db_gen)


@router.get("/context/{domain}")
async def domain_context_endpoint(domain: str) -> dict:
    """Return detailed context for a specific domain."""
    db, db_gen = _get_db_session()
    try:
        return generate_domain_context(db, domain)
    finally:
        _close_db(db_gen)


# --- Template Endpoints ---


@router.get("/templates")
async def list_templates_endpoint(
    domain: str | None = None, cq_type: str | None = None
) -> list[dict]:
    """List CQ templates, optionally filtered by domain or type."""
    if domain:
        templates = get_templates_for_domain(domain)
    elif cq_type:
        templates = get_templates_by_type(CQType(cq_type))
    else:
        templates = list(load_templates())
    return [t.model_dump() for t in templates]


@router.post("/templates/render")
async def render_template_endpoint(request: RenderTemplateRequest) -> dict:
    """Render a template with user-provided placeholder values."""
    text = render_template(request.template_id, request.values)
    return {"template_id": request.template_id, "rendered_text": text}


@router.post("/templates/suggest")
async def suggest_templates_endpoint(request: SuggestTemplatesRequest) -> list[dict]:
    """Given raw user brainstorm text, suggest relevant templates."""
    templates = suggest_templates(request.raw_input)
    return [t.model_dump() for t in templates]


# --- Cluster Endpoints ---


@router.get("/clusters")
async def list_clusters_endpoint(domain: str | None = None) -> list[dict]:
    """List CQ clusters, optionally filtered by domain."""
    db, db_gen = _get_db_session()
    try:
        clusters = list_clusters(db, domain=domain)
        return [c.model_dump(mode="json") for c in clusters]
    finally:
        _close_db(db_gen)


@router.get("/clusters/{cluster_id}/members")
async def cluster_members_endpoint(cluster_id: str) -> list[dict]:
    """Get all CQs in a cluster."""
    db, db_gen = _get_db_session()
    try:
        members = get_cluster_members(db, UUID(cluster_id))
        return [m.model_dump(mode="json") for m in members]
    finally:
        _close_db(db_gen)


# --- CQ Generation Endpoints ---


class GenerateCQsRequest(BaseModel):
    """Request body for CQ generation."""

    passes: list[str] | None = Field(default=None, description="Passes to run")
    domains: list[str] | None = Field(default=None, description="Domains to process")
    dry_run: bool = Field(default=False, description="Build prompts without calling Ollama")


async def _run_generation_background(
    passes: list[str] | None,
    domains: list[str] | None,
    run_id: str,
) -> None:
    """Background task wrapper for CQ generation.

    Threads ``run_id`` through so the polled run and the executing run are the
    same object (required for status + cancellation), and records any error on
    that run so the frontend stops polling instead of timing out.
    """
    from datetime import UTC, datetime
    from src.discovery.cq_generator import _generation_runs

    try:
        await run_generation_pipeline(passes=passes, domains=domains, run_id=run_id)
    except Exception as exc:  # noqa: BLE001 — surface to the UI, don't swallow
        run = _generation_runs.get(run_id)
        if run is not None:
            run.error = str(exc)
            run.completed_at = datetime.now(UTC)
        logger.exception("cq_generation_background_failed", run_id=run_id, error=str(exc))


@router.post("/generate-cqs")
async def generate_cqs_endpoint(
    background_tasks: BackgroundTasks,
    request: GenerateCQsRequest | None = None,
) -> dict:
    """Trigger CQ generation pipeline. Runs as BackgroundTask."""
    passes = request.passes if request else None
    domains = request.domains if request else None
    dry_run = request.dry_run if request else False

    if dry_run:
        result = await run_generation_pipeline(passes=passes, domains=domains, dry_run=True)
        return {
            "status": "completed",
            "run_id": result.run_id,
            "dry_run": True,
            "pass_results": len(result.pass_results),
        }

    # For real runs, use background task
    from src.discovery.cq_generator import _generation_runs, GenerationRun
    from uuid import uuid4
    from datetime import UTC, datetime

    run = GenerationRun(model="pending")
    _generation_runs[run.run_id] = run

    background_tasks.add_task(_run_generation_background, passes, domains, run.run_id)
    return {
        "status": "started",
        "run_id": run.run_id,
        "message": "CQ generation started. Check GET /api/discovery/generation-status/{run_id}",
    }


@router.get("/generation-status/{run_id}")
async def generation_status_endpoint(run_id: str) -> dict:
    """Return status of a generation run."""
    run = get_generation_run(run_id)
    if run is None:
        return {"error": "Run not found", "run_id": run_id}
    return run.model_dump(mode="json")


@router.post("/generate-cqs/{run_id}/cancel")
async def cancel_generation_endpoint(run_id: str) -> dict:
    """Request early stop of a CQ generation run.

    The in-flight LLM call cannot be interrupted, so the run stops at the next
    pass checkpoint; any CQs already generated are kept. Returns 404 if the run
    is unknown, otherwise the live run state.
    """
    from fastapi import HTTPException

    if not request_cancellation(run_id):
        raise HTTPException(status_code=404, detail="Generation run not found")
    run = get_generation_run(run_id)
    return {
        "status": "cancelling",
        "run_id": run_id,
        "message": "Stop requested. Generation will halt after the current pass.",
        "run": run.model_dump(mode="json") if run is not None else None,
    }


@router.get("/ollama-health")
async def ollama_health_endpoint() -> dict:
    """Check Ollama availability and model status."""
    return await check_ollama_health()


# --- CQ Candidates Endpoints (D227, Chunk 29) ---


@router.get("/cq-candidates")
async def list_cq_candidates_endpoint(
    session_id: str,
    source_origin: str | None = None,
    validation_status: str | None = None,
) -> list[dict]:
    """List CQ candidates for a session, optionally filtered by source/status."""
    from src.discovery.cq_candidates import CQCandidateRecord, list_candidates

    db, db_gen = _get_db_session()
    try:
        rows = list_candidates(
            db,
            UUID(session_id),
            source_origin=source_origin,
            validation_status=validation_status,
        )
        return [
            CQCandidateRecord.model_validate(row).model_dump(mode="json")
            for row in rows
        ]
    finally:
        _close_db(db_gen)


@router.post("/cq-candidates/generate", status_code=202)
async def generate_cq_candidates_endpoint(
    background_tasks: BackgroundTasks,
    request: dict,
) -> dict:
    """Trigger background CQ candidate generation. Returns 202 if accepted, 409 if already in flight."""
    from datetime import datetime as dt, timezone
    from src.discovery.cq_candidates import (
        GenerateCQCandidatesRequest,
        GenerateCQCandidatesResponse,
        _run_generation_sync,
        acquire_generation_lock,
        is_generation_in_flight,
    )
    from fastapi import HTTPException

    parsed = GenerateCQCandidatesRequest.model_validate(request)

    if not acquire_generation_lock(parsed.session_id):
        raise HTTPException(
            status_code=409,
            detail="Generation already in flight for this session",
        )

    task_id = uuid4()
    background_tasks.add_task(
        _run_generation_sync,
        parsed.session_id,
        parsed.segment,
        parsed.source_origin,
    )

    response = GenerateCQCandidatesResponse(
        task_id=task_id,
        accepted_at=dt.now(timezone.utc),
    )
    return response.model_dump(mode="json")
