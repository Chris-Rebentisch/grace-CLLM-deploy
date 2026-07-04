"""API tests for Chunk 28 D212 neighborhood route.

Wraps the existing `fetch_entity_neighborhood` unchanged; this suite
covers the HTTP-layer contract only.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient as HttpxTestClient

from src.api.main import app


def _neighborhood_payload(seed_id: str = "seed-1", depth: int = 1) -> dict:
    """Return a fake `fetch_entity_neighborhood` response suitable for the depth."""
    neighbors = [{"grace_id": f"n-{i}", "@type": "Legal_Entity"} for i in range(2)]
    edges = [
        {
            "grace_id": f"e-{i}",
            "source_grace_id": seed_id,
            "target_grace_id": f"n-{i}",
            "relationship_type": "owns",
        }
        for i in range(2)
    ]
    if depth >= 2:
        neighbors.append({"grace_id": "n-depth2", "@type": "Contract"})
        edges.append(
            {
                "grace_id": "e-depth2",
                "source_grace_id": "n-0",
                "target_grace_id": "n-depth2",
                "relationship_type": "references",
            }
        )
    return {
        "seed": {"grace_id": seed_id, "@type": "Legal_Entity"},
        "neighbors": neighbors,
        "edges": edges,
    }


@pytest.mark.asyncio
async def test_neighborhood_depth_1_default():
    with patch("src.api.graph_routes._get_client") as mock_get_client, patch(
        "src.api.graph_routes.fetch_entity_neighborhood",
        new=AsyncMock(return_value=_neighborhood_payload("seed-1", depth=1)),
    ), patch(
        "src.api.graph_routes.get_entity",
        new=AsyncMock(return_value={"grace_id": "seed-1"}),
    ):
        mock_get_client.return_value = AsyncMock()

        transport = ASGITransport(app=app)
        async with HttpxTestClient(
            transport=transport, base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/graph/entities/seed-1/neighborhood")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {"seed", "neighbors", "edges"}
        assert body["seed"]["grace_id"] == "seed-1"
        assert len(body["neighbors"]) == 2
        assert len(body["edges"]) == 2


@pytest.mark.asyncio
async def test_neighborhood_depth_2_expands():
    captured = {}

    async def fake_fetch(client, grace_id, max_depth=2):
        captured["max_depth"] = max_depth
        return _neighborhood_payload(grace_id, depth=max_depth)

    with patch("src.api.graph_routes._get_client") as mock_get_client, patch(
        "src.api.graph_routes.fetch_entity_neighborhood", side_effect=fake_fetch
    ), patch(
        "src.api.graph_routes.get_entity",
        new=AsyncMock(return_value={"grace_id": "seed-1"}),
    ):
        mock_get_client.return_value = AsyncMock()

        transport = ASGITransport(app=app)
        async with HttpxTestClient(
            transport=transport, base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/graph/entities/seed-1/neighborhood?depth=2")
        assert resp.status_code == 200
        assert captured["max_depth"] == 2
        body = resp.json()
        assert len(body["neighbors"]) == 3  # 2 depth-1 + 1 depth-2


@pytest.mark.asyncio
async def test_neighborhood_depth_3_rejected_422():
    transport = ASGITransport(app=app)
    async with HttpxTestClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/graph/entities/seed-1/neighborhood?depth=3")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_neighborhood_invalid_grace_id_returns_404():
    with patch("src.api.graph_routes._get_client") as mock_get_client, patch(
        "src.api.graph_routes.get_entity",
        new=AsyncMock(return_value=None),
    ):
        mock_get_client.return_value = AsyncMock()

        transport = ASGITransport(app=app)
        async with HttpxTestClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/graph/entities/ghost-id/neighborhood")
        assert resp.status_code == 404
