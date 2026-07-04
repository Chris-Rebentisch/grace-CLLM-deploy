"""Eval results writer tests (Chunk 34, D259).

Round-trip an ``EvalRun`` through PostgreSQL and assert that the unique
``(run_id, case_id, metric_name)`` constraint actually fires.

These tests touch the live development database (the same one all other
DB-backed integration tests use). The c34 migration must be applied
before they run.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.eval.deepeval_runner import EvalResult, EvalRun
from src.eval.results_writer import (
    deepeval_results_pg,
    eval_runs_pg,
    write_run,
)
from src.shared.database import get_engine


@pytest.fixture(autouse=True)
def _clean_eval_tables():
    """Wipe eval tables around each test to keep counts deterministic."""
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM deepeval_results"))
        conn.execute(text("DELETE FROM eval_runs"))
        conn.commit()
    yield
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM deepeval_results"))
        conn.execute(text("DELETE FROM eval_runs"))
        conn.commit()


def _run(case_id: str = "c1", metric: str = "faithfulness", score: float = 0.91) -> EvalRun:
    return EvalRun(
        id=uuid4(),
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        status="success",
        triggered_by="cli",
        config_hash="abc1234567890def",
        golden_dataset_hash="0123456789abcdef",
        total_cases=1,
        passed_warn_floor=1,
        passed_fail_floor=1,
        results=[
            EvalResult(
                case_id=case_id,
                query_text="q",
                metric_name=metric,
                metric_score=score,
                passed_warn_floor=True,
                passed_fail_floor=True,
                latency_ms=42,
            )
        ],
    )


def test_write_run_round_trip_via_session():
    """Insert an EvalRun + one result row, commit, then read back and assert."""
    engine = get_engine()
    run = _run()

    with Session(engine) as session:
        write_run(session, run)
        session.commit()

    with engine.connect() as conn:
        runs = conn.execute(eval_runs_pg.select()).fetchall()
        results = conn.execute(deepeval_results_pg.select()).fetchall()

    assert len(runs) == 1
    assert runs[0].id == run.id
    assert runs[0].total_cases == 1
    assert runs[0].config_hash == "abc1234567890def"
    assert len(results) == 1
    assert results[0].case_id == "c1"
    assert results[0].metric_name == "faithfulness"


def test_unique_constraint_violation_on_duplicate_metric():
    """Inserting two results with identical (run_id, case_id, metric_name)
    must raise IntegrityError (uq_deepeval_results_run_case_metric)."""
    engine = get_engine()

    run = _run()
    # Add a duplicate row with same case_id + metric_name as the first.
    run.results.append(
        EvalResult(
            case_id="c1",
            query_text="q",
            metric_name="faithfulness",
            metric_score=0.50,
            passed_warn_floor=False,
            passed_fail_floor=False,
            latency_ms=99,
        )
    )

    with Session(engine) as session:
        with pytest.raises(IntegrityError):
            write_run(session, run)
            session.commit()
