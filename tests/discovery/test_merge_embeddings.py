"""Tests for Tier 1: embedding generation, similarity computation, and HDBSCAN clustering."""

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from src.discovery.merge_embeddings import (
    cluster_embeddings,
    compute_similarity_matrix,
    embed_texts,
    run_tier1,
)
from src.discovery.merge_models import HDBSCANResult, Tier1Result


def _make_mock_resp(status_code, json_data=None):
    """Create a mock httpx Response with sync .json() and .raise_for_status()."""
    resp = MagicMock()
    resp.status_code = status_code
    if json_data is not None:
        resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    return resp


# --- embed_texts tests ---


@pytest.mark.asyncio
async def test_embed_texts_success():
    """Mock Ollama POST /api/embed response, verify returns list of lists."""
    embeddings = [[0.1] * 384, [0.2] * 384]
    mock_resp = _make_mock_resp(200, {"embeddings": embeddings})

    with patch("src.discovery.merge_embeddings.httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.post.return_value = mock_resp
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_instance

        result = await embed_texts(["hello", "world"])
        assert len(result) == 2
        assert len(result[0]) == 384
        assert result[0][0] == pytest.approx(0.1)
        assert result[1][0] == pytest.approx(0.2)


@pytest.mark.asyncio
async def test_embed_texts_error():
    """Mock connection error, verify exception raised after retries."""
    import httpx as httpx_mod

    with patch("src.discovery.merge_embeddings.httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.post.side_effect = httpx_mod.ConnectError("Connection refused")
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_instance

        with pytest.raises(RuntimeError, match="failed after 3 attempts"):
            await embed_texts(["test"])


@pytest.mark.asyncio
async def test_embed_texts_empty_input():
    """Empty list input returns empty embeddings list."""
    mock_resp = _make_mock_resp(200, {"embeddings": []})

    with patch("src.discovery.merge_embeddings.httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.post.return_value = mock_resp
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_instance

        result = await embed_texts([])
        assert result == []


# --- compute_similarity_matrix tests ---


def test_compute_similarity_matrix_identity():
    """Same vector should have similarity 1.0 with itself."""
    vec = [1.0, 0.0, 0.0]
    embeddings = [vec, vec]
    sim = compute_similarity_matrix(embeddings)
    assert sim[0][0] == pytest.approx(1.0)
    assert sim[0][1] == pytest.approx(1.0)
    assert sim[1][0] == pytest.approx(1.0)


def test_compute_similarity_matrix_symmetry():
    """Similarity matrix should be symmetric."""
    np.random.seed(42)
    embeddings = [np.random.randn(384).tolist() for _ in range(5)]
    sim = compute_similarity_matrix(embeddings)
    for i in range(5):
        for j in range(5):
            assert sim[i][j] == pytest.approx(sim[j][i], abs=1e-6)


def test_compute_similarity_matrix_orthogonal():
    """Orthogonal vectors should have similarity ~0.0."""
    vec_a = [1.0, 0.0, 0.0]
    vec_b = [0.0, 1.0, 0.0]
    sim = compute_similarity_matrix([vec_a, vec_b])
    assert sim[0][1] == pytest.approx(0.0, abs=1e-6)


# --- cluster_embeddings tests ---


def test_cluster_embeddings_basic():
    """Create 3 tight groups of 3 similar vectors + 2 outliers, verify HDBSCAN produces clusters."""
    np.random.seed(42)
    group1 = [np.random.normal(1.0, 0.01, 384).tolist() for _ in range(3)]
    group2 = [np.random.normal(-1.0, 0.01, 384).tolist() for _ in range(3)]
    group3 = [np.random.normal(0.5, 0.01, 384).tolist() for _ in range(3)]
    outlier1 = [np.random.normal(0.0, 1.0, 384).tolist()]
    outlier2 = [np.random.normal(0.0, 1.0, 384).tolist()]

    all_vecs = group1 + group2 + group3 + outlier1 + outlier2
    config = {"min_cluster_size": 2, "cluster_selection_method": "eom"}

    result = cluster_embeddings(all_vecs, config)

    assert isinstance(result, HDBSCANResult)
    assert len(result.labels) == 11
    assert result.n_clusters >= 1  # At least some clusters found
    assert result.n_clusters + result.n_noise > 0


def test_cluster_embeddings_all_same():
    """All identical vectors should cluster together."""
    vec = [1.0] * 384
    all_vecs = [vec for _ in range(5)]
    config = {"min_cluster_size": 2}

    result = cluster_embeddings(all_vecs, config)
    assert isinstance(result, HDBSCANResult)
    # All identical -> should be in the same cluster or all noise
    unique_labels = set(l for l in result.labels if l >= 0)
    assert len(unique_labels) <= 1


def test_cluster_embeddings_min_cluster_size():
    """Config min_cluster_size is respected by HDBSCAN."""
    np.random.seed(42)
    # Two tight groups of 5 each
    group1 = [np.random.normal(1.0, 0.01, 384).tolist() for _ in range(5)]
    group2 = [np.random.normal(-1.0, 0.01, 384).tolist() for _ in range(5)]
    all_vecs = group1 + group2

    # With min_cluster_size=3, clusters need at least 3 members
    config = {"min_cluster_size": 3}
    result = cluster_embeddings(all_vecs, config)
    assert isinstance(result, HDBSCANResult)
    # Each group of 5 should still form a cluster with min_cluster_size=3
    for label in set(result.labels):
        if label >= 0:
            count = result.labels.count(label)
            assert count >= 3


# --- Tier1Result construction tests ---


@pytest.mark.asyncio
async def test_tier1_result_construction():
    """Mock embed_texts, verify Tier1Result fields populated."""
    np.random.seed(42)
    group1 = [np.random.normal(1.0, 0.01, 384).tolist() for _ in range(3)]
    group2 = [np.random.normal(-1.0, 0.01, 384).tolist() for _ in range(3)]
    mock_embeddings = group1 + group2

    from src.discovery.cq_models import CQSource, CompetencyQuestion

    cqs = []
    for i in range(6):
        with patch("src.discovery.cq_models.get_valid_domains", return_value=["insurance", "legal", "other"]):
            cq = CompetencyQuestion(
                canonical_text=f"Test question {i}?",
                source=CQSource.LLM_TOP_DOWN,
                source_pass="top_down",
            )
            cqs.append(cq)

    with patch("src.discovery.merge_embeddings.embed_texts", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = mock_embeddings
        config = {"min_cluster_size": 2}
        result = await run_tier1(cqs, config)

    assert isinstance(result, Tier1Result)
    assert len(result.embeddings) == 6
    assert len(result.similarity_matrix) == 6
    assert len(result.similarity_matrix[0]) == 6
    assert isinstance(result.hdbscan_result, HDBSCANResult)
    # cluster_groups + singletons should account for all CQs
    all_indices = []
    for indices in result.cluster_groups.values():
        all_indices.extend(indices)
    all_indices.extend(result.singleton_indices)
    assert sorted(all_indices) == list(range(6))


def test_hdbscan_result_fields():
    """Verify HDBSCANResult has expected fields after clustering."""
    np.random.seed(42)
    group1 = [np.random.normal(1.0, 0.01, 384).tolist() for _ in range(4)]
    group2 = [np.random.normal(-1.0, 0.01, 384).tolist() for _ in range(4)]
    all_vecs = group1 + group2
    config = {"min_cluster_size": 2}

    result = cluster_embeddings(all_vecs, config)

    assert hasattr(result, "labels")
    assert hasattr(result, "probabilities")
    assert hasattr(result, "cluster_persistence")
    assert hasattr(result, "soft_memberships")
    assert hasattr(result, "n_clusters")
    assert hasattr(result, "n_noise")
    assert len(result.labels) == 8
    assert len(result.probabilities) == 8
    assert isinstance(result.n_clusters, int)
    assert isinstance(result.n_noise, int)
