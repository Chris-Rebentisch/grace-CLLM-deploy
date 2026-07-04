"""Route-level tests for GET /api/claims/{claim_id} (D470)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.api.main import app
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


def test_get_claim_200(client, db):
    """GET /api/claims/{claim_id} returns 200 for existing claim."""
    claim_id = str(uuid4())
    db.execute(
        text(
            "INSERT INTO extraction_claims "
            "(claim_id, extraction_unit_id, subject_name, predicate, "
            "source_document_id, source_chunk_id, status, decision_source, created_at) "
            "VALUES (:cid, 'unit1', 'TestEntity', 'has_prop', "
            "'doc1', 'chunk1', 'quarantined', 'llm', now())"
        ),
        {"cid": claim_id},
    )
    db.commit()

    resp = client.get(f"/api/claims/{claim_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["claim_id"] == claim_id
    assert data["subject_name"] == "TestEntity"

    # Cleanup
    db.execute(
        text("DELETE FROM extraction_claims WHERE claim_id = :cid"),
        {"cid": claim_id},
    )
    db.commit()


def test_get_claim_404(client):
    """GET /api/claims/{claim_id} returns 404 for missing claim."""
    resp = client.get(f"/api/claims/{uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Claim not found"
