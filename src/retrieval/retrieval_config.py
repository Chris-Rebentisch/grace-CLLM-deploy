"""Retrieval pipeline configuration."""

from pydantic import BaseModel, Field


class RetrievalConfig(BaseModel):
    """Retrieval pipeline configuration."""

    # Strategy toggles
    graph_traversal_enabled: bool = True
    semantic_search_enabled: bool = True
    bm25_search_enabled: bool = True
    temporal_as_strategy: bool = Field(
        default=False,
        description="False=temporal filter on graph results, True=separate RRF strategy",
    )
    query_aware_filter_enabled: bool = Field(
        default=True,
        description="Enable rule-based query-aware property filtering before serialization.",
    )
    iterative_retrieval_enabled: bool = Field(
        default=False,
        description="Enable iterative second retrieval round for complex queries.",
    )
    iterative_auto_trigger_enabled: bool = Field(
        default=True,
        description="Allow heuristic auto-trigger for iterative retrieval when query appears complex.",
    )
    iterative_trigger_min_signals: int = Field(
        default=2,
        ge=1,
        le=3,
        description="Minimum heuristic signals required to auto-trigger iterative retrieval.",
    )
    iterative_min_tokens: int = Field(
        default=10,
        ge=1,
        description="Minimum query token count signal for iterative retrieval heuristic.",
    )
    iterative_round2_graph_hops: int = Field(
        default=2,
        ge=1,
        le=5,
        description="Traversal depth for round-2 graph search in iterative mode.",
    )
    iterative_round2_seed_limit: int = Field(
        default=10,
        ge=1,
        description="Maximum top round-1 results used as seeds for round-2 retrieval.",
    )

    # Graph traversal
    max_hop_depth: int = Field(
        default=3, ge=1, le=10, description="Max variable-length path depth"
    )
    graph_result_limit: int = Field(
        default=50, description="Max results from graph traversal"
    )

    # Semantic search
    embedding_model: str = "nomic-embed-text"
    embedding_dim: int = Field(
        default=768, description="Auto-detected from Ollama at startup"
    )
    semantic_result_limit: int = Field(
        default=50, description="Max results from semantic search"
    )

    # BM25
    bm25_result_limit: int = Field(default=50, description="Max results from BM25")

    # Temporal
    temporal_result_limit: int = Field(
        default=50, description="Max results when temporal is strategy"
    )

    # Chunk-semantic (D466/D467, Chunk 71)
    chunk_semantic_enabled: bool = Field(
        default=True,
        description="Enable chunk-semantic ANN strategy over Document_Chunk._embedding",
    )
    chunk_semantic_top_k: int = Field(
        default=20,
        description="Max results from chunk-semantic ANN search",
    )

    # F-0026 / ISS-0037 (validation run): Document_Chunk results are
    # TRIPLE-fused (semantic + bm25 + chunk_semantic all return them), so RRF
    # structurally over-weights chunks and an intent "why" query at top_k=8
    # returned 8/8 Document_Chunks (the intent plane started at rank ~14).
    # This cap bounds the chunk share of the FINAL returned top-k; the
    # pipeline backfills the freed slots with the next-ranked non-chunk
    # results (see RetrievalPipeline._select_final_results).
    chunk_share_max: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description=(
            "Maximum share of the final top-k that Document_Chunk-typed "
            "results may occupy (at most ceil(top_k * chunk_share_max) "
            "chunks); freed slots are backfilled with next-ranked non-chunk "
            "results. 1.0 disables the cap."
        ),
    )

    # RRF
    rrf_k: int = Field(default=60, description="RRF damping constant")

    # Reranker
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    reranker_top_k: int = Field(
        default=10, description="Final top-K after reranking"
    )
    reranker_candidates: int = Field(
        default=20, description="Candidates to send to cross-encoder"
    )

    # Serialization
    serialization_format: str = Field(
        default="template", description="template | turtle | llm"
    )
    token_budget: int = Field(
        default=2000, description="Max tokens for serialized context"
    )
    serialization_model: str = Field(
        default="qwen2.5:7b",
        description="Model for LLM serialization when serialization_format is llm",
    )

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
