"""Reciprocal Rank Fusion (RRF) — merges multiple ranked lists into one."""

from __future__ import annotations

from src.analytics import metrics as grace_metrics
from src.retrieval.retrieval_models import FusedCandidate, RetrievalCandidate


def _fused_rank_bucket(rank: int) -> str:
    """1-indexed fused rank -> bucket label for §5.4."""
    if rank == 1:
        return "top1"
    if rank <= 5:
        return "top5"
    if rank <= 10:
        return "top10"
    return "tail"


def reciprocal_rank_fusion(
    strategy_results: dict[str, list[RetrievalCandidate]],
    k: int = 60,
) -> list[FusedCandidate]:
    """Merge multiple ranked lists via RRF.

    Formula: score(d) = sum(1 / (k + rank(d, strategy))) for each strategy
    where rank is 1-indexed position in that strategy's results.

    Items are keyed by grace_id for deduplication across strategies.
    Output sorted by RRF score descending.
    """
    # Accumulate scores and metadata per grace_id
    scores: dict[str, float] = {}
    metadata: dict[str, dict] = {}
    strategy_ranks: dict[str, dict[str, int]] = {}

    for strategy_name, candidates in strategy_results.items():
        for rank_idx, candidate in enumerate(candidates):
            gid = candidate.grace_id
            rank = rank_idx + 1  # 1-indexed
            rrf_score = 1.0 / (k + rank)
            scores[gid] = scores.get(gid, 0.0) + rrf_score

            if gid not in metadata:
                metadata[gid] = {
                    "entity_type": candidate.entity_type,
                    "name": candidate.name,
                    "properties": candidate.properties,
                }
                strategy_ranks[gid] = {}

            strategy_ranks[gid][strategy_name] = rank

    # Build fused candidates sorted by score descending
    fused: list[FusedCandidate] = []
    for fused_rank_idx, (gid, score) in enumerate(
        sorted(scores.items(), key=lambda x: x[1], reverse=True)
    ):
        meta = metadata[gid]
        fused_rank = fused_rank_idx + 1
        rank_bucket = _fused_rank_bucket(fused_rank)
        # Chunk 25 §5.4: one increment per result per contributing strategy.
        for strategy_name in strategy_ranks[gid].keys():
            grace_metrics.retrieval_strategy_contributions.add(
                1,
                attributes={
                    "strategy": strategy_name,
                    "fused_rank_bucket": rank_bucket,
                },
            )
        fused.append(
            FusedCandidate(
                grace_id=gid,
                entity_type=meta["entity_type"],
                name=meta["name"],
                properties=meta["properties"],
                rrf_score=score,
                contributing_strategies=list(strategy_ranks[gid].keys()),
                strategy_ranks=strategy_ranks[gid],
            )
        )

    return fused
