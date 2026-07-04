"""Tests for semantic search strategy (mocked Ollama)."""

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from src.retrieval.semantic_strategy import (
    SemanticSearchIndex,
    cosine_similarity,
    embed_texts,
)


def _mock_response(data: dict) -> MagicMock:
    """Create a mock httpx Response with .json() returning data."""
    resp = MagicMock()
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


@pytest.mark.asyncio
@patch("src.shared.embeddings.httpx.AsyncClient")
async def test_build_index_embeds_all(mock_client_class):
    """Build index embeds all entities via Ollama."""
    mock_client = AsyncMock()
    mock_client.post.return_value = _mock_response({
        "embeddings": [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    })
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client_class.return_value = mock_client

    index = SemanticSearchIndex("http://localhost:11434")
    entities = [("id-1", "Person: Alice"), ("id-2", "Company: Acme")]
    await index.build_index(entities)

    assert len(index.grace_ids) == 2
    assert index.embeddings is not None
    assert index.embeddings.shape == (2, 3)


@pytest.mark.asyncio
@patch("src.shared.embeddings.httpx.AsyncClient")
async def test_search_returns_top_k(mock_client_class):
    """Search returns top-K by cosine similarity."""
    mock_client = AsyncMock()

    call_count = 0

    async def mock_post(url, json=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _mock_response({
                "embeddings": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.7, 0.7, 0.0]]
            })
        else:
            return _mock_response({"embeddings": [[0.9, 0.1, 0.0]]})

    mock_client.post = mock_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client_class.return_value = mock_client

    index = SemanticSearchIndex("http://localhost:11434")
    entities = [("id-1", "Alice"), ("id-2", "Bob"), ("id-3", "Charlie")]
    await index.build_index(entities)
    results = await index.search("find Alice", top_k=2)

    assert len(results) <= 2
    assert results[0].strategy == "semantic"
    assert results[0].score > results[1].score


@pytest.mark.asyncio
async def test_search_empty_index():
    """Search with empty index returns empty."""
    index = SemanticSearchIndex("http://localhost:11434")
    results = await index.search("test query")
    assert results == []


@pytest.mark.asyncio
@patch("src.shared.embeddings.httpx.AsyncClient")
async def test_embed_call_format(mock_client_class):
    """Embedding call formats correctly for Ollama /api/embed."""
    mock_client = AsyncMock()
    mock_client.post.return_value = _mock_response({"embeddings": [[0.1, 0.2]]})
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client_class.return_value = mock_client

    await embed_texts(["hello"], "http://localhost:11434", "nomic-embed-text")

    call_args = mock_client.post.call_args
    assert "/api/embed" in call_args[0][0]
    body = call_args[1].get("json", call_args[0][1] if len(call_args[0]) > 1 else None)
    assert body["model"] == "nomic-embed-text"
    assert body["input"] == ["hello"]


def test_cosine_similarity_rankings():
    """Cosine similarity produces correct rankings."""
    query = np.array([1.0, 0.0, 0.0])
    matrix = np.array([
        [1.0, 0.0, 0.0],  # identical
        [0.0, 1.0, 0.0],  # orthogonal
        [0.7, 0.7, 0.0],  # partial match
    ])
    sims = cosine_similarity(query, matrix)
    assert sims[0] > sims[2] > sims[1]
    assert abs(sims[0] - 1.0) < 1e-6
    assert abs(sims[1] - 0.0) < 1e-6
