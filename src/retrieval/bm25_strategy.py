"""Strategy 3: BM25 keyword search via bm25s.

PHASE_4_UPGRADE: Re-index when Extraction module produces entity descriptions.
"""

from __future__ import annotations

import bm25s
import structlog

from src.retrieval.retrieval_models import RetrievalCandidate

logger = structlog.get_logger()


class BM25SearchIndex:
    """BM25 keyword search over entity text representations using bm25s."""

    def __init__(self) -> None:
        self.retriever: bm25s.BM25 | None = None
        self.grace_ids: list[str] = []
        self.texts: list[str] = []

    def build_index(self, entities: list[tuple[str, str]]) -> None:
        """Tokenize texts, build BM25 index."""
        if not entities:
            self.retriever = None
            self.grace_ids = []
            self.texts = []
            return

        self.grace_ids = [gid for gid, _ in entities]
        self.texts = [text for _, text in entities]

        corpus_tokens = bm25s.tokenize(self.texts)
        self.retriever = bm25s.BM25()
        self.retriever.index(corpus_tokens)
        logger.info("bm25_index.built", entity_count=len(self.grace_ids))

    def search(self, query_text: str, top_k: int = 50) -> list[RetrievalCandidate]:
        """Tokenize query, retrieve top-K by BM25 score."""
        if self.retriever is None or not self.grace_ids:
            return []

        query_tokens = bm25s.tokenize([query_text])
        # Clamp top_k to corpus size
        effective_k = min(top_k, len(self.grace_ids))
        results, scores = self.retriever.retrieve(query_tokens, k=effective_k)

        candidates: list[RetrievalCandidate] = []
        # results and scores are 2D arrays — first row is our single query
        for rank, (idx, score) in enumerate(zip(results[0], scores[0])):
            idx = int(idx)
            if idx < 0 or idx >= len(self.grace_ids):
                continue
            score_val = float(score)
            if score_val <= 0:
                continue
            text = self.texts[idx]
            candidates.append(
                RetrievalCandidate(
                    grace_id=self.grace_ids[idx],
                    entity_type="Entity",
                    name=text.split(":")[0] if ":" in text else "",
                    score=score_val,
                    strategy="bm25",
                    rank=rank + 1,
                )
            )

        return candidates
