"""Tests for FastAPI CQ test route endpoints."""

import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session as SASession

from src.ontology.cq_test_models import CQTestResult, CQTestRun, CQTestRunStatus
from src.ontology.cq_test_runner import create_test_run
from src.shared.database import get_db, get_engine
from src.shared.llm_provider import LLMResponse


# D485 — SAVEPOINT-rollback fixture (Chunk 75a).
# Invariant: replaces TRUNCATE-based per-test isolation with SQLAlchemy 2.0
# SAVEPOINT-rollback pattern. The dependency override ensures the FastAPI
# app's get_db() uses the same transactional connection.
# Authorization: D485 / spec §6 Step 2.


@pytest.fixture()
def db_session():
    """Yield a SAVEPOINT-rollback session for testing (D485)."""
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
            pass  # Don't close — outer fixture handles cleanup

    app.dependency_overrides[get_db] = override_get_db
    yield session

    app.dependency_overrides.pop(get_db, None)
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def client():
    """Create a FastAPI TestClient."""
    from fastapi.testclient import TestClient
    from src.api.main import app
    return TestClient(app)


SAMPLE_SCHEMA = {
    "entity_types": {"Company": {"description": "A co", "properties": {}, "domain": "corp"}},
    "relationships": {},
}


def _create_version(db_session):
    """Create a ratified version for testing."""
    from src.ontology.schema_store import ratify_version
    from src.ontology.models import VersionSource

    return ratify_version(
        db=db_session,
        schema_json=SAMPLE_SCHEMA,
        schema_modules={"corp": {}},
        source=VersionSource.DISCOVERY,
        reviewer="test",
    )


def test_post_run_starts_test(client, db_session):
    """POST /run starts a test run and returns run_id.

    The production background task (`_run_tests_background`) opens its OWN pooled
    DB connection. Under this test's D485 SAVEPOINT-rollback fixture, that
    connection cannot see the uncommitted version row and blocks on the
    ACCESS EXCLUSIVE lock held by the fixture's open `TRUNCATE ... CASCADE`
    transaction — a deadlock, because Starlette's TestClient runs BackgroundTasks
    synchronously before returning. This is a test-isolation artifact, not a
    production defect (in production the request transaction commits before the
    background task runs). This test only asserts the synchronous response
    contract, so we patch the background task to a no-op. (Task #33A — removes the
    D486 allowlist entry for this test.)
    """
    version = _create_version(db_session)
    with patch("src.api.cq_test_routes._run_tests_background"):
        resp = client.post("/api/ontology/cq-test/run", json={
            "schema_version_id": str(version.id),
            "concurrency": 1,
        })
    assert resp.status_code == 200
    data = resp.json()
    assert "run_id" in data
    assert data["status"] == "running"


def test_get_run_returns_run(client, db_session):
    """GET /{run_id} returns test run with results."""
    version = _create_version(db_session)
    run = CQTestRun(
        schema_version_id=version.id,
        schema_version_number=version.version_number,
        status=CQTestRunStatus.COMPLETED,
        total_cqs=5,
        passing=4,
        failing=1,
        pass_rate=0.8,
    )
    created = create_test_run(db_session, run)

    resp = client.get(f"/api/ontology/cq-test/{created.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_cqs"] == 5
    assert data["passing"] == 4


def test_get_run_404(client):
    """GET /{run_id} returns 404 for nonexistent."""
    resp = client.get(f"/api/ontology/cq-test/{uuid4()}")
    assert resp.status_code == 404


def test_get_failures(client, db_session):
    """GET /{run_id}/failures returns only failing CQs."""
    version = _create_version(db_session)
    from src.ontology.cq_test_models import CQTestResultEntry
    run = CQTestRun(
        schema_version_id=version.id,
        status=CQTestRunStatus.COMPLETED,
        total_cqs=2,
        passing=1,
        failing=1,
        pass_rate=0.5,
        results=[
            CQTestResultEntry(
                cq_id="cq_001", cq_text="Q1", result=CQTestResult.PASS,
            ),
            CQTestResultEntry(
                cq_id="cq_002", cq_text="Q2", result=CQTestResult.FAIL,
                gap_type="missing_type", gap_severity="major",
                gap_details="Missing Person type",
            ),
        ],
    )
    created = create_test_run(db_session, run)

    resp = client.get(f"/api/ontology/cq-test/{created.id}/failures")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["cq_id"] == "cq_002"
    assert data[0]["gap_severity"] == "major"


def test_post_gate(client, db_session):
    """POST /gate returns gate result with gate_passed."""
    _create_version(db_session)

    mock_prov = AsyncMock()
    mock_prov.provider_name = "mock"
    mock_prov.model = "mock-model"
    mock_prov.generate.return_value = LLMResponse(
        text=json.dumps({
            "result": "pass", "confidence": 0.9, "path": "A",
            "reasoning": "ok",
        }),
        model="mock", provider="mock",
    )

    with patch("src.ontology.cq_test_runner.get_provider", return_value=mock_prov), \
         patch("src.ontology.cq_test_runner.load_testable_cqs") as mock_load:
        mock_load.return_value = (
            [{"cq_id": "cq_001", "cq_text": "Q1", "domain": "d"}],
            [],
        )
        resp = client.post("/api/ontology/cq-test/gate", json={
            "proposed_schema_json": SAMPLE_SCHEMA,
            "threshold": 0.90,
            "concurrency": 1,
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["gate_passed"] is True
    assert data["pass_rate"] == 1.0


def test_get_history(client, db_session):
    """GET /history returns test run history."""
    version = _create_version(db_session)
    run = CQTestRun(
        schema_version_id=version.id,
        schema_version_number=version.version_number,
        status=CQTestRunStatus.COMPLETED,
        total_cqs=10,
        passing=9,
        failing=1,
        pass_rate=0.9,
    )
    create_test_run(db_session, run)

    resp = client.get("/api/ontology/cq-test/history")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["total_cqs"] == 10


def test_get_history_filter_by_version(client, db_session):
    """GET /history filters by schema_version_id."""
    version = _create_version(db_session)
    run = CQTestRun(
        schema_version_id=version.id,
        status=CQTestRunStatus.COMPLETED,
    )
    create_test_run(db_session, run)

    # Filter by this version
    resp = client.get(f"/api/ontology/cq-test/history?schema_version_id={version.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1

    # Filter by random version
    resp2 = client.get(f"/api/ontology/cq-test/history?schema_version_id={uuid4()}")
    assert resp2.status_code == 200
    assert len(resp2.json()) == 0
