"""Pydantic models for the retrieval pipeline."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class RetrievalQuery(BaseModel):
    """Input query for the retrieval pipeline."""

    query_text: str = Field(description="Natural language query")
    seed_entity_ids: list[str] = Field(
        default_factory=list, description="grace_ids to start traversal from"
    )
    temporal_start: datetime | None = Field(
        default=None, description="Filter: valid_from >= this"
    )
    temporal_end: datetime | None = Field(
        default=None, description="Filter: valid_to <= this (or NULL)"
    )
    entity_types: list[str] = Field(
        default_factory=list, description="Restrict to these types"
    )
    top_k: int = Field(default=10, description="Final number of results")
    iterative_mode: str | None = Field(
        default=None,
        description="Optional override: auto|on|off for iterative round-2 retrieval.",
    )


class RetrievalCandidate(BaseModel):
    """A single candidate from one retrieval strategy."""

    grace_id: str
    entity_type: str
    name: str
    properties: dict[str, Any] = Field(default_factory=dict)
    score: float = Field(default=0.0, description="Strategy-specific score")
    strategy: str = Field(
        description="Which strategy found this: graph|semantic|bm25|temporal"
    )
    rank: int = Field(default=0, description="Rank within strategy results")
    # Graph-specific
    hop_distance: int | None = Field(
        default=None, description="Hops from seed entity"
    )
    path: list[str] | None = Field(
        default=None, description="grace_ids in traversal path"
    )


class FusedCandidate(BaseModel):
    """Candidate after RRF fusion."""

    grace_id: str
    entity_type: str
    name: str
    properties: dict[str, Any] = Field(default_factory=dict)
    rrf_score: float = Field(description="Combined RRF score")
    contributing_strategies: list[str] = Field(
        description="Which strategies found this"
    )
    strategy_ranks: dict[str, int] = Field(description="Rank per strategy")


class RankedResult(BaseModel):
    """Final result after cross-encoder reranking."""

    grace_id: str
    entity_type: str
    name: str
    properties: dict[str, Any] = Field(default_factory=dict)
    rerank_score: float = Field(description="Cross-encoder relevance score")
    rrf_score: float
    contributing_strategies: list[str]
    hop_distance: int | None = None


class RetrievalResponse(BaseModel):
    """Complete response from the retrieval pipeline."""

    query: str
    results: list[RankedResult]
    serialized_context: str = Field(
        description="Serialized subgraph for LLM prompt"
    )
    serialization_format: str = Field(description="template|turtle|llm")
    total_candidates: int = Field(description="Total before reranking")
    strategy_contributions: dict[str, int] = Field(
        description="Count per strategy in final results"
    )
    latency_ms: dict[str, float] = Field(description="Per-component timing")
    retrieval_mode: str = Field(
        default="single_round",
        description="single_round or iterative_round2 depending on retrieval execution path.",
    )
    query_intents: list[str] = Field(
        default_factory=list,
        description="Rule-based intent tags used by query-aware property filtering.",
    )
    properties_omitted_count: int = Field(
        default=0,
        description="Count of candidate properties omitted by query-aware filtering.",
    )
    multi_hop_proxy_score: float = Field(
        default=0.0,
        description="Proxy score for multi-hop richness based on path lengths and cross-strategy support.",
    )
    latency_p95_by_mode_ms: dict[str, float] = Field(
        default_factory=dict,
        description="Rolling p95 total latency by retrieval mode from in-process history.",
    )
    query_event_id: UUID | None = Field(
        default=None,
        description="Opaque query audit trail identifier",
    )
