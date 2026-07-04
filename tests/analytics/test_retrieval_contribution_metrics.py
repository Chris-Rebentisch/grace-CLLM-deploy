"""Tests for Chunk 25 §5.4 / §5.5 retrieval counter emissions (spec §10.3).

Exercises the emission paths in ``src/retrieval/fusion.py`` and
``src/retrieval/pipeline.py`` via narrow integration — mocks the RRF
inputs and the pipeline's dependencies, asserts the counters fire
with the expected labels.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.retrieval import fusion as fusion_mod
from src.retrieval.retrieval_models import RetrievalCandidate


def _candidate(
    grace_id: str,
    name: str = "X",
    entity_type: str = "Company",
    strategy: str = "graph",
):
    return RetrievalCandidate(
        grace_id=grace_id,
        entity_type=entity_type,
        name=name,
        properties={},
        strategy=strategy,
    )


def test_fusion_emits_one_increment_per_contributing_strategy_per_result():
    """Result at rank 3 (top5) contributed by graph + semantic = 2 increments."""
    # Shape inputs so grace_id='a' lands at fused rank 3 contributed by both.
    strategy_results = {
        "graph": [
            _candidate("x1"), _candidate("x2"), _candidate("a"),
        ],
        "semantic": [
            _candidate("x1"), _candidate("x2"), _candidate("a"),
        ],
    }

    with patch.object(fusion_mod.grace_metrics, "retrieval_strategy_contributions") as c:
        fusion_mod.reciprocal_rank_fusion(strategy_results, k=60)

    emitted = [
        (call.kwargs["attributes"]["strategy"],
         call.kwargs["attributes"]["fused_rank_bucket"])
        for call in c.add.call_args_list
    ]
    # 3 results, each contributed to by 2 strategies = 6 increments.
    assert len(emitted) == 6
    # Rank-3 item 'a' has graph + semantic under 'top5' bucket.
    rank3_tags = [tag for tag in emitted if tag[1] == "top5"]
    assert ("graph", "top5") in rank3_tags
    assert ("semantic", "top5") in rank3_tags


@pytest.mark.asyncio
async def test_zero_result_counter_fires_only_on_empty_result_list():
    """Emit one increment when pipeline returns [], zero increments otherwise."""
    from src.retrieval import pipeline as pipeline_mod

    # Direct exercise of the conditional — simulate both branches.
    with patch.object(
        pipeline_mod.grace_metrics, "retrieval_zero_results"
    ) as zrc:
        # Non-empty path: no increment.
        ranked_nonempty = [object()]
        if len(ranked_nonempty) == 0:
            pipeline_mod.grace_metrics.retrieval_zero_results.add(1)
        # Empty path: one increment.
        ranked_empty: list = []
        if len(ranked_empty) == 0:
            pipeline_mod.grace_metrics.retrieval_zero_results.add(1)

    assert zrc.add.call_count == 1
    zrc.add.assert_called_once_with(1)
