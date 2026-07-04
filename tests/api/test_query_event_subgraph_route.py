"""API tests for the D267 GET /api/retrieval/query-events/{id}/subgraph route.

Backend integration test. The pipeline + ArcadeClient are mocked because
the route is a thin OpenCypher projection over the live graph; the
contract under test is the response shape (Cytoscape elements) and the
404 / 422 error posture.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient as HttpxTestClient

from src.api.main import app


def _row(q_grace_id: str, e_grace_id: str, rank: int) -> dict:
    """Mocked ArcadeDB row binding for q, r, e variables."""
    return {
        "q": {
            "grace_id": q_grace_id,
            "query_event_id": "qe-123",
            "query_text": "who owns Acme?",
        },
        "r": {
            "grace_id": f"edge-{rank}",
            "rank_ordinal": rank,
            "query_event_id": "qe-123",
        },
        "e": {
            "grace_id": e_grace_id,
            "name": f"entity-{rank}",
            "@type": "Legal_Entity",
        },
    }


@pytest.mark.asyncio
async def test_subgraph_returns_cytoscape_elements_for_known_id():
    """200 path: the route projects MATCH rows into nodes + edges JSON."""
    qeid = str(uuid4())
    fake_arcade = AsyncMock()
    fake_arcade.execute_cypher = AsyncMock(return_value={
        "result": [
            _row("q-grace-1", "ent-a", 1),
            _row("q-grace-1", "ent-b", 2),
        ]
    })

    fake_pipeline = AsyncMock()
    fake_pipeline.client = fake_arcade

    with patch(
        "src.api.retrieval_routes._get_pipeline",
        return_value=fake_pipeline,
    ):
        transport = ASGITransport(app=app)
        async with HttpxTestClient(
            transport=transport, base_url="http://test"
        ) as ac:
            resp = await ac.get(
                f"/api/retrieval/query-events/{qeid}/subgraph"
            )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["query_event_id"] == qeid
    # Query_Event node + 2 retrieved entities = 3 nodes total
    assert len(body["nodes"]) == 3
    # Two retrieved_from edges
    assert len(body["edges"]) == 2
    # Verify node groups (B2 resolution: only query_event + entity)
    groups = {n["data"]["group"] for n in body["nodes"]}
    assert groups == {"query_event", "entity"}
    # Verify NO Response_Event in projection
    types = {n["data"]["type"] for n in body["nodes"]}
    assert "Response_Event" not in types
    # rank_ordinal exposed in API JSON edge data (NB2 — programmatic use only)
    rank_ordinals = sorted(
        e["data"].get("rank_ordinal") for e in body["edges"]
    )
    assert rank_ordinals == [1, 2]


@pytest.mark.asyncio
async def test_subgraph_returns_404_for_unknown_query_event_id():
    """404 path: unknown id returns generic body without UUID echo (§19.1)."""
    qeid = str(uuid4())
    fake_arcade = AsyncMock()
    # Both calls return empty -> 404
    fake_arcade.execute_cypher = AsyncMock(return_value={"result": []})

    fake_pipeline = AsyncMock()
    fake_pipeline.client = fake_arcade

    with patch(
        "src.api.retrieval_routes._get_pipeline",
        return_value=fake_pipeline,
    ):
        transport = ASGITransport(app=app)
        async with HttpxTestClient(
            transport=transport, base_url="http://test"
        ) as ac:
            resp = await ac.get(
                f"/api/retrieval/query-events/{qeid}/subgraph"
            )

    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body == {"detail": "Query event not found"}
    # No UUID echo in error body (security posture §19.1).
    assert qeid not in body["detail"]


@pytest.mark.asyncio
async def test_subgraph_rejects_malformed_uuid_with_422():
    """Malformed path parameter is rejected by FastAPI's UUID validator."""
    transport = ASGITransport(app=app)
    async with HttpxTestClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/retrieval/query-events/not-a-uuid/subgraph")
    assert resp.status_code == 422
