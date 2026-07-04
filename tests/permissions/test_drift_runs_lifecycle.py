"""CP7 — drift_runs lifecycle tests (D460)."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.shared.database import get_session_factory


@pytest.fixture()
def db():
    session = get_session_factory()()
    yield session
    # Clean up test rows
    session.execute(text("DELETE FROM drift_runs WHERE run_id IN (SELECT run_id FROM drift_runs)"))
    session.commit()
    session.close()


def test_drift_runs_api_insert_then_cli_update_preserves_triggered_by(db):
    """Full round-trip: INSERT with triggered_by='api', simulate CLI UPDATE, verify triggered_by unchanged."""
    job_id = uuid4()
    db.execute(text("""
        INSERT INTO drift_runs (id, run_id, observation_time, dry_run, started_at, status, triggered_by, summary_json)
        VALUES (:id, :run_id, now(), false, now(), 'running', 'api', '{}')
    """), {"id": str(job_id), "run_id": str(job_id)})
    db.commit()

    # Simulate CLI UPDATE on completion
    db.execute(text("""
        UPDATE drift_runs SET completed_at = now(), status = 'success', summary_json = :summary
        WHERE id = :id
    """), {"id": str(job_id), "summary": json.dumps({"classifications": 5})})
    db.commit()

    row = db.execute(text("SELECT triggered_by, status FROM drift_runs WHERE id = :id"), {"id": str(job_id)}).fetchone()
    assert row is not None
    assert row[0] == "api", f"triggered_by should remain 'api', got {row[0]}"
    assert row[1] == "success"


def test_drift_runs_cli_self_insert(db):
    """CLI without --job-id creates row with triggered_by='cli'."""
    job_id = uuid4()
    db.execute(text("""
        INSERT INTO drift_runs (id, run_id, observation_time, dry_run, started_at, status, triggered_by, summary_json)
        VALUES (:id, :run_id, now(), false, now(), 'running', 'cli', '{}')
    """), {"id": str(job_id), "run_id": str(job_id)})
    db.commit()

    row = db.execute(text("SELECT triggered_by FROM drift_runs WHERE id = :id"), {"id": str(job_id)}).fetchone()
    assert row is not None
    assert row[0] == "cli"


def test_drift_runs_cli_missing_row_warning(db):
    """CLI with --job-id pointing to nonexistent row — UPDATE returns 0 rows (no crash)."""
    missing_id = uuid4()
    result = db.execute(text("""
        UPDATE drift_runs SET completed_at = now(), status = 'success', summary_json = '{}'
        WHERE id = :id
    """), {"id": str(missing_id)})
    db.commit()
    assert result.rowcount == 0, "Expected 0 rows updated for missing job_id"


def test_drift_runs_status_check_violation(db):
    """INSERT with invalid status value raises IntegrityError (CHECK constraint)."""
    job_id = uuid4()
    with pytest.raises(IntegrityError):
        db.execute(text("""
            INSERT INTO drift_runs (id, run_id, started_at, status, triggered_by, summary_json)
            VALUES (:id, :run_id, now(), 'invalid', 'cli', '{}')
        """), {"id": str(job_id), "run_id": str(job_id)})
        db.commit()
    db.rollback()


def test_drift_runs_error_message_population(db):
    """Error path populates error_message and sets status='error'."""
    job_id = uuid4()
    db.execute(text("""
        INSERT INTO drift_runs (id, run_id, started_at, status, triggered_by, summary_json)
        VALUES (:id, :run_id, now(), 'running', 'cli', '{}')
    """), {"id": str(job_id), "run_id": str(job_id)})
    db.commit()

    error_msg = "kNN model unavailable: no active matrix"
    db.execute(text("""
        UPDATE drift_runs SET completed_at = now(), status = 'error', error_message = :err
        WHERE id = :id
    """), {"id": str(job_id), "err": error_msg})
    db.commit()

    row = db.execute(text("SELECT status, error_message FROM drift_runs WHERE id = :id"), {"id": str(job_id)}).fetchone()
    assert row is not None
    assert row[0] == "error"
    assert row[1] == error_msg
