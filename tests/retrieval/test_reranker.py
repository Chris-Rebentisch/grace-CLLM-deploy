"""Tests for cross-encoder reranking (mocked CrossEncoder)."""

from unittest.mock import MagicMock, patch

import numpy as np

from src.retrieval.reranker import CrossEncoderReranker
from src.retrieval.retrieval_models import FusedCandidate


def _fused(grace_id: str, name: str = "test") -> FusedCandidate:
    return FusedCandidate(
        grace_id=grace_id,
        entity_type="Entity",
        name=name,
        properties={"name": name},
        rrf_score=0.5,
        contributing_strategies=["graph"],
        strategy_ranks={"graph": 1},
    )


@patch("src.retrieval.reranker.CrossEncoder")
def test_reranker_returns_top_k_sorted(mock_ce_class):
    """Reranker returns top_k results sorted by rerank_score."""
    mock_model = MagicMock()
    mock_model.predict.return_value = np.array([0.3, 0.9, 0.1, 0.7])
    mock_ce_class.return_value = mock_model

    reranker = CrossEncoderReranker(model_name="test-model")
    candidates = [_fused("a"), _fused("b"), _fused("c"), _fused("d")]
    results = reranker.rerank("test query", candidates, top_k=2)

    assert len(results) == 2
    assert results[0].grace_id == "b"  # score 0.9
    assert results[1].grace_id == "d"  # score 0.7
    assert results[0].rerank_score > results[1].rerank_score


@patch("src.retrieval.reranker.CrossEncoder")
def test_reranker_fewer_than_top_k(mock_ce_class):
    """Reranker with fewer candidates than top_k returns all."""
    mock_model = MagicMock()
    mock_model.predict.return_value = np.array([0.5, 0.8])
    mock_ce_class.return_value = mock_model

    reranker = CrossEncoderReranker(model_name="test-model")
    candidates = [_fused("a"), _fused("b")]
    results = reranker.rerank("query", candidates, top_k=10)

    assert len(results) == 2


@patch("src.retrieval.reranker.CrossEncoder")
def test_reranker_pairs_correctly_formatted(mock_ce_class):
    """Reranker pairs (query, text) correctly formatted for predict()."""
    mock_model = MagicMock()
    mock_model.predict.return_value = np.array([0.5])
    mock_ce_class.return_value = mock_model

    reranker = CrossEncoderReranker(model_name="test-model")
    candidates = [_fused("a", name="Alice")]
    reranker.rerank("find Alice", candidates, top_k=1)

    call_args = mock_model.predict.call_args[0][0]
    assert len(call_args) == 1
    assert call_args[0][0] == "find Alice"
    assert "Alice" in call_args[0][1]


@patch("src.retrieval.reranker.CrossEncoder")
def test_reranker_empty_candidates(mock_ce_class):
    """Reranker handles empty candidates list."""
    mock_ce_class.return_value = MagicMock()

    reranker = CrossEncoderReranker(model_name="test-model")
    results = reranker.rerank("query", [], top_k=10)

    assert results == []
