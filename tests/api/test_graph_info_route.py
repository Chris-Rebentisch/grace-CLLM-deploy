"""F-0001 / ISS-0042 — GET /api/graph/info names the bound database.

The raw ArcadeDB *server* info blob never says which database this API
instance is bound to, making sandbox/live verification ("confirm the API
is bound to grace_test") unsatisfiable. The route now returns a
``database`` field carrying the ArcadeConfig-resolved database name.
Pure unit tests — ArcadeClient fully mocked, no services.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient as HttpxTestClient

from src.api.main import app


def _mock_client(database: str = "grace_test") -> MagicMock:
    client = MagicMock()
    client.config = MagicMock()
    client.config.database = database
    client.health_check = AsyncMock(
        return_value={"version": "26.5.1", "databases": [database]}
    )
    return client


@pytest.mark.asyncio
async def test_graph_info_names_bound_database():
    """Response carries the ArcadeConfig-resolved database name."""
    with patch(
        "src.api.graph_routes._get_client", return_value=_mock_client("grace_test")
    ):
        transport = ASGITransport(app=app)
        async with HttpxTestClient(
            transport=transport, base_url="http://test"
        ) as http:
            resp = await http.get("/api/graph/info")

    assert resp.status_code == 200
    body = resp.json()
    assert body["database"] == "grace_test"
    # The raw server blob is still present (unchanged contract, additive field).
    assert body["server"] == {"version": "26.5.1", "databases": ["grace_test"]}


@pytest.mark.asyncio
async def test_graph_info_database_follows_config():
    """A differently-bound client reports its own database name."""
    with patch(
        "src.api.graph_routes._get_client", return_value=_mock_client("grace")
    ):
        transport = ASGITransport(app=app)
        async with HttpxTestClient(
            transport=transport, base_url="http://test"
        ) as http:
            resp = await http.get("/api/graph/info")

    assert resp.status_code == 200
    assert resp.json()["database"] == "grace"


@pytest.mark.asyncio
async def test_graph_info_unavailable_still_503():
    """ArcadeDB failure path unchanged: 503, no database leakage."""
    client = _mock_client()
    client.health_check = AsyncMock(side_effect=ConnectionError("down"))
    with patch("src.api.graph_routes._get_client", return_value=client):
        transport = ASGITransport(app=app)
        async with HttpxTestClient(
            transport=transport, base_url="http://test"
        ) as http:
            resp = await http.get("/api/graph/info")

    assert resp.status_code == 503
