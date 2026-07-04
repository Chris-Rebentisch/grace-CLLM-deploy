"""F-0025c / ISS-0051 — claim accept 422 carries resolve suggestions.

A plain accept whose endpoint names don't resolve to graph entities
previously returned an opaque 422 string. The 422 detail is now structured:
``{message, suggestions, hint}`` where suggestions carries up to 5 nearest
matching entity candidates per unresolved endpoint.

Pure unit tests — DB session and ArcadeDB client are mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.claim_routes import _suggest_resolution_candidates, router
from src.extraction.claim_models import Claim, ClaimStatus, ClaimVerdict


def _relationship_claim(**overrides) -> Claim:
    fields = dict(
        relationship_type="owns",
        subject_name="Sablewood Holdings",
        subject_type="Legal_Entity",
        object_name="Kestrel Road tract",
        object_type="Property",
        predicate="owns",
        verdict=ClaimVerdict.REFUTED,
        status=ClaimStatus.QUARANTINED,
    )
    fields.update(overrides)
    return Claim(**fields)


class _FakeArcade:
    """execute_cypher stub returning canned candidate rows."""

    def __init__(self, rows):
        self.rows = rows
        self.queries: list[str] = []

    async def execute_cypher(self, query, *args, **kwargs):
        self.queries.append(query)
        return {"result": self.rows}


# --- Helper-level tests ------------------------------------------------------


@pytest.mark.asyncio
async def test_suggestions_returned_for_unresolved_object():
    arcade = _FakeArcade(
        [
            {"name": "Kestrel Road Parcel", "grace_id": "gid-1"},
            {"name": "Kestrel Estate", "grace_id": "gid-2"},
        ]
    )
    claim = _relationship_claim()
    suggestions = await _suggest_resolution_candidates(arcade, claim)

    assert "object" in suggestions
    names = [s["name"] for s in suggestions["object"]]
    assert "Kestrel Road Parcel" in names
    assert all(s["entity_type"] == "Property" for s in suggestions["object"])


@pytest.mark.asyncio
async def test_suggestions_capped_at_limit():
    arcade = _FakeArcade(
        [{"name": f"Candidate {i}", "grace_id": f"gid-{i}"} for i in range(10)]
    )
    claim = _relationship_claim()
    suggestions = await _suggest_resolution_candidates(arcade, claim, limit=5)
    assert len(suggestions["object"]) <= 5


@pytest.mark.asyncio
async def test_suggestions_best_effort_on_lookup_failure():
    """A lookup exception yields empty suggestions, never a raised error."""

    class _BrokenArcade:
        async def execute_cypher(self, query, *args, **kwargs):
            raise RuntimeError("graph down")

    claim = _relationship_claim()
    suggestions = await _suggest_resolution_candidates(_BrokenArcade(), claim)
    assert suggestions["object"] == []


@pytest.mark.asyncio
async def test_unsafe_entity_type_never_reaches_cypher_label():
    arcade = _FakeArcade([])
    claim = _relationship_claim(object_type="Bad Type; DROP")
    await _suggest_resolution_candidates(arcade, claim)
    for q in arcade.queries:
        assert "DROP" not in q


@pytest.mark.asyncio
async def test_entity_claim_returns_no_suggestions():
    arcade = _FakeArcade([{"name": "X", "grace_id": "g"}])
    claim = Claim(
        entity_type="Person",
        subject_name="Amelia",
        predicate="entity",
        verdict=ClaimVerdict.REFUTED,
        status=ClaimStatus.QUARANTINED,
    )
    suggestions = await _suggest_resolution_candidates(arcade, claim)
    assert suggestions == {}


# --- Route-level test --------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    from src.graph.arcade_client import get_arcade_client
    from src.shared.database import get_db

    app = FastAPI()
    app.include_router(router)

    mock_db = MagicMock()
    arcade = _FakeArcade(
        [{"name": "Kestrel Road Parcel", "grace_id": "gid-1"}]
    )

    def _override_db():
        yield mock_db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_arcade_client] = lambda: arcade

    claim = _relationship_claim()
    monkeypatch.setattr(
        "src.api.claim_routes.get_claim", lambda db, cid: claim
    )
    monkeypatch.setattr(
        "src.api.claim_routes.promote_claim_to_graph",
        AsyncMock(
            side_effect=ValueError(
                f"Relationship claim {claim.claim_id} cannot be promoted: "
                "could not resolve object Property:Kestrel Road tract "
                "against the current graph."
            )
        ),
    )

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


def test_plain_accept_unresolvable_object_422_carries_suggestions(client):
    resp = client.post(
        "/api/claims/some-claim-id/accept",
        json={"reviewer": "ops@example.com"},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert isinstance(detail, dict)
    assert "could not resolve" in detail["message"]
    names = [s["name"] for s in detail["suggestions"]["object"]]
    assert "Kestrel Road Parcel" in names
    assert "Edit-and-Accept" in detail["hint"]


# --- Edit-and-Accept promote path (ISS-0051 deferral, closed 2026-07-03) ----


def _edit_accept_app(monkeypatch, promote_mock):
    """App wired for the Edit-and-Accept path with a patchable promote."""
    from src.graph.arcade_client import get_arcade_client
    from src.shared.database import get_db

    app = FastAPI()
    app.include_router(router)

    mock_db = MagicMock()
    arcade = _FakeArcade(
        [{"name": "Kestrel Road Parcel", "grace_id": "gid-1"}]
    )

    def _override_db():
        yield mock_db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_arcade_client] = lambda: arcade

    original = _relationship_claim()
    monkeypatch.setattr(
        "src.api.claim_routes.get_claim", lambda db, cid: original
    )
    monkeypatch.setattr("src.api.claim_routes.insert_claim", MagicMock())
    monkeypatch.setattr(
        "src.api.claim_routes.update_claim_status", MagicMock()
    )
    monkeypatch.setattr(
        "src.api.claim_routes.promote_claim_to_graph", promote_mock
    )
    return app, mock_db, original


def test_edit_and_accept_unresolvable_name_422_carries_suggestions(monkeypatch):
    """The promote path previously let the resolution ValueError surface as
    a raw 500 — it must return the same enriched 422 as plain accept."""
    promote = AsyncMock(
        side_effect=ValueError(
            "Relationship claim x cannot be promoted: could not resolve "
            "object Property:Kestrel Road tract against the current graph."
        )
    )
    app, mock_db, _ = _edit_accept_app(monkeypatch, promote)

    with TestClient(app) as c:
        resp = c.post(
            "/api/claims/some-claim-id/accept",
            json={
                "reviewer": "ops@example.com",
                "modified_claim": {
                    "subject_name": "Sablewood Holdings",
                    "object_name": "Kestrel Road tract",
                },
            },
        )
    app.dependency_overrides.clear()

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert isinstance(detail, dict)
    assert "could not resolve" in detail["message"]
    names = [s["name"] for s in detail["suggestions"]["object"]]
    assert "Kestrel Road Parcel" in names
    assert "Edit-and-Accept" in detail["hint"]
    # The 422 raised before db.commit(): the superseding INSERT and the
    # SUPERSEDED status flip must not have been committed.
    mock_db.commit.assert_not_called()


def test_edit_and_accept_happy_path_unchanged(monkeypatch):
    """A resolvable Edit-and-Accept still promotes and returns 200."""
    promote = AsyncMock(return_value={"entities_written": 1})
    app, mock_db, original = _edit_accept_app(monkeypatch, promote)

    with TestClient(app) as c:
        resp = c.post(
            "/api/claims/some-claim-id/accept",
            json={
                "reviewer": "ops@example.com",
                "modified_claim": {
                    "subject_name": "Sablewood Holdings",
                    "object_name": "Kestrel Road Parcel",
                },
            },
        )
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["graph_write_result"] == {"entities_written": 1}
    assert body["superseded_claim_id"] == original.claim_id
    promote.assert_awaited_once()
    mock_db.commit.assert_called_once()
