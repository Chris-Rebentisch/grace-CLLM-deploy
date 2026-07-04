"""Pydantic models for the Regeneration Module (Chunk 23).

phase_state is typed via Literal in both request and response.
claim-span level certainty annotations implement Elicitation Protocol
§6.3 and §12.2.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.retrieval.retrieval_models import RetrievalQuery

PhaseState = Literal[
    "prepare", "open", "structure", "clarify", "close", "none"
]

SpanDetectorMode = Literal["sentence_fallback", "llm_judged", "hybrid"]

CertaintyBand = Literal[
    "high", "medium", "low", "insufficient_evidence"
]

SpanConfidence = Literal["high", "medium", "low"]


class RegenOverrides(BaseModel):
    """Optional per-request overrides for regeneration dispatch."""

    regeneration_model: str | None = None
    temperature: float | None = None
    response_max_tokens: int | None = None


class RegenerationQuery(BaseModel):
    """Input to the regeneration pipeline."""

    query_text: str = Field(description="Natural language query")
    retrieval_query: RetrievalQuery | None = Field(
        default=None,
        description=(
            "Optional pre-built RetrievalQuery. Typed for contract safety "
            "(no silent dict drift). If None, constructed from query_text "
            "with top_k=10 and no seed entities."
        ),
    )
    phase_state: PhaseState = Field(
        default="none",
        description="Active elicitation phase. Shapes response style.",
    )
    overrides: RegenOverrides | None = None


class AssembledPrompt(BaseModel):
    """Deterministic output of PromptAssembler."""

    system_prompt: str
    context: str
    user_query: str
    system_token_estimate: int
    context_token_estimate: int
    query_token_estimate: int
    total_token_estimate: int
    context_truncated: bool = False
    truncation_details: str | None = None
    phase_style_applied: str = Field(
        description="Which phase directive was used"
    )


class ClaimSpan(BaseModel):
    """One factual claim in the response, annotated with certainty (D130)."""

    text: str
    sentence_indices: list[int]
    start_char: int | None = Field(
        default=None, description="Char offset in response_text (future UI)"
    )
    end_char: int | None = Field(
        default=None, description="Char offset in response_text (future UI)"
    )
    certainty_band: CertaintyBand
    span_confidence: SpanConfidence = Field(
        default="low",
        description="Detector's confidence in the span boundary",
    )
    supporting_grace_ids: list[str] = Field(default_factory=list)


class ResponseMetadata(BaseModel):
    """UI/debug hints. Non-authoritative; purely informational."""

    model_config = ConfigDict(protected_namespaces=())

    context_truncated: bool = False
    span_detector_mode: SpanDetectorMode = "sentence_fallback"
    phase_style_applied: str
    span_detection_note: str | None = Field(
        default=None,
        description=(
            "E.g. 'no_substantive_claims_detected' or "
            "'span_detection_degraded'"
        ),
    )
    model_override_applied: bool = Field(
        default=False,
        description=(
            "Whether overrides.regeneration_model was actually dispatched "
            "to the provider. In v1 this is always False (D137); the "
            "field exists so callers are not misled by accepted-but-ignored "
            "override values."
        ),
    )


class RegenerationResponse(BaseModel):
    """Final response from the regeneration pipeline."""

    model_config = ConfigDict(protected_namespaces=())

    query: str
    response_text: str
    claim_spans: list[ClaimSpan] = Field(default_factory=list)
    phase_state: PhaseState
    contributing_grace_ids: list[str] = Field(
        default_factory=list,
        description="Flattened deduplicated list across all spans",
    )
    strategy_contributions: dict[str, int] = Field(default_factory=dict)
    latency_ms: dict[str, float] = Field(
        default_factory=dict,
        description="Keys: retrieve, assemble, synthesize, span_detect, total",
    )
    token_usage: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Keys: input_tokens, output_tokens, system_estimate, "
            "context_estimate"
        ),
    )
    model: str = ""
    provider: str = ""
    retrieval_mode: str = "single_round"
    response_metadata: ResponseMetadata


class RegenerationError(BaseModel):
    """API error payload shape only (not a control-flow object)."""

    stage: Literal["retrieve", "assemble", "synthesize", "span_detect"]
    error_type: str
    error_message: str
    partial_response: str | None = None
    request_id: str | None = Field(
        default=None,
        description=(
            "UUID4 generated at request entry; echoed on success and error "
            "for troubleshooting and log correlation."
        ),
    )
    stage_latencies_ms: dict[str, float] = Field(
        default_factory=dict,
        description=(
            "Partial per-stage timings captured before the failure. "
            "Populated from the raised stage exception's attached context."
        ),
    )


class RegenerationConfigResponse(BaseModel):
    """Explicit non-secret config shape for GET /config.

    Reflecting RegenSettings directly risks leaking future sensitive
    fields. This model is an explicit allowlist.
    """

    system_budget_tokens: int
    context_budget_tokens: int
    query_budget_tokens: int
    response_budget_tokens: int
    total_input_budget_tokens: int
    regeneration_model: str
    regeneration_temperature: float
    chars_per_token: int
    enable_claim_span_detection: bool
    span_detector_mode: SpanDetectorMode
    phase_style_overrides_applied: list[str] = Field(
        description="Which phase keys have custom overrides configured"
    )
