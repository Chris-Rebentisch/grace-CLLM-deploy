"""Tests for Reciprocal Rank Fusion."""

from src.retrieval.fusion import reciprocal_rank_fusion
from src.retrieval.retrieval_models import RetrievalCandidate


def _candidate(grace_id: str, strategy: str, rank: int = 0) -> RetrievalCandidate:
    return RetrievalCandidate(
        grace_id=grace_id,
        entity_type="Entity",
        name=f"entity-{grace_id}",
        strategy=strategy,
        rank=rank,
    )


def test_rrf_two_strategies_item_in_both():
    """Item in both strategies gets sum of reciprocal ranks."""
    results = {
        "graph": [_candidate("a", "graph"), _candidate("b", "graph")],
        "semantic": [_candidate("a", "semantic"), _candidate("c", "semantic")],
    }
    fused = reciprocal_rank_fusion(results, k=60)
    scores = {f.grace_id: f.rrf_score for f in fused}
    # "a" is rank 1 in both → 1/(60+1) + 1/(60+1)
    expected_a = 2.0 / 61
    assert abs(scores["a"] - expected_a) < 1e-9
    # "a" should be highest
    assert fused[0].grace_id == "a"


def test_rrf_three_strategies_item_in_one():
    """Item only in one strategy gets score from that one only."""
    results = {
        "graph": [_candidate("a", "graph")],
        "semantic": [_candidate("b", "semantic")],
        "bm25": [_candidate("c", "bm25")],
    }
    fused = reciprocal_rank_fusion(results, k=60)
    # All three have same score: 1/(60+1)
    assert len(fused) == 3
    for f in fused:
        assert abs(f.rrf_score - 1.0 / 61) < 1e-9


def test_rrf_deduplicates_by_grace_id():
    """Same grace_id from multiple strategies appears once in output."""
    results = {
        "graph": [_candidate("a", "graph")],
        "semantic": [_candidate("a", "semantic")],
        "bm25": [_candidate("a", "bm25")],
    }
    fused = reciprocal_rank_fusion(results, k=60)
    assert len(fused) == 1
    assert fused[0].grace_id == "a"
    assert abs(fused[0].rrf_score - 3.0 / 61) < 1e-9


def test_rrf_sorted_descending():
    """Output is sorted by RRF score descending."""
    results = {
        "graph": [_candidate("a", "graph"), _candidate("b", "graph")],
        "semantic": [_candidate("b", "semantic"), _candidate("a", "semantic")],
    }
    fused = reciprocal_rank_fusion(results, k=60)
    # Both appear in both strategies:
    # a: rank 1 in graph (1/61) + rank 2 in semantic (1/62)
    # b: rank 2 in graph (1/62) + rank 1 in semantic (1/61)
    # Both have same score, but order should still be consistent
    assert fused[0].rrf_score >= fused[1].rrf_score


def test_rrf_empty_strategies():
    """Empty strategy list returns empty result."""
    fused = reciprocal_rank_fusion({}, k=60)
    assert fused == []


def test_rrf_contributing_strategies():
    """contributing_strategies correctly populated."""
    results = {
        "graph": [_candidate("a", "graph")],
        "semantic": [_candidate("a", "semantic")],
    }
    fused = reciprocal_rank_fusion(results, k=60)
    assert set(fused[0].contributing_strategies) == {"graph", "semantic"}
    assert fused[0].strategy_ranks == {"graph": 1, "semantic": 1}
