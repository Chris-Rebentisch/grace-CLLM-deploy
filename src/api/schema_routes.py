"""FastAPI endpoints for schema extraction and merge operations."""

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/discovery/schema", tags=["schema"])


# --- Request models ---


class ExtractRequest(BaseModel):
    """Request body for schema extraction."""

    dry_run: bool = Field(default=False, description="Build prompts only, no LLM calls")
    domains: list[str] | None = Field(default=None, description="Specific domains to process")
    passes: list[str] | None = Field(default=None, description="Specific passes to run")


class MergeRequest(BaseModel):
    """Request body for schema merge."""

    extraction_run_id: str | None = Field(
        default=None,
        description="SchemaExtractionRun ID. If omitted, uses most recent completed run.",
    )
    dry_run: bool = Field(default=False, description="Skip LLM calls in merge")


# --- Helpers ---


def _get_schema_runs() -> dict:
    from src.discovery.schema_extractor import _schema_runs
    return _schema_runs


def _get_merge_runs() -> dict:
    from src.discovery.schema_merge import _schema_merge_runs
    return _schema_merge_runs


async def _run_extraction_background(
    dry_run: bool,
    domains: list[str] | None,
    passes: list[str] | None,
    run_id: str | None = None,
) -> None:
    from src.discovery.schema_extractor import run_schema_extraction
    # Pass run_id so the executing run is the same object the frontend polls
    # (otherwise the placeholder run never completes and the UI times out).
    await run_schema_extraction(
        dry_run=dry_run, domains=domains, passes=passes, run_id=run_id
    )


async def _run_merge_background(
    extraction_run_id: str | None, dry_run: bool, run_id: str | None = None
) -> None:
    from src.discovery.schema_merge import run_schema_merge
    # Pass run_id so the executing merge run is the same object the frontend polls.
    await run_schema_merge(
        extraction_run_id=extraction_run_id, dry_run=dry_run, run_id=run_id
    )


# --- Endpoints ---


@router.post("/extract")
async def extract_schema(
    background_tasks: BackgroundTasks,
    request: ExtractRequest | None = None,
) -> dict:
    """Trigger schema extraction pipeline."""
    req = request or ExtractRequest()

    if req.dry_run:
        from src.discovery.schema_extractor import run_schema_extraction
        result = await run_schema_extraction(
            dry_run=True, domains=req.domains, passes=req.passes
        )
        return {
            "status": "completed",
            "run_id": result.run_id,
            "dry_run": True,
            "total_entity_types": result.total_entity_types,
            "total_relationships": result.total_relationships,
        }

    from src.discovery.schema_models import SchemaExtractionRun
    from src.discovery.schema_extractor import _schema_runs
    run = SchemaExtractionRun()
    _schema_runs[run.run_id] = run

    background_tasks.add_task(
        _run_extraction_background, False, req.domains, req.passes, run.run_id
    )
    return {
        "status": "started",
        "run_id": run.run_id,
        "message": "Schema extraction started. Check GET /api/discovery/schema/extraction-status/{run_id}",
    }


@router.get("/extraction-status/{run_id}")
async def extraction_status(run_id: str) -> dict:
    """Return status of a schema extraction run."""
    runs = _get_schema_runs()
    run = runs.get(run_id)
    if run is None:
        return {"error": "Run not found", "run_id": run_id}
    return run.model_dump(mode="json")


@router.post("/merge")
async def merge_schema(
    background_tasks: BackgroundTasks,
    request: MergeRequest | None = None,
) -> dict:
    """Trigger schema merge pipeline."""
    req = request or MergeRequest()

    if req.dry_run:
        from src.discovery.schema_merge import run_schema_merge
        result = await run_schema_merge(
            extraction_run_id=req.extraction_run_id, dry_run=True
        )
        return {
            "status": result.status,
            "run_id": result.run_id,
            "dry_run": True,
            "merged_entity_types": result.merged_entity_types,
            "merged_relationships": result.merged_relationships,
        }

    from src.discovery.schema_merge_models import SchemaMergeRun
    from src.discovery.schema_merge import _schema_merge_runs
    run = SchemaMergeRun(extraction_run_id=req.extraction_run_id or "")
    _schema_merge_runs[run.run_id] = run

    background_tasks.add_task(
        _run_merge_background, req.extraction_run_id, False, run.run_id
    )
    return {
        "status": "started",
        "run_id": run.run_id,
        "message": "Schema merge started. Check GET /api/discovery/schema/merge-status/{run_id}",
    }


@router.get("/merge-status/{run_id}")
async def merge_status(run_id: str) -> dict:
    """Return status of a schema merge run."""
    runs = _get_merge_runs()
    run = runs.get(run_id)
    if run is None:
        return {"error": "Run not found", "run_id": run_id}
    return run.model_dump(mode="json")


@router.get("/seed-schema/{run_id}")
async def get_seed_schema(run_id: str) -> dict:
    """Return the full SeedSchema JSON for a completed merge run."""
    runs = _get_merge_runs()
    run = runs.get(run_id)
    if run is None:
        return {"error": "Run not found", "run_id": run_id}
    if run.seed_schema_json is None:
        return {"error": "Seed schema not yet available", "status": run.status}
    return run.seed_schema_json


@router.get("/coverage/{run_id}")
async def get_coverage(run_id: str) -> dict:
    """Return the CQ coverage matrix from a completed merge run."""
    runs = _get_merge_runs()
    run = runs.get(run_id)
    if run is None:
        return {"error": "Run not found", "run_id": run_id}
    if run.seed_schema_json is None:
        return {"error": "Coverage not yet available", "status": run.status}
    return {"coverage_matrix": run.seed_schema_json.get("coverage_matrix", [])}


@router.get("/provenance/{run_id}")
async def get_provenance(run_id: str) -> dict:
    """Return provenance distribution and quality metrics."""
    runs = _get_merge_runs()
    run = runs.get(run_id)
    if run is None:
        return {"error": "Run not found", "run_id": run_id}
    return {
        "provenance_distribution": run.provenance_distribution,
        "richness_distribution": run.richness_distribution,
        "cq_coverage_rate": run.cq_coverage_rate,
        "cross_pass_agreement_rate": run.cross_pass_agreement_rate,
    }
