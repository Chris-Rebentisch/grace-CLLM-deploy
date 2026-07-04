"""F-13 regression: stalled non-terminal jobs must not deadlock re-submission.

Validation run finding: the POST /api/extraction/jobs 409 concurrent-trigger guard
treated any non-terminal ('pending'/'running') job as blocking forever, so a
stuck job made the source path permanently un-resubmittable. The fix reuses the
D470 30-min stalled threshold: if the existing job's heartbeat is older than the
threshold, the new spawn is allowed and the stalled row is marked failed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.api.main import app
from src.api.extraction_routes import _job_heartbeat_age_seconds, _STALLED_THRESHOLD_SECONDS
from src.shared.database import get_session_factory


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def allowed_source_file(tmp_path):
    root = tmp_path.resolve()
    test_file = root / "stalled_doc.txt"
    test_file.write_text("stalled job document")
    stub_schema = root / "ontology.json"
    stub_schema.write_text("{}")
    with patch(
        "src.api.extraction_routes._resolve_active_ontology_json",
        return_value=stub_schema,
    ):
        yield test_file, root


def test_f13_heartbeat_age_helper():
    """Unit: the heartbeat-age helper computes age from started_at/created_at."""
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    row = MagicMock()
    row.progress_json = None
    row.started_at = old
    row.created_at = old
    age = _job_heartbeat_age_seconds(row)
    assert age is not None
    assert age > _STALLED_THRESHOLD_SECONDS


def test_f13_stalled_pending_job_allows_respawn(client, allowed_source_file, db):
    """A stalled pending job must NOT 409 — the new spawn proceeds and the
    stalled row is marked failed."""
    test_file, tmp_path = allowed_source_file
    source_key = str(test_file.resolve())

    # Insert a stalled 'pending' row with a created_at older than the threshold.
    stalled_id = uuid4()
    stale_ts = datetime.now(timezone.utc) - timedelta(seconds=_STALLED_THRESHOLD_SECONDS + 600)
    db.execute(
        text(
            "INSERT INTO extraction_jobs "
            "(job_id, job_kind, source_path, status, created_at, created_by) "
            "VALUES (:jid, 'document', :sp, 'pending', :ts, 'test')"
        ),
        {"jid": str(stalled_id), "sp": source_key, "ts": stale_ts},
    )
    db.commit()

    mock_proc = MagicMock()
    mock_proc.pid = 88888
    mock_proc.wait = MagicMock(return_value=0)

    try:
        with patch("src.api.extraction_routes._get_allowed_roots", return_value=[tmp_path]), \
             patch("src.api.extraction_routes.subprocess.Popen", return_value=mock_proc):
            resp = client.post("/api/extraction/jobs", json={
                "job_kind": "document",
                "source_path": str(test_file),
            })

        assert resp.status_code == 202, resp.text
        new_job_id = resp.json()["job_id"]
        assert new_job_id != str(stalled_id)

        # The stalled row must now be 'failed'.
        row = db.execute(
            text("SELECT status FROM extraction_jobs WHERE job_id = :jid"),
            {"jid": str(stalled_id)},
        ).first()
        assert row is not None
        assert row.status == "failed"
    finally:
        db.execute(
            text("DELETE FROM extraction_jobs WHERE source_path = :sp"),
            {"sp": source_key},
        )
        db.commit()


def test_f13_fresh_pending_job_still_409s(client, allowed_source_file, db):
    """A recent (non-stalled) pending job must still 409 — guard preserved."""
    test_file, tmp_path = allowed_source_file
    source_key = str(test_file.resolve())

    fresh_id = uuid4()
    db.execute(
        text(
            "INSERT INTO extraction_jobs "
            "(job_id, job_kind, source_path, status, created_at, created_by) "
            "VALUES (:jid, 'document', :sp, 'pending', now(), 'test')"
        ),
        {"jid": str(fresh_id), "sp": source_key},
    )
    db.commit()

    mock_proc = MagicMock()
    mock_proc.pid = 88887
    mock_proc.wait = MagicMock(return_value=0)

    try:
        with patch("src.api.extraction_routes._get_allowed_roots", return_value=[tmp_path]), \
             patch("src.api.extraction_routes.subprocess.Popen", return_value=mock_proc):
            resp = client.post("/api/extraction/jobs", json={
                "job_kind": "document",
                "source_path": str(test_file),
            })
        assert resp.status_code == 409
    finally:
        db.execute(
            text("DELETE FROM extraction_jobs WHERE source_path = :sp"),
            {"sp": source_key},
        )
        db.commit()
