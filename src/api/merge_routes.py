"""FastAPI endpoints for CQ merge operations."""

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/discovery", tags=["merge"])


class MergeCQsRequest(BaseModel):
    """Request body for merge-cqs endpoint."""

    dry_run: bool = Field(default=False, description="Run Tier 1+2 only, skip Tier 3 LLM calls")


# In-memory reference to merge runs (populated by cq_merge module)
def _get_merge_runs() -> dict:
    from src.discovery.cq_merge import _merge_runs
    return _merge_runs


async def _run_merge_background(dry_run: bool) -> None:
    """Background task wrapper for merge pipeline."""
    from src.discovery.cq_merge import run_merge_pipeline
    await run_merge_pipeline(dry_run=dry_run)


@router.post("/merge-cqs")
async def merge_cqs_endpoint(
    background_tasks: BackgroundTasks,
    request: MergeCQsRequest | None = None,
) -> dict:
    """Trigger CQ merge pipeline."""
    dry_run = request.dry_run if request else False

    if dry_run:
        from src.discovery.cq_merge import run_merge_pipeline
        result = await run_merge_pipeline(dry_run=True)
        return {
            "status": "completed",
            "run_id": result.run_id,
            "dry_run": True,
            "total_clusters": result.total_clusters,
            "total_singletons": result.total_singletons,
        }

    # Start background task
    from src.discovery.cq_merge import MergeRun, _merge_runs
    from src.discovery.merge_models import MergeRun as MergeRunModel
    run = MergeRunModel()
    _merge_runs[run.run_id] = run

    background_tasks.add_task(_run_merge_background, False)
    return {
        "status": "started",
        "run_id": run.run_id,
        "message": "Merge pipeline started. Check GET /api/discovery/merge-status/{run_id}",
    }


@router.get("/merge-latest")
async def merge_latest_endpoint() -> dict:
    """Return the latest completed CQ merge run summary (DB-backed).

    Unlike ``/merge-status/{run_id}`` (in-memory, cleared on restart), this reads
    the persisted ``merge_runs`` table so the onboarding header can show the
    collapsed canonical review-set size across reloads. ``canonical_count`` is
    derived from ``tier3_results_json.canonical_review_set`` (no DB column).
    """
    from src.discovery.cq_database import MergeRunRow
    from src.shared.database import get_db

    db_gen = get_db()
    db = next(db_gen)
    try:
        row = (
            db.query(MergeRunRow)
            .filter(MergeRunRow.status == "completed")
            .order_by(MergeRunRow.completed_at.desc().nullslast())
            .first()
        )
        if row is None:
            return {"has_merge": False}
        t3 = row.tier3_results_json or {}
        canonical = t3.get("canonical_review_set") or []
        return {
            "has_merge": True,
            "run_id": str(row.id),
            "canonical_count": len(canonical),
            "total_cqs_input": row.total_cqs_input or 0,
            "total_gap_fills": row.total_gap_fills or 0,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }
    finally:
        db_gen.close()


@router.get("/merge-status/{run_id}")
async def merge_status_endpoint(run_id: str) -> dict:
    """Return status of a merge run."""
    runs = _get_merge_runs()
    run = runs.get(run_id)
    if run is None:
        return {"error": "Run not found", "run_id": run_id}
    return run.model_dump(mode="json")


@router.get("/merge-results/{run_id}")
async def merge_results_endpoint(run_id: str) -> dict:
    """Return full merge results."""
    runs = _get_merge_runs()
    run = runs.get(run_id)
    if run is None:
        return {"error": "Run not found", "run_id": run_id}
    return run.model_dump(mode="json")


@router.get("/merge-results/{run_id}/hierarchy")
async def merge_hierarchy_endpoint(run_id: str) -> dict:
    """Return the hierarchy JSON from a merge run."""
    runs = _get_merge_runs()
    run = runs.get(run_id)
    if run is None:
        return {"error": "Run not found", "run_id": run_id}
    return run.hierarchy_json or {}


@router.get("/merge-results/{run_id}/gaps")
async def merge_gaps_endpoint(run_id: str) -> dict:
    """Return the gap report from a merge run."""
    runs = _get_merge_runs()
    run = runs.get(run_id)
    if run is None:
        return {"error": "Run not found", "run_id": run_id}
    return run.gap_report_json or {}
