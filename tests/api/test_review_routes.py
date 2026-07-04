"""Tests for FastAPI review route endpoints."""

from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session as SASession

from src.shared.database import get_db, get_engine


# D485 — SAVEPOINT-rollback fixture (Chunk 75a).
# Invariant: replaces TRUNCATE-based per-test isolation with SQLAlchemy 2.0
# SAVEPOINT-rollback pattern. The dependency override ensures the FastAPI
# app's get_db() uses the same transactional connection.
# Authorization: D485 / spec §6 Step 2.


SAMPLE_SEED_SCHEMA = {
    "entity_types": [
        {
            "name": "Company",
            "description": "A business entity",
            "domain": "corporate",
            "parent_type": None,
            "properties": [
                {"name": "name", "data_type": "string", "required": True},
                {"name": "jurisdiction", "data_type": "string", "required": False},
            ],
            "provenance": "seed+3pass",
            "confidence": 1.0,
            "source_passes": ["top_down", "bottom_up", "middle_out"],
            "answerable_cqs": ["cq_001", "cq_002"],
        },
        {
            "name": "Insurance_Policy",
            "description": "An insurance coverage policy",
            "domain": "insurance",
            "parent_type": None,
            "properties": [
                {"name": "policy_number", "data_type": "string", "required": True},
            ],
            "provenance": "2pass_novel",
            "confidence": 0.67,
            "source_passes": ["top_down", "middle_out"],
            "answerable_cqs": ["cq_003"],
        },
    ],
    "relationships": [
        {
            "name": "covers",
            "source_type": "Insurance_Policy",
            "target_type": "Company",
            "description": "Policy covers a company",
            "richness_tier": "attributed",
            "edge_properties": [
                {"name": "coverage_amount", "data_type": "float"},
            ],
            "domain": "insurance",
            "provenance": "seed+2pass",
            "confidence": 0.8,
            "source_passes": ["top_down", "bottom_up"],
            "answerable_cqs": ["cq_003"],
        },
    ],
    "coverage_matrix": [
        {
            "cq_id": "cq_001",
            "cq_text": "What companies does Acme control?",
            "domain": "corporate",
            "covered_by_types": ["Company"],
            "covered_by_relationships": [],
            "coverage_status": "partial",
        },
        {
            "cq_id": "cq_002",
            "cq_text": "In which jurisdictions are companies registered?",
            "domain": "corporate",
            "covered_by_types": ["Company"],
            "covered_by_relationships": [],
            "coverage_status": "partial",
        },
        {
            "cq_id": "cq_003",
            "cq_text": "What insurance covers each company?",
            "domain": "insurance",
            "covered_by_types": ["Insurance_Policy", "Company"],
            "covered_by_relationships": ["covers"],
            "coverage_status": "covered",
        },
    ],
    "quality_metrics": {"cq_coverage_rate": 0.67},
    "provenance_summary": {"seed+3pass": 1, "2pass_novel": 1, "seed+2pass": 1},
}


