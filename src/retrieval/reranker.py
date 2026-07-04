"""Cross-encoder reranking wrapper using sentence-transformers."""

from __future__ import annotations

from sentence_transformers import CrossEncoder

from src.retrieval.retrieval_models import FusedCandidate, RankedResult
from src.retrieval.text_representation import entity_to_text


class CrossEncoderReranker:
    """Wrapper around sentence-transformers CrossEncoder.

    Loads ms-marco-MiniLM-L-6-v2 on CPU. Reranks top-N RRF candidates.
    """

    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"):
        self.model = CrossEncoder(model_name)

    def rerank(
        self,
        query: str,
        candidates: list[FusedCandidate],
        top_k: int = 10,
    ) -> list[RankedResult]:
        """Score each candidate against the query, return top-K by rerank score.

        Pairs: [(query, candidate_text), ...] for each candidate.
        candidate_text = entity_to_text(candidate.entity_type, candidate.properties)
        """
        if not candidates:
            return []

        pairs = [
            [query, entity_to_text(c.entity_type, c.properties)]
            for c in candidates
        ]
        scores = self.model.predict(pairs)

        # Pair scores with candidates
        scored = list(zip(scores, candidates))
        scored.sort(key=lambda x: float(x[0]), reverse=True)

        results: list[RankedResult] = []
        for score, candidate in scored[:top_k]:
            results.append(
                RankedResult(
                    grace_id=candidate.grace_id,
                    entity_type=candidate.entity_type,
                    name=candidate.name,
                    properties=candidate.properties,
                    rerank_score=float(score),
                    rrf_score=candidate.rrf_score,
                    contributing_strategies=candidate.contributing_strategies,
                )
            )

        return results
