"""FastAPI endpoints for CQ Test Runner."""

import asyncio
from uuid import UUID

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.ontology.cq_test_models import CQTestResult, CQTestRunStatus
from src.ontology.cq_test_runner import (
    cancel_test_run,
    get_test_run_by_id,
    list_test_runs,
    mark_test_run_failed,
    run_cq_tests,
    run_non_regression_gate,
)
from src.shared.database import get_db

log = structlog.get_logger()

router = APIRouter(prefix="/api/ontology/cq-test", tags=["cq-test"])


# --- Request Models ---


class RunTestsRequest(BaseModel):
    """Request body for running CQ tests."""

    schema_version_id: UUID | None = None
    concurrency: int = 1


class GateRequest(BaseModel):
    """Request body for the non-regression gate."""

    proposed_schema_json: dict
    threshold: float = 0.90
    concurrency: int = 1


# --- Background task helper ---


def _run_tests_background(
    schema_version_id: UUID | None,
    concurrency: int,
    existing_run_id: UUID | None = None,
):
    """Run CQ tests in background. Creates its own DB session."""
    from src.shared.database import get_db

    gen = get_db()
    db = next(gen)
    try:
        asyncio.run(
            run_cq_tests(
                db=db,
                schema_version_id=schema_version_id,
                concurrency=concurrency,
                # F-58 (validation run): reuse the run row already created
                # by the route so the returned run_id IS the executing row.
                existing_run_id=existing_run_id,
            )
        )
    except Exception as e:
        log.error(
            "background_cq_test_failed",
            run_id=str(existing_run_id) if existing_run_id else None,
            error=str(e),
        )
        # F-0035 / ISS-0026 (validation run 2026-07-03): previously the
        # exception was ONLY logged — the run row created by the route stayed
        # `status='running', total_cqs=0` for six hours with no failure
        # propagation (anyone polling waited forever; operator SQL-flipped the
        # row). This is the guarantee: ANY exception in the background task —
        # including ones raised before run_cq_tests reaches its own try block
        # (schema load, CQ read-back, provider construction) — marks the row
        # failed with the error message and completed_at persisted.
        if existing_run_id is not None:
            try:
                # Try directly; roll back and retry only if the session holds a
                # failed transaction (unconditional rollback discards too much).
                try:
                    mark_test_run_failed(db, existing_run_id, str(e))
                except Exception:
                    db.rollback()
                    mark_test_run_failed(db, existing_run_id, str(e))
            except Exception as mark_err:  # never let cleanup raise
                log.error(
                    "background_cq_test_mark_failed_error",
                    run_id=str(existing_run_id),
                    error=str(mark_err),
                )
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


# --- Endpoints ---


@router.post("/run")
async def start_test_run(
    body: RunTestsRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Start a CQ test run. Returns run_id immediately; tests run in background."""
    from src.ontology.cq_test_runner import create_test_run, CQTestRunRow
    from src.ontology.database import get_active_version, get_version_by_id
    from src.ontology.cq_test_models import CQTestRun

    # Determine version
    if body.schema_version_id:
        version = get_version_by_id(db, body.schema_version_id)
        if version is None:
            raise HTTPException(status_code=404, detail="Schema version not found")
        version_id = version.id
        version_number = version.version_number
    else:
        active = get_active_version(db)
        if active is None:
            raise HTTPException(status_code=404, detail="No active schema version")
        version_id = active.id
        version_number = active.version_number

    # Create initial run record
    run = CQTestRun(
        schema_version_id=version_id,
        schema_version_number=version_number,
        status=CQTestRunStatus.RUNNING,
        concurrency=body.concurrency,
    )
    # create_test_run COMMITs the row before returning, and Starlette runs
    # BackgroundTasks only after the response is sent — so the run_id we return
    # always references a committed, immediately-GETtable row (no orphan
    # run_id window; verified for F-0035 / ISS-0026).
    created = create_test_run(db, run)

    # Schedule background execution — thread created.id so the executing run
    # updates the SAME row we return (F-58, validation run).
    background_tasks.add_task(
        _run_tests_background,
        body.schema_version_id,
        body.concurrency,
        created.id,
    )

    return {"run_id": str(created.id), "status": "running"}


@router.post("/{run_id}/cancel")
def cancel_run(run_id: UUID, db: Session = Depends(get_db)):
    """Cancel a running CQ test run.

    F-0035 / ISS-0026 (validation run 2026-07-03): there was no cancel
    endpoint — a stuck run could only be cleared by SQL-flipping the row.
    Path shape follows the existing per-run routes (``GET /{run_id}``,
    ``GET /{run_id}/failures``). Mutating-route auth posture is supplied by the
    global auth middleware, same as the sibling ``POST /run`` / ``POST /gate``
    (X-Admin-Key required when GRACE_ADMIN_KEY is set; loopback bypass
    otherwise) — this route is not on the READONLY_ROUTES allowlist.

    Returns 200 on running → cancelled, 409 if the run is already terminal
    (completed / failed / cancelled), 404 if the run does not exist. The
    in-flight background task observes the flip cooperatively and stops
    issuing LLM calls without overwriting the cancelled status.
    """
    outcome, run = cancel_test_run(db, run_id)
    if outcome == "not_found":
        raise HTTPException(status_code=404, detail="Test run not found")
    if outcome == "conflict":
        raise HTTPException(
            status_code=409,
            detail=f"Test run is already terminal (status={run.status.value})",
        )
    log.info("cq_test_run_cancel_requested", run_id=str(run_id))
    return {"run_id": str(run_id), "status": run.status.value}


@router.get("/history")
def get_history(
    schema_version_id: UUID | None = Query(default=None),
    limit: int = Query(default=20),
    offset: int = Query(default=0),
    db: Session = Depends(get_db),
):
    """List past test runs with summary metrics."""
    runs = list_test_runs(
        db,
        schema_version_id=schema_version_id,
        limit=limit,
        offset=offset,
    )
    # Return summaries without per-CQ results
    return [
        {
            "id": str(r.id),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "status": r.status.value,
            "schema_version_number": r.schema_version_number,
            "total_cqs": r.total_cqs,
            "passing": r.passing,
            "failing": r.failing,
            "out_of_scope": r.out_of_scope,
            "pass_rate": r.pass_rate,
            "duration_ms": r.duration_ms,
        }
        for r in runs
    ]


@router.get("/{run_id}")
def get_run(run_id: UUID, db: Session = Depends(get_db)):
    """Get a full test run with per-CQ results."""
    run = get_test_run_by_id(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Test run not found")
    return run.model_dump(mode="json")


@router.get("/{run_id}/failures")
def get_failures(run_id: UUID, db: Session = Depends(get_db)):
    """Get only the failing CQs from a test run."""
    run = get_test_run_by_id(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Test run not found")
    failures = [r for r in run.results if r.result == CQTestResult.FAIL]
    return [f.model_dump(mode="json") for f in failures]


@router.post("/gate")
async def run_gate(body: GateRequest, db: Session = Depends(get_db)):
    """Run the non-regression quality gate synchronously."""
    try:
        result = await run_non_regression_gate(
            db=db,
            proposed_schema_json=body.proposed_schema_json,
            threshold=body.threshold,
            concurrency=body.concurrency,
        )
        return result.model_dump(mode="json")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
