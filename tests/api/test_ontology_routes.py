"""Tests for FastAPI ontology route endpoints."""

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session as SASession

from src.shared.database import get_db, get_engine


# D485 — SAVEPOINT-rollback fixture (Chunk 75a).
# Invariant: replaces TRUNCATE-based per-test isolation with SQLAlchemy 2.0
# SAVEPOINT-rollback pattern. The dependency override ensures the FastAPI
# app's get_db() uses the same transactional connection.
# Authorization: D485 / spec §6 Step 2.


@pytest.fixture(autouse=True)
def _db_rollback():
    """SAVEPOINT-rollback isolation for API tests (D485)."""
    from src.api.main import app

    engine = get_engine()
    connection = engine.connect()
    transaction = connection.begin()
    connection.execute(text(
        "TRUNCATE TABLE schema_promotion_events, calibration_records, "
        "schema_proposals, ontology_versions "
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
    yield

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


def _ratify_body(**overrides) -> dict:
    """Create a ratify request body with sensible defaults."""
    defaults = {
        "schema_json": {"entity_types": {"Company": {"properties": {}}}, "relationships": {}},
        "schema_modules": {"core": {"types": ["Company"]}},
        "source": "discovery",
        "reviewer": "test_user",
        "changelog": "Test version",
    }
    defaults.update(overrides)
    return defaults


def test_get_active_404_when_empty(client):
    """GET /api/ontology/active returns 404 when no versions exist."""
    resp = client.get("/api/ontology/active")
    assert resp.status_code == 404


def test_ratify_creates_version(client):
    """POST /api/ontology/ratify creates version and returns it."""
    resp = client.post("/api/ontology/ratify", json=_ratify_body())
    assert resp.status_code == 200
    data = resp.json()
    assert data["version_number"] == 1
    assert data["is_active"] is True
    assert data["hash_chain"] is not None


def test_get_active_after_ratify(client):
    """GET /api/ontology/active returns active version after ratify."""
    client.post("/api/ontology/ratify", json=_ratify_body())
    resp = client.get("/api/ontology/active")
    assert resp.status_code == 200
    data = resp.json()
    assert data["version_number"] == 1
    assert data["is_active"] is True


def test_get_versions_list(client):
    """GET /api/ontology/versions returns version history."""
    client.post("/api/ontology/ratify", json=_ratify_body())
    resp = client.get("/api/ontology/versions")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["version_number"] == 1


def test_get_version_by_number(client):
    """GET /api/ontology/versions/{n} returns specific version."""
    client.post("/api/ontology/ratify", json=_ratify_body())
    resp = client.get("/api/ontology/versions/1")
    assert resp.status_code == 200
    assert resp.json()["version_number"] == 1


def test_get_version_not_found(client):
    """GET /api/ontology/versions/{n} returns 404 for nonexistent."""
    resp = client.get("/api/ontology/versions/999")
    assert resp.status_code == 404


def test_get_module_schema(client):
    """GET /api/ontology/modules/{name} returns module schema."""
    client.post("/api/ontology/ratify", json=_ratify_body())
    resp = client.get("/api/ontology/modules/core")
    assert resp.status_code == 200
    assert resp.json() == {"types": ["Company"]}


def test_get_module_not_found(client):
    """GET /api/ontology/modules/{name} returns 404 for nonexistent module."""
    client.post("/api/ontology/ratify", json=_ratify_body())
    resp = client.get("/api/ontology/modules/nonexistent")
    assert resp.status_code == 404


def test_verify_chain(client):
    """GET /api/ontology/verify-chain returns valid chain result."""
    client.post("/api/ontology/ratify", json=_ratify_body())
    resp = client.get("/api/ontology/verify-chain")
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["versions_checked"] == 1


def test_diff_between_versions(client):
    """GET /api/ontology/diff/{old}/{new} returns diff between versions."""
    client.post("/api/ontology/ratify", json=_ratify_body())

    schema2 = {
        "entity_types": {"Company": {"properties": {}}, "Trust": {"properties": {}}},
        "relationships": {"owns": {}},
    }
    client.post("/api/ontology/ratify", json=_ratify_body(
        schema_json=schema2, changelog="Added Trust and owns"
    ))

    resp = client.get("/api/ontology/diff/1/2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["old_version"] == 1
    assert data["new_version"] == 2
    assert "rfc6902_patch" in data
    assert "entity_level_diff" in data
    assert len(data["rfc6902_patch"]) > 0
