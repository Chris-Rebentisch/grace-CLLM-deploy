"""Persistence tests for c41b Layer 5/6 columns (Chunk 41, CP6, D327).

Round-trip the new ``layer5_decision`` and ``layer6_validation`` JSONB
columns through the ``run_repository`` first-write-only helpers, and
verify the seven-value status enum.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, InternalError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from src.decomposition import run_repository
from src.decomposition.run_repository import (
    create_run,
    get_run,
    transition_status,
    update_layer5_decision,
    update_layer6_validation,
)


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


def _make_run(db_session, archive: Path) -> dict:
    return create_run(db_session, archive_root=str(archive), operator=None)


def test_update_layer5_decision_first_write_succeeds(
    db_session, tmp_path: Path
):
    archive = tmp_path / "a"
    archive.mkdir()
    row = _make_run(db_session, archive)
    out = update_layer5_decision(
        db_session,
        row["run_id"],
        {
            "decision_kind": "accepted_null",
            "rationale": "",
            "decided_at": datetime.now(timezone.utc).isoformat(),
            "modifications": [],
        },
    )
    db_session.commit()
    assert out["layer5_decision"]["decision_kind"] == "accepted_null"


def test_update_layer5_decision_second_write_blocked(
    db_session, tmp_path: Path
):
    archive = tmp_path / "a"
    archive.mkdir()
    row = _make_run(db_session, archive)
    update_layer5_decision(
        db_session,
        row["run_id"],
        {
            "decision_kind": "accepted_null",
            "rationale": "",
            "decided_at": datetime.now(timezone.utc).isoformat(),
            "modifications": [],
        },
    )
    db_session.commit()
    with pytest.raises(
        (DBAPIError, InternalError, ProgrammingError, Exception)
    ):
        update_layer5_decision(
            db_session,
            row["run_id"],
            {
                "decision_kind": "rerun_finer",
                "rationale": "",
                "decided_at": datetime.now(timezone.utc).isoformat(),
                "modifications": [],
            },
        )
        db_session.commit()
    db_session.rollback()


def test_update_layer6_validation_first_write_succeeds(
    db_session, tmp_path: Path
):
    archive = tmp_path / "a"
    archive.mkdir()
    row = _make_run(db_session, archive)
    out = update_layer6_validation(
        db_session,
        row["run_id"],
        {
            "segments": [],
            "validated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    db_session.commit()
    assert out["layer6_validation"]["segments"] == []


def test_update_layer6_validation_second_write_blocked(
    db_session, tmp_path: Path
):
    archive = tmp_path / "a"
    archive.mkdir()
    row = _make_run(db_session, archive)
    update_layer6_validation(
        db_session,
        row["run_id"],
        {"segments": [], "validated_at": datetime.now(timezone.utc).isoformat()},
    )
    db_session.commit()
    with pytest.raises(Exception):
        update_layer6_validation(
            db_session,
            row["run_id"],
            {
                "segments": [{"segment_name": "x", "sample_cqs": [],
                              "approved_count": 0, "rejected_count": 0}],
                "validated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        db_session.commit()
    db_session.rollback()


@pytest.mark.parametrize(
    "new_status",
    ["paused_pre_layer5", "paused_pre_layer6", "paused_pre_layer7"],
)
def test_transition_status_accepts_new_pause_states(
    db_session, tmp_path: Path, new_status: str
):
    archive = tmp_path / "a"
    archive.mkdir()
    row = _make_run(db_session, archive)
    out = transition_status(db_session, row["run_id"], new_status)
    db_session.commit()
    assert out["status"] == new_status


def test_transition_status_rejects_unknown_status(db_session, tmp_path: Path):
    archive = tmp_path / "a"
    archive.mkdir()
    row = _make_run(db_session, archive)
    with pytest.raises(ValueError):
        transition_status(db_session, row["run_id"], "bogus_state")
    db_session.rollback()
