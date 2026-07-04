"""Tests for retrieval API endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.api.main import app
from src.retrieval.retrieval_config import RetrievalConfig
from src.retrieval.retrieval_models import RankedResult, RetrievalResponse


def _mock_response() -> RetrievalResponse:
    return RetrievalResponse(
        query="test",
        results=[
            RankedResult(
                grace_id="id-1",
                entity_type="Entity",
                name="test",
                rerank_score=0.9,
                rrf_score=0.03,
                contributing_strategies=["graph"],
            )
        ],
        serialized_context="Entity: Entity \"test\"",
        serialization_format="template",
        total_candidates=5,
        strategy_contributions={"graph": 1},
        latency_ms={"graph": 10.0, "fusion": 1.0, "rerank": 5.0},
    )


@pytest.mark.asyncio
@patch("src.api.retrieval_routes._get_pipeline")
async def test_query_endpoint(mock_get_pipeline):
    """POST /api/retrieval/query returns 200 with RetrievalResponse."""
    mock_pipeline = MagicMock()
    mock_pipeline.query = AsyncMock(return_value=_mock_response())
    mock_get_pipeline.return_value = mock_pipeline

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/retrieval/query",
            json={"query_text": "test query"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == "test"
    assert len(data["results"]) == 1


@pytest.mark.asyncio
@patch("src.api.retrieval_routes._get_pipeline")
async def test_build_indexes_endpoint(mock_get_pipeline):
    """POST /api/retrieval/build-indexes returns 200."""
    mock_pipeline = MagicMock()
    mock_pipeline.build_indexes = AsyncMock(return_value=42)
    mock_get_pipeline.return_value = mock_pipeline

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/retrieval/build-indexes")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["entity_count"] == 42


@pytest.mark.asyncio
@patch("src.api.retrieval_routes._get_pipeline")
async def test_config_endpoint(mock_get_pipeline):
    """GET /api/retrieval/config returns current config."""
    mock_pipeline = MagicMock()
    mock_pipeline.config = RetrievalConfig()
    mock_get_pipeline.return_value = mock_pipeline

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/retrieval/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["rrf_k"] == 60
    assert data["embedding_dim"] == 768


@pytest.mark.asyncio
@patch("src.api.retrieval_routes._is_federation_active", return_value=False)
@patch("src.api.retrieval_routes._get_pipeline")
async def test_query_connection_error(mock_get_pipeline, _mock_fed_inactive):
    """POST /api/retrieval/query in non-federated mode returns 503 on ConnectionError.

    With federation inactive (the single-graph default), the route queries the
    local pipeline directly and maps a ``ConnectionError`` to 503 Service
    Unavailable. Federation is forced inactive here so the test does not depend
    on whether the (shared) test DB has leftover federation namespaces. The
    federated path's mother-failure handling — degrade (D498) vs strict 504 —
    is covered in ``tests/retrieval/test_federation_router.py``.
    """
    mock_pipeline = MagicMock()
    mock_pipeline.query = AsyncMock(
        side_effect=ConnectionError("ArcadeDB not reachable")
    )
    mock_get_pipeline.return_value = mock_pipeline

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/retrieval/query",
            json={"query_text": "test"},
        )
    assert resp.status_code == 503
