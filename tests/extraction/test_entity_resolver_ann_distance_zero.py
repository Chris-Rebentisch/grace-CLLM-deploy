"""F-08 regression: ArcadeDB vectorNeighbors() returns distance 0.0 for stale/
un-reindexed vectors, which the old ER path turned into similarity 1.00 and
auto-merged unrelated entities. The fix recomputes cosine client-side from the
neighbor's own ``_embedding`` and never trusts a 0.0 distance for a differently
named neighbor lacking a usable embedding.
"""

from __future__ import annotations

from src.extraction.entity_resolver import (
    _authoritative_similarity,
    _pure_cosine,
)


def test_orthogonal_embedding_not_merged_despite_distance_zero():
    query = [1.0, 0.0, 0.0]
    # ArcadeDB claims distance 0.0 (the bug), but the neighbor's real embedding
    # is orthogonal → true cosine 0.0. Must NOT be treated as a 1.00 match.
    neighbor = {
        "grace_id": "g-other",
        "name": "Completely Different Corp",
        "distance": 0.0,
        "_embedding": [0.0, 1.0, 0.0],
    }
    sim = _authoritative_similarity(query, neighbor, extracted_name="Acme Inc")
    assert sim is not None
    assert sim < 0.5, "orthogonal embedding must not merge even at distance 0.0"


def test_identical_embedding_is_high_similarity():
    query = [0.3, 0.4, 0.5]
    neighbor = {
        "grace_id": "g-same",
        "name": "Acme Inc",
        "distance": 0.0,
        "_embedding": [0.3, 0.4, 0.5],
    }
    sim = _authoritative_similarity(query, neighbor, extracted_name="Acme Inc")
    assert sim is not None and sim > 0.99


def test_distance_zero_no_embedding_different_name_dropped():
    # No embedding to verify + distance 0.0 sentinel + different name → drop.
    neighbor = {"grace_id": "g", "name": "Foo Corp", "distance": 0.0}
    sim = _authoritative_similarity([1.0, 2.0], neighbor, extracted_name="Bar LLC")
    assert sim is None


def test_distance_zero_no_embedding_exact_name_trusted():
    neighbor = {"grace_id": "g", "name": "Acme Inc", "distance": 0.0}
    sim = _authoritative_similarity([1.0, 2.0], neighbor, extracted_name="acme inc")
    assert sim == 1.0


def test_real_nonzero_distance_falls_back():
    neighbor = {"grace_id": "g", "name": "X", "distance": 0.3}
    sim = _authoritative_similarity([1.0], neighbor, extracted_name="Y")
    assert abs(sim - 0.7) < 1e-9


def test_pure_cosine_basic():
    assert _pure_cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert abs(_pure_cosine([1.0, 0.0], [0.0, 1.0])) < 1e-9
    assert _pure_cosine([], [1.0]) is None
    assert _pure_cosine([0.0, 0.0], [1.0, 1.0]) is None
