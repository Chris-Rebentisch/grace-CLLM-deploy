"""Route-level tests for GET /api/extraction/events (D470)."""

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


def test_list_extraction_events(client):
    """GET /api/extraction/events returns paginated list."""
    resp = client.get("/api/extraction/events")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data


def test_get_extraction_event_200(client, db):
    """GET /api/extraction/events/{event_id} returns 200 for existing event."""
    # Insert a minimal event
    event_id = str(uuid4())
    batch_id = str(uuid4())
    db.execute(
        text(
            "INSERT INTO extraction_events_pg "
            "(event_id, batch_id, source_document_id, status, created_at) "
            "VALUES (:eid, :bid, 'test_doc', 'completed', now())"
        ),
        {"eid": event_id, "bid": batch_id},
    )
    db.commit()

    resp = client.get(f"/api/extraction/events/{event_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["event_id"] == event_id

    # Cleanup
    db.execute(
        text("DELETE FROM extraction_events_pg WHERE event_id = :eid"),
        {"eid": event_id},
    )
    db.commit()


def test_get_extraction_event_404(client):
    """GET /api/extraction/events/{event_id} returns 404 for missing event."""
    resp = client.get(f"/api/extraction/events/{uuid4()}")
    assert resp.status_code == 404
