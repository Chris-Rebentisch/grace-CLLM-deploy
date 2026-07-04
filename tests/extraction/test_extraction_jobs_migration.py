"""Migration tests for c72a_extraction_jobs (D469).

Verifies the extraction_jobs table exists after upgrade and that column
types + constraints match the spec.
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, text

from src.shared.database import get_session_factory


@pytest.fixture
def db():
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


def test_extraction_jobs_table_exists(db):
    """Table exists after alembic upgrade head."""
    inspector = inspect(db.bind)
    tables = inspector.get_table_names()
    assert "extraction_jobs" in tables


def test_extraction_jobs_column_types(db):
    """Column types and constraints match spec D469."""
    inspector = inspect(db.bind)
    columns = {c["name"]: c for c in inspector.get_columns("extraction_jobs")}

    expected_columns = [
        "job_id", "job_kind", "source_path", "status", "pid",
        "progress_json", "error_message", "started_at", "completed_at",
        "created_at", "created_by", "provider", "model",
        "cost_budget_usd", "shard_pids",
    ]
    for col_name in expected_columns:
        assert col_name in columns, f"Missing column: {col_name}"

    # Verify index exists
    indexes = inspector.get_indexes("extraction_jobs")
    index_names = [idx["name"] for idx in indexes]
    assert "ix_extraction_jobs_status_created" in index_names

    # Verify CHECK constraints exist by attempting invalid inserts
    from uuid import uuid4

    # Invalid job_kind
    with pytest.raises(Exception):
        db.execute(
            text(
                "INSERT INTO extraction_jobs (job_id, job_kind, source_path) "
                "VALUES (:jid, 'invalid_kind', '/tmp/test')"
            ),
            {"jid": str(uuid4())},
        )
        db.flush()
    db.rollback()

    # Invalid status
    with pytest.raises(Exception):
        db.execute(
            text(
                "INSERT INTO extraction_jobs (job_id, job_kind, source_path, status) "
                "VALUES (:jid, 'document', '/tmp/test', 'invalid_status')"
            ),
            {"jid": str(uuid4())},
        )
        db.flush()
    db.rollback()
