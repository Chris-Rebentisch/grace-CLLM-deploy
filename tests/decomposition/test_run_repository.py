"""Repository tests for ``decomposition_runs`` (Chunk 40, CP10).

Covers append-only trigger enforcement (D310 first-write-only JSONB),
``grace_readonly`` SELECT, Path B resume, and the
``latest_completed_run_for_archive_hash`` helper.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, InternalError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from src.decomposition import run_repository
from src.decomposition.run_repository import (
    ArchiveDriftError,
    _canonical_hash,
    create_resume_run,
    create_run,
    finalize_run,
    get_run,
    latest_completed_run_for_archive_hash,
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


def _make_run(session, archive_root: str = "/tmp/synthetic-archive") -> dict:
    return create_run(session, archive_root=archive_root, operator=None)


def test_repository_append_only_trigger_denies_archive_root_update(db_session):
    row = _make_run(db_session, "/tmp/archive-A")
    db_session.commit()
    with pytest.raises((DBAPIError, InternalError, ProgrammingError, Exception)):
        db_session.execute(
            text(
                "UPDATE decomposition_runs SET archive_root = :new "
                "WHERE run_id = :id"
            ),
            {"new": "/tmp/archive-B", "id": row["run_id"]},
        )
        db_session.commit()
    db_session.rollback()


def test_repository_overwrite_of_populated_jsonb_is_denied(db_session):
    row = _make_run(db_session, "/tmp/archive-overwrite")
    finalize_run(
        db_session,
        row["run_id"],
        status="paused_pre_layer4",
        completed_at=None,
        layer_artifacts={
            "layer1_summary": {"archive_root": "/tmp/archive-overwrite",
                               "total_files": 0, "files": [], "folders": []},
        },
    )
    db_session.commit()
    # First write succeeded; second write must be denied (NULL → value
    # is allowed; value → value is not).
    with pytest.raises(Exception):
        db_session.execute(
            text(
                "UPDATE decomposition_runs SET layer1_summary = "
                "CAST(:v AS JSONB) WHERE run_id = :id"
            ),
            {"v": json.dumps({"archive_root": "x", "total_files": 1,
                              "files": [], "folders": []}),
             "id": row["run_id"]},
        )
        db_session.commit()
    db_session.rollback()


def test_repository_first_write_to_null_jsonb_is_allowed(db_session):
    row = _make_run(db_session, "/tmp/archive-firstwrite")
    fetched = get_run(db_session, row["run_id"])
    assert fetched is not None
    assert fetched["layer2_decision"] is None

    finalize_run(
        db_session,
        row["run_id"],
        status="paused_pre_layer4",
        completed_at=None,
        layer_artifacts={
            "layer2_decision": {
                "algorithm": "hdbscan",
                "cluster_count": 0,
                "outlier_count": 0,
                "outlier_ratio_at_gate": 0.0,
                "outlier_ratio_gate": 0.30,
                "cluster_labels": [],
                "umap": {"n_components": 10, "n_neighbors": 15,
                         "min_dist": 0.1, "metric": "cosine",
                         "random_state": 42},
                "embedding": {"model": "mock", "dimension": 4,
                              "document_count": 0},
            }
        },
    )
    db_session.commit()
    refreshed = get_run(db_session, row["run_id"])
    assert refreshed is not None
    assert refreshed["layer2_decision"] is not None


def test_repository_status_and_completed_at_updates_succeed(db_session):
    from datetime import datetime, timezone

    row = _make_run(db_session, "/tmp/archive-status")
    finalize_run(
        db_session,
        row["run_id"],
        status="completed",
        completed_at=datetime.now(timezone.utc),
        layer_artifacts={},
    )
    db_session.commit()
    refreshed = get_run(db_session, row["run_id"])
    assert refreshed is not None
    assert refreshed["status"] == "completed"
    assert refreshed["completed_at"] is not None


def test_repository_grace_readonly_can_select(db_session):
    """grace_readonly is GRANTed SELECT on the table; skip if role absent."""
    role_present = db_session.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly'")
    ).one_or_none()
    if role_present is None:
        pytest.skip("grace_readonly role not provisioned in this environment")
    result = db_session.execute(
        text(
            "SELECT has_table_privilege('grace_readonly', "
            "'decomposition_runs', 'SELECT') AS can_select"
        )
    ).one()
    assert result._mapping["can_select"] is True


def test_repository_path_b_resume_creates_successor_row(db_session, tmp_path: Path):
    """create_resume_run inserts a new row with resumed_from_run_id set."""
    archive = tmp_path / "archive"
    archive.mkdir()
    paused = create_run(db_session, archive_root=str(archive), operator=None)
    finalize_run(
        db_session,
        paused["run_id"],
        status="paused_pre_layer4",
        completed_at=None,
        layer_artifacts={
            "layer1_summary": {"archive_root": str(archive),
                               "total_files": 0, "files": [], "folders": []},
        },
    )
    db_session.commit()

    successor = create_resume_run(db_session, paused["run_id"])
    db_session.commit()

    assert successor["run_id"] != paused["run_id"]
    refreshed_succ = get_run(db_session, successor["run_id"])
    assert refreshed_succ is not None
    assert refreshed_succ["resumed_from_run_id"] == paused["run_id"]
    # Layer 1 carried forward via INSERT (so it bypasses the trigger).
    assert refreshed_succ["layer1_summary"] is not None


def test_repository_archive_drift_raises(db_session, tmp_path: Path, monkeypatch):
    archive = tmp_path / "archive"
    archive.mkdir()
    paused = create_run(db_session, archive_root=str(archive), operator=None)
    finalize_run(
        db_session,
        paused["run_id"],
        status="paused_pre_layer4",
        completed_at=None,
        layer_artifacts={},
    )
    db_session.commit()

    # Force the recompute to differ from the stored hash.
    monkeypatch.setattr(
        run_repository, "_canonical_hash", lambda _root: "f" * 64
    )
    with pytest.raises(ArchiveDriftError):
        create_resume_run(db_session, paused["run_id"])
    db_session.rollback()


def test_repository_latest_completed_for_hash(db_session, tmp_path: Path):
    archive = tmp_path / "archive"
    archive.mkdir()
    h = _canonical_hash(str(archive))

    # Three runs: two completed, one running.
    r1 = create_run(db_session, archive_root=str(archive), operator=None)
    finalize_run(db_session, r1["run_id"], status="completed",
                 completed_at=None, layer_artifacts={})
    r2 = create_run(db_session, archive_root=str(archive), operator=None)
    finalize_run(db_session, r2["run_id"], status="completed",
                 completed_at=None, layer_artifacts={})
    create_run(db_session, archive_root=str(archive), operator=None)
    db_session.commit()

    latest = latest_completed_run_for_archive_hash(db_session, h)
    assert latest is not None
    assert latest["status"] == "completed"
    assert latest["archive_root_canonical_hash"] == h
