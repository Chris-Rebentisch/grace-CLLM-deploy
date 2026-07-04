"""Tests for GET /api/communications/profiles (Chunk 60, CP1).

Shape verification + cursor pagination.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from src.api.main import app

    return TestClient(app)


def _make_profile_row(idx: int = 0):
    """Fake row tuple matching the query column order."""
    from datetime import datetime, timezone

    return (
        f"id-{idx}",                                  # id
        f"person-{idx}",                               # sender_person_id
        idx + 1,                                       # profile_version
        {"sentence_length_band": "medium"},            # style_signature (JSONB)
        "high",                                        # profile_quality_band
        datetime(2026, 1, 1, tzinfo=timezone.utc),     # created_at
    )


def test_profiles_list_empty_returns_empty_items(client):
    """Empty DB returns {items: [], next_cursor: null}."""
    mock_session = MagicMock()
    mock_session.execute.return_value.fetchall.return_value = []

    with patch("src.api.communications_routes.get_session_factory", return_value=lambda: mock_session):
        resp = client.get("/api/communications/profiles")

    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["next_cursor"] is None


def test_profiles_list_pagination(client):
    """When more rows exist than limit, next_cursor is set."""
    rows = [_make_profile_row(i) for i in range(4)]  # 4 rows; limit=3 → has_more

    mock_session = MagicMock()
    mock_session.execute.return_value.fetchall.return_value = rows

    with patch("src.api.communications_routes.get_session_factory", return_value=lambda: mock_session):
        resp = client.get("/api/communications/profiles?limit=3")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 3
    assert body["next_cursor"] == "3"
    # Verify response shape has person_id (mapped from sender_person_id)
    assert "person_id" in body["items"][0]
    assert body["items"][0]["person_id"] == "person-0"
