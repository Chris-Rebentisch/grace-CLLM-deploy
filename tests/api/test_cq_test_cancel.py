"""F-0035 (ISS-0026) regression: cancel endpoint + background failure propagation
at the route layer.

POST /api/ontology/cq-test/{run_id}/cancel: running -> cancelled (200),
already-terminal -> 409, missing -> 404. `_run_tests_background` must mark the
run row failed on ANY exception instead of leaving a permanent `running` zombie.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session as SASession

from src.ontology.cq_test_models import CQTestRun, CQTestRunStatus
from src.ontology.cq_test_runner import create_test_run
from src.shared.database import get_db, get_engine


@pytest.fixture()
def db_session():
    """SAVEPOINT-rollback session (D485 pattern, mirrors test_cq_test_routes)."""
    from src.api.main import app

    engine = get_engine()
    connection = engine.connect()
    transaction = connection.begin()
    connection.execute(text(
        "TRUNCATE TABLE cq_test_runs, change_of_status_events, "
        "review_decisions, review_sessions, schema_promotion_events, "
        "calibration_records, schema_proposals, ontology_versions "
        "RESTART IDENTITY CASCADE"
    ))
    session = SASession(bind=connection)
    session.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(sess, trans):
        if trans.nested and not trans._parent.nested:
            sess.begin_nested()

    def override_get_db():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    yield session

    app.dependency_overrides.pop(get_db, None)
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from src.api.main import app
    return TestClient(app)


SAMPLE_SCHEMA = {
    "entity_types": {"Company": {"description": "A co", "properties": {}, "domain": "corp"}},
    "relationships": {},
}


def _create_version(db_session):
    from src.ontology.models import VersionSource
    from src.ontology.schema_store import ratify_version

    return ratify_version(
        db=db_session,
        schema_json=SAMPLE_SCHEMA,
        schema_modules={"corp": {}},
        source=VersionSource.DISCOVERY,
        reviewer="test",
    )


def _create_run(db_session, version, status: CQTestRunStatus) -> CQTestRun:
    run = CQTestRun(
        schema_version_id=version.id,
        schema_version_number=version.version_number,
        status=status,
    )
    return create_test_run(db_session, run)


# --- Cancel endpoint ---


def test_cancel_running_run(client, db_session):
    """POST /{run_id}/cancel flips running -> cancelled."""
    version = _create_version(db_session)
    created = _create_run(db_session, version, CQTestRunStatus.RUNNING)

    resp = client.post(f"/api/ontology/cq-test/{created.id}/cancel")

    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == str(created.id)
    assert data["status"] == "cancelled"

    # Row is terminal + visible through the read path.
    get_resp = client.get(f"/api/ontology/cq-test/{created.id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["status"] == "cancelled"
    assert get_resp.json()["completed_at"] is not None


def test_cancel_terminal_run_409(client, db_session):
    """Cancelling an already-terminal run returns 409."""
    version = _create_version(db_session)
    completed = _create_run(db_session, version, CQTestRunStatus.COMPLETED)

    resp = client.post(f"/api/ontology/cq-test/{completed.id}/cancel")
    assert resp.status_code == 409
    assert "terminal" in resp.json()["detail"]

    # Second cancel of a cancelled run is also 409.
    running = _create_run(db_session, version, CQTestRunStatus.RUNNING)
    assert client.post(f"/api/ontology/cq-test/{running.id}/cancel").status_code == 200
    assert client.post(f"/api/ontology/cq-test/{running.id}/cancel").status_code == 409


def test_cancel_missing_run_404(client):
    resp = client.post(f"/api/ontology/cq-test/{uuid4()}/cancel")
    assert resp.status_code == 404


# --- Background failure propagation (pure unit, mock session) ---


def test_background_task_failure_marks_run_failed():
    """ANY exception in the background task marks the run row failed with the
    error message — never a swallowed log line + zombie `running` row (F-0035)."""
    from src.api import cq_test_routes

    run_id = uuid4()
    fake_db = MagicMock()

    def fake_get_db():
        yield fake_db

    with patch("src.shared.database.get_db", fake_get_db), \
         patch(
             "src.api.cq_test_routes.run_cq_tests",
             side_effect=RuntimeError("Invalid domain 'x'. Must be one of: [...]"),
         ), \
         patch("src.api.cq_test_routes.mark_test_run_failed") as mock_mark:
        # Must not raise — the wrapper owns failure propagation.
        cq_test_routes._run_tests_background(None, 1, existing_run_id=run_id)

    mock_mark.assert_called_once_with(
        fake_db, run_id, "Invalid domain 'x'. Must be one of: [...]"
    )


def test_background_task_mark_failure_never_raises():
    """Even if marking the row failed itself explodes, the wrapper must not
    propagate (background-task cleanup must be exception-proof)."""
    from src.api import cq_test_routes

    fake_db = MagicMock()

    def fake_get_db():
        yield fake_db

    with patch("src.shared.database.get_db", fake_get_db), \
         patch("src.api.cq_test_routes.run_cq_tests", side_effect=RuntimeError("boom")), \
         patch(
             "src.api.cq_test_routes.mark_test_run_failed",
             side_effect=RuntimeError("db down"),
         ):
        cq_test_routes._run_tests_background(None, 1, existing_run_id=uuid4())


def test_background_task_success_does_not_mark_failed():
    from src.api import cq_test_routes

    fake_db = MagicMock()

    def fake_get_db():
        yield fake_db

    async def ok_run(**kwargs):
        return MagicMock()

    with patch("src.shared.database.get_db", fake_get_db), \
         patch("src.api.cq_test_routes.run_cq_tests", side_effect=ok_run), \
         patch("src.api.cq_test_routes.mark_test_run_failed") as mock_mark:
        cq_test_routes._run_tests_background(None, 1, existing_run_id=uuid4())

    mock_mark.assert_not_called()