@pytest.fixture(autouse=True)
def _db_rollback():
    """SAVEPOINT-rollback isolation for API tests (D485)."""
    from src.api.main import app

    engine = get_engine()
    connection = engine.connect()
    transaction = connection.begin()
    connection.execute(text(
        "TRUNCATE TABLE change_of_status_events, review_decisions, "
        "review_sessions, schema_promotion_events, calibration_records, "
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


def _start_session(client) -> dict:
    """Helper to start a review session and return the response data."""
    resp = client.post("/api/ontology/review/start", json={
        "merge_run_id": "test-merge-run",
        "reviewer": "tester",
        "seed_schema_data": SAMPLE_SEED_SCHEMA,
    })
    assert resp.status_code == 200
    return resp.json()


def test_start_review_session(client):
    """POST /start creates a session with element counts."""
    data = _start_session(client)
    assert data["status"] == "in_progress"
    assert data["total_entity_types"] == 2
    assert data["total_relationships"] == 1
    assert data["reviewer"] == "tester"


def test_get_session(client):
    """GET /{session_id} returns session status."""
    created = _start_session(client)
    resp = client.get(f"/api/ontology/review/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


def test_get_elements(client):
    """GET /{session_id}/elements returns element review status."""
    created = _start_session(client)
    resp = client.get(f"/api/ontology/review/{created['id']}/elements")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["entity_types"]) == 2
    assert len(data["relationships"]) == 1
    assert data["entity_types"][0]["status"] == "pending"


def test_decide_records_decision(client):
    """POST /{session_id}/decide records a decision with CQ impact."""
    created = _start_session(client)
    resp = client.post(f"/api/ontology/review/{created['id']}/decide", json={
        "element_type": "entity_type",
        "element_name": "Company",
        "decision": "approved",
        "reviewer": "tester",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["decision"]["element_name"] == "Company"
    assert data["decision"]["decision"] == "approved"
    assert "cq_impact" in data


def test_decide_returns_400_if_not_in_progress(client):
    """POST /{session_id}/decide returns 400 if session not in_progress."""
    created = _start_session(client)
    # Abandon the session first
    client.post(f"/api/ontology/review/{created['id']}/abandon", json={
        "agent": "tester",
        "reason": "testing",
    })
    resp = client.post(f"/api/ontology/review/{created['id']}/decide", json={
        "element_type": "entity_type",
        "element_name": "Company",
        "decision": "approved",
        "reviewer": "tester",
    })
    assert resp.status_code == 400


def test_cq_impact_preview(client):
    """GET /{session_id}/cq-impact/{name} returns impact preview."""
    created = _start_session(client)
    resp = client.get(
        f"/api/ontology/review/{created['id']}/cq-impact/Company",
        params={"decision": "rejected"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["element_name"] == "Company"
    assert "coverage_before" in data
    assert "coverage_after" in data


def test_progress(client):
    """GET /{session_id}/progress returns progress percentages."""
    created = _start_session(client)
    resp = client.get(f"/api/ontology/review/{created['id']}/progress")
    assert resp.status_code == 200
    data = resp.json()
    assert data["overall_percent"] == 0.0
    assert data["entity_types"]["total"] == 2


def test_complete_ratifies_schema(client):
    """POST /{session_id}/complete ratifies schema on completion."""
    created = _start_session(client)

    # Approve all elements
    for et in SAMPLE_SEED_SCHEMA["entity_types"]:
        client.post(f"/api/ontology/review/{created['id']}/decide", json={
            "element_type": "entity_type",
            "element_name": et["name"],
            "decision": "approved",
            "reviewer": "tester",
        })
    for rel in SAMPLE_SEED_SCHEMA["relationships"]:
        client.post(f"/api/ontology/review/{created['id']}/decide", json={
            "element_type": "relationship",
            "element_name": rel["name"],
            "decision": "approved",
            "reviewer": "tester",
        })

    resp = client.post(f"/api/ontology/review/{created['id']}/complete", json={
        "reviewer": "tester",
        "force": False,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"]["version_number"] == 1
    assert data["session"]["status"] == "completed"


def test_complete_force_false_returns_400_incomplete(client):
    """POST /{session_id}/complete with force=false returns 400 for incomplete review."""
    created = _start_session(client)
    # Don't approve anything
    resp = client.post(f"/api/ontology/review/{created['id']}/complete", json={
        "reviewer": "tester",
        "force": False,
    })
    assert resp.status_code == 400
    assert "Un-reviewed" in resp.json()["detail"]


def test_abandon(client):
    """POST /{session_id}/abandon marks session as abandoned."""
    created = _start_session(client)
    resp = client.post(f"/api/ontology/review/{created['id']}/abandon", json={
        "agent": "tester",
        "reason": "Rerunning discovery",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "abandoned"
