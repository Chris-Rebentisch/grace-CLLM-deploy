"""Extraction module configuration loaded from .env and config/discovery.yaml."""

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ExtractionSettings(BaseSettings):
    """Extraction-specific configuration.

    Provider/model fields default to None, meaning fall back to the
    global config in config/discovery.yaml (read by llm_provider.py).
    Set these to override the global provider for extraction only.
    """

    # Provider overrides (None = use global config from discovery.yaml)
    extraction_provider: str | None = Field(
        default=None,
        description="Provider for extraction calls (ollama, anthropic, openai). None = use global.",
    )
    # F-0015(b) / ISS-0031: with env_prefix="EXTRACTION_" this field was only
    # reachable via the double-prefixed EXTRACTION_EXTRACTION_MODEL env var —
    # the natural EXTRACTION_MODEL spelling was silently dead. AliasChoices
    # makes both env spellings work (friendly alias first); the old name keeps
    # working, and populate_by_name=True (model_config) keeps constructor
    # kwargs (`ExtractionSettings(extraction_model=...)`, used by
    # eval_checkpoint.py) working.
    extraction_model: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "EXTRACTION_MODEL", "EXTRACTION_EXTRACTION_MODEL", "extraction_model"
        ),
        description="Model for extraction. None = use global model from discovery.yaml. "
                    "Env: EXTRACTION_MODEL (alias) or EXTRACTION_EXTRACTION_MODEL (legacy).",
    )
    verification_provider: str | None = Field(
        default=None,
        description="Provider for verification calls. Can differ from extraction_provider.",
    )
    verification_model: str | None = Field(
        default=None,
        description="Model for verification. Can differ from extraction_model.",
    )

    # Provider base URL overrides
    extraction_base_url: str | None = Field(
        default=None,
        description="Base URL for extraction provider. Required when extraction_provider is overridden to openai.",
    )
    verification_base_url: str | None = Field(
        default=None,
        description="Base URL for verification provider. Required when verification_provider is overridden to openai.",
    )

    # Instructor settings
    max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description=(
            "Instructor retry limit on Pydantic validation failure. "
            "Bounded 0-10: each retry re-sends the full prompt to the LLM, "
            "so an unbounded value risks runaway cost/latency."
        ),
    )
    extraction_timeout: float = Field(
        default=120.0, description="Total timeout in seconds across all extraction retries"
    )
    verification_timeout: float = Field(
        default=60.0, description="Total timeout in seconds across all verification retries"
    )

    # Chunking (schema-only in Chunk 16; behavior in Chunk 17)
    chunk_token_cap: int = Field(
        default=3000, description="Max tokens per extraction chunk"
    )
    chunk_overlap_tokens: int = Field(
        default=200, description="Overlap tokens between adjacent chunks"
    )

    # Concurrency (schema-only in Chunk 16; used by pipeline in Chunk 18)
    concurrency_limit: int = Field(
        default=3, description="Max concurrent chunk extractions via asyncio.Semaphore"
    )

    # Inference
    # F-0015(a) / ISS-0031: newer Anthropic models (e.g. claude-sonnet-5)
    # reject the temperature parameter with a 400 ("temperature is deprecated
    # for this model"). Optional so operators can set None to OMIT temperature
    # from the request entirely (instructor_client.py skips the kwarg when
    # None). Default stays 0.0 for max schema adherence on models that accept it.
    temperature: float | None = Field(
        default=0.0,
        description="LLM temperature. 0.0 for max schema adherence. "
                    "None = omit the parameter (required by newer Claude models).",
    )
    # F-0015(c) / ISS-0031: the fixed 4096 default was sized for qwen2.5:7b's
    # 8K context and silently capped Claude on dense chunks. None = provider-
    # aware default via effective_max_output_tokens(): ollama keeps 4096,
    # anthropic/openai-compatible get 8192. An explicit value always wins.
    max_output_tokens: int | None = Field(
        default=None,
        description="Max output tokens per call. None = provider-aware default "
                    "(ollama: 4096 to stay under qwen2.5:7b 8K limit; "
                    "anthropic/openai: 8192). Set explicitly to override.",
    )

    # Confidence thresholds (schema-only in Chunk 16; used by scorer in Chunk 19)
    confidence_threshold_supported: float = Field(
        default=0.8, description="Confidence floor for SUPPORTED verdict"
    )
    confidence_threshold_insufficient: float = Field(
        default=0.5, description="Ceiling for INSUFFICIENT verdict confidence scoring"
    )
    confidence_threshold_refuted: float = Field(
        default=0.05, description="Confidence ceiling for REFUTED verdict"
    )

    # Verification-specific inference config (D83)
    # F-0015(a) / ISS-0031: Optional for the same reason as `temperature` —
    # None omits the parameter for models that 400 on it.
    verification_temperature: float | None = Field(
        default=0.0,
        description="LLM temperature for verification calls. Separate from "
                    "extraction temperature for independent tuning. "
                    "None = omit the parameter.",
    )
    verification_max_output_tokens: int = Field(
        default=2048,
        description="Max output tokens for verification calls. Shorter than "
                    "extraction (4096) because verification responses are smaller.",
    )

    # Verification failure scoring (D72b)
    verification_failure_confidence: float = Field(
        default=0.3,
        description="Confidence score assigned when verification call fails "
                    "(ExtractionLLMError). Distinct from model-judged INSUFFICIENT "
                    "to separate infrastructure failures from model uncertainty.",
    )

    # Entity resolution defaults (schema-only; per-type overrides in Chunk 20)
    er_default_merge: float = Field(
        default=0.85, description="Embedding similarity threshold for auto-merge"
    )
    er_default_review: float = Field(
        default=0.70, description="Embedding similarity threshold for LLM disambiguation"
    )
    er_candidate_limit: int = Field(
        default=10, description="Max candidates for embedding comparison per entity"
    )

    # Entity resolution per-type thresholds (D85, D89, D95)
    # F-0019 (validation run, 2026-07-03): Legal_Entity merge was 0.90 and
    # nomic scored "Meridian Land Development LLC" vs "Meridian Holdings LLC"
    # at 0.9096 — two DISTINCT sibling LLCs auto-merged 0.0096 above threshold,
    # producing a parent_of self-loop and erasing a core entity from the graph.
    # Related family/sibling company names (shared surname + form suffix) land in
    # the 0.90–0.93 band on nomic-embed-text; raise merge to 0.93 so that band
    # routes to Tier-3 LLM adjudication (which correctly separated the 0.70
    # ZC-case pair) instead of silently merging.
    er_thresholds: dict[str, dict[str, float]] = Field(
        default_factory=lambda: {
            "Legal_Entity": {"merge": 0.93, "review": 0.78},
            "Person": {"merge": 0.93, "review": 0.82},
        },
        description="Per-type merge/review thresholds. Fallback to er_default_merge/review.",
    )
    er_embedding_model: str = Field(
        default="nomic-embed-text",
        description="Ollama embedding model for Tier 2 similarity",
    )
    er_ann_top_k: int = Field(
        default=20,
        description="Top-K for vectorNeighbors() ANN query",
    )

    # Entity resolution LLM config (Tier 3)
    er_model: str | None = Field(
        default=None,
        description="Model for ER Tier 3 disambiguation. None = use extraction model.",
    )
    er_provider: str | None = Field(
        default=None,
        description="Provider for ER Tier 3. None = use extraction provider.",
    )
    er_base_url: str | None = Field(
        default=None,
        description="Base URL for ER Tier 3 when provider is openai or ollama. "
                    "None = same precedence as extraction (extraction_base_url then YAML).",
    )
    # F-0015(a) / ISS-0031: Optional for the same reason as `temperature`.
    er_temperature: float | None = Field(
        default=0.0,
        description="Temperature for Tier 3 LLM disambiguation. "
                    "None = omit the parameter.",
    )
    er_timeout: float = Field(
        default=30.0,
        description="Timeout for Tier 3 LLM call (shorter — small prompt)",
    )
    er_max_output_tokens: int = Field(
        default=512,
        description="Max output tokens for Tier 3 (YES/NO + reasoning is short)",
    )

    def effective_max_output_tokens(self, provider: str) -> int:
        """Provider-aware output-token ceiling (F-0015(c) / ISS-0031).

        An explicitly configured max_output_tokens always wins. Otherwise:
        ollama keeps the legacy 4096 (sized for qwen2.5:7b's 8K context);
        anthropic / openai-compatible cloud models get 8192 so dense chunks
        are not truncated mid-extraction.

        Args:
            provider: Resolved provider name (ollama, anthropic, openai).

        Returns:
            Max output tokens to send on the request.
        """
        if self.max_output_tokens is not None:
            return self.max_output_tokens
        return 4096 if provider == "ollama" else 8192

    model_config = SettingsConfigDict(
        env_prefix="EXTRACTION_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # F-0015(b) / ISS-0031: required so fields carrying validation_alias
        # (extraction_model) remain constructible by field name.
        populate_by_name=True,
    )
