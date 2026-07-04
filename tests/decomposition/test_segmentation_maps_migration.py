"""Migration round-trip tests for c41a + c41b (Chunk 41, CP2/CP3)."""

from __future__ import annotations

import json
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, InternalError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker


pytestmark = pytest.mark.skipif(
    os.environ.get("GRACE_SKIP_DB_TESTS") == "1",
    reason="Postgres not available",
)


def _database_url() -> str:
    return os.environ.get(
        "DATABASE_URL", "postgresql+psycopg2:///grace"
    )


@pytest.fixture
def db_session() -> Session:
    engine = create_engine(_database_url(), future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()
        engine.dispose()


def _make_run(session) -> str:
    row = session.execute(
        text(
            "INSERT INTO decomposition_runs (archive_root, archive_root_canonical_hash, status) "
            "VALUES (:r, :h, 'running') RETURNING run_id"
        ),
        {"r": "/tmp/c41-mig-archive-" + uuid4().hex, "h": "f" * 64},
    ).one()
    session.commit()
    return str(row[0])


# ---------- c41a: segmentation_maps table ----------


def test_c41a_segmentation_maps_table_exists(db_session):
    row = db_session.execute(
        text(
            "SELECT to_regclass('public.segmentation_maps') IS NOT NULL"
        )
    ).scalar()
    assert row is True


def test_c41a_append_only_trigger_blocks_update(db_session):
    run_id = _make_run(db_session)
    # Per-test unique payload_hash so successive runs against the shared dev
    # database don't collide on uq_segmentation_maps_payload_hash.
    unique_hash = uuid4().hex + uuid4().hex  # 64 hex chars
    db_session.execute(
        text(
            "INSERT INTO segmentation_maps "
            "(decomposition_run_id, schema_version, payload_hash, payload, null_hypothesis_accepted) "
            "VALUES (:run, '1.0', :h, CAST(:p AS JSONB), FALSE)"
        ),
        {
            "run": run_id,
            "h": unique_hash,
            "p": json.dumps({"k": "v"}),
        },
    )
    db_session.commit()

    with pytest.raises((DBAPIError, InternalError, ProgrammingError, Exception)):
        db_session.execute(
            text(
                "UPDATE segmentation_maps SET schema_version='2.0' "
                "WHERE payload_hash = :h"
            ),
            {"h": unique_hash},
        )
        db_session.commit()
    db_session.rollback()


def test_c41a_grants_select_to_grace_readonly(db_session):
    """grace_readonly role should hold SELECT on segmentation_maps when role exists."""
    role_exists = db_session.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly'")
    ).first()
    if role_exists is None:
        pytest.skip("grace_readonly role not bootstrapped")
    privs = db_session.execute(
        text(
            "SELECT has_table_privilege('grace_readonly', 'segmentation_maps', 'SELECT')"
        )
    ).scalar()
    assert privs is True


# ---------- c41b: decomposition_runs widening ----------


def test_c41b_status_check_accepts_seven_values(db_session):
    """All seven status values must be accepted by the CHECK constraint."""
    valid = [
        "running",
        "completed",
        "failed",
        "paused_pre_layer4",
        "paused_pre_layer5",
        "paused_pre_layer6",
        "paused_pre_layer7",
    ]
    for s in valid:
        run_id = _make_run(db_session)
        # First UPDATE sets status; allowed.
        db_session.execute(
            text("UPDATE decomposition_runs SET status = :s WHERE run_id = :r"),
            {"s": s, "r": run_id},
        )
        db_session.commit()
        row = db_session.execute(
            text("SELECT status FROM decomposition_runs WHERE run_id = :r"),
            {"r": run_id},
        ).scalar()
        assert row == s


def test_c41b_status_check_rejects_unknown(db_session):
    run_id = _make_run(db_session)
    with pytest.raises((DBAPIError, InternalError, ProgrammingError, Exception)):
        db_session.execute(
            text(
                "UPDATE decomposition_runs SET status = 'bogus' WHERE run_id = :r"
            ),
            {"r": run_id},
        )
        db_session.commit()
    db_session.rollback()


def test_c41b_layer5_decision_first_write_only(db_session):
    run_id = _make_run(db_session)
    payload = json.dumps({"decision_kind": "accepted_segmented"})
    db_session.execute(
        text(
            "UPDATE decomposition_runs SET layer5_decision = CAST(:p AS JSONB) "
            "WHERE run_id = :r"
        ),
        {"p": payload, "r": run_id},
    )
    db_session.commit()
    # Second write should raise.
    with pytest.raises((DBAPIError, InternalError, ProgrammingError, Exception)):
        db_session.execute(
            text(
                "UPDATE decomposition_runs SET layer5_decision = CAST(:p AS JSONB) "
                "WHERE run_id = :r"
            ),
            {"p": json.dumps({"decision_kind": "accepted_null"}), "r": run_id},
        )
        db_session.commit()
    db_session.rollback()


def test_c41b_layer6_validation_first_write_only(db_session):
    run_id = _make_run(db_session)
    db_session.execute(
        text(
            "UPDATE decomposition_runs SET layer6_validation = CAST(:p AS JSONB) "
            "WHERE run_id = :r"
        ),
        {"p": json.dumps({"segments": []}), "r": run_id},
    )
    db_session.commit()
    with pytest.raises((DBAPIError, InternalError, ProgrammingError, Exception)):
        db_session.execute(
            text(
                "UPDATE decomposition_runs SET layer6_validation = CAST(:p AS JSONB) "
                "WHERE run_id = :r"
            ),
            {"p": json.dumps({"segments": [{"x": 1}]}), "r": run_id},
        )
        db_session.commit()
    db_session.rollback()
