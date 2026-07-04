"""Collapse raw CQ candidates into a ranked, canonical set (relevance selection).

CQ generation produces many overlapping questions, and the same themes recur across
every document — so raw counts scale with the corpus and overwhelm human review
(e.g. 44 CQs from 6 docs). This module collapses near-duplicates via embedding
similarity and surfaces a manageable, coverage-ranked canonical set, so a reviewer
sees ~dozens of distinct competency questions instead of thousands. The distinct set
*converges* as documents grow rather than scaling linearly.

Method: nomic-embed-text (shared embeddings) + agglomerative cosine clustering at a
tuned similarity threshold. This complements `cq_merge` Tier-1 HDBSCAN: HDBSCAN is
density-based and leaves most short CQ sets as noise (44 -> 5 clusters + 20 noise),
whereas threshold agglomerative gives coherent, noise-free thematic groups
(44 -> ~9 clusters covering all). Tuned default similarity_threshold=0.65.

Canonical selection prefers the most *general/reusable* question in each cluster
(schema-shaping type, then fewest proper-noun-like tokens), since those define entity
types and relationships that apply across the whole corpus; per-instance VALIDATING
facts are extraction targets, not review-worthy CQs.
"""
from __future__ import annotations

import re

import numpy as np
import structlog
from sklearn.cluster import AgglomerativeClustering

from src.discovery.merge_embeddings import embed_texts
from src.discovery.models import load_discovery_config

logger = structlog.get_logger()

# Most general (schema-shaping) types first; VALIDATING (per-instance facts) last.
_TYPE_PRIORITY = {
    "FOUNDATIONAL": 0,
    "SCOPING": 1,
    "RELATIONSHIP": 2,
    "METAPROPERTY": 3,
    "UNCLASSIFIED": 4,
    "VALIDATING": 5,
}
_SCHEMA_TYPES = {"FOUNDATIONAL", "SCOPING", "RELATIONSHIP", "METAPROPERTY"}
_PROPER = re.compile(r"\b[A-Z][A-Za-z0-9.&'\-]{2,}")


def _relevance_config() -> dict:
    return load_discovery_config().get("cq_merge", {})


def _specificity(question: str) -> int:
    """Proxy for how document-specific a question is: count of proper-noun-like tokens.

    Lower = more general/reusable (fewer named parties/sections), which makes a better
    canonical representative for a cross-document theme.
    """
    return len(_PROPER.findall(question))


def cluster_by_similarity(
    embeddings: list[list[float]], similarity_threshold: float
) -> list[int]:
    """Agglomerative cosine (average-linkage) clustering. Every item gets a label
    (no noise). Items grouped when cosine similarity >= similarity_threshold."""
    n = len(embeddings)
    if n == 0:
        return []
    if n == 1:
        return [0]
    distance_threshold = 1.0 - similarity_threshold
    model = AgglomerativeClustering(
        metric="cosine",
        linkage="average",
        distance_threshold=distance_threshold,
        n_clusters=None,
    )
    return [int(x) for x in model.fit(np.array(embeddings)).labels_]


def select_canonical(members: list[dict]) -> dict:
    """Pick the most general/reusable CQ in a cluster as its canonical representative.

    Specificity (count of proper-noun-like tokens) is the primary signal because
    model-assigned cq_type labels are noisy (generators tend to over-label
    FOUNDATIONAL); a question with fewer named parties/sections generalizes better
    across the corpus. Type and brevity break ties.
    """
    return min(
        members,
        key=lambda m: (
            _specificity(m.get("question", "")),
            _TYPE_PRIORITY.get((m.get("cq_type") or "UNCLASSIFIED").upper(), 4),
            len(m.get("question", "")),
        ),
    )


async def collapse_and_rank(
    cqs: list[dict],
    embeddings: list[list[float]] | None = None,
    similarity_threshold: float | None = None,
    schema_only: bool = False,
) -> list[dict]:
    """Collapse near-duplicate CQs into canonical themes, ranked by coverage.

    Args:
        cqs: dicts with at least ``question`` (and optionally ``cq_type``).
        embeddings: precomputed embeddings parallel to ``cqs``; embedded if omitted.
        similarity_threshold: cosine cutoff for merging (default from config / 0.65).
        schema_only: drop per-instance VALIDATING facts (route them to extraction).

    Returns:
        Canonical CQs sorted by ``coverage`` (how many raw CQs collapsed into each),
        each with ``question``, ``cq_type``, ``coverage``, and ``variants``.
    """
    if not cqs:
        return []
    cfg = _relevance_config()
    if similarity_threshold is None:
        similarity_threshold = cfg.get("relevance_similarity_threshold", 0.65)

    pool = cqs
    if schema_only:
        pool = [c for c in cqs if (c.get("cq_type") or "").upper() in _SCHEMA_TYPES] or cqs

    if embeddings is None:
        embeddings = await embed_texts([c["question"] for c in pool])
    elif schema_only and len(embeddings) == len(cqs):
        keep = [(c.get("cq_type") or "").upper() in _SCHEMA_TYPES for c in cqs]
        if any(keep):
            embeddings = [e for e, k in zip(embeddings, keep) if k]

    labels = cluster_by_similarity(embeddings, similarity_threshold)
    groups: dict[int, list[dict]] = {}
    for cq, label in zip(pool, labels):
        groups.setdefault(label, []).append(cq)

    ranked = sorted(groups.values(), key=len, reverse=True)
    out = []
    for g in ranked:
        c = select_canonical(g)
        out.append({
            "question": c["question"],
            "cq_type": c.get("cq_type"),
            "coverage": len(g),
            "variants": [m["question"] for m in g if m is not c],
        })
    logger.info(
        "cq_collapse_complete",
        raw=len(cqs),
        canonical=len(out),
        threshold=similarity_threshold,
        schema_only=schema_only,
    )
    return out
