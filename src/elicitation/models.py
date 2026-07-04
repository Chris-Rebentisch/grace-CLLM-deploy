"""Pydantic models for elicitation telemetry (Chunk 27, protocol §8.2).

Envelope is the outer shape; per-event-type payload models are validated
against `event_type`. Unknown event types are rejected with a 422
`telemetry_validation_error`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

PhaseName = Literal[
    "prepare", "open", "structure", "clarify", "close", "none"
]
ActorType = Literal["human", "system", "agent"]

EventType = Literal[
    "session_started",
    "phase_entered",
    "phase_exited",
    "session_paused",
    "session_resumed",
    "session_closed",
    "close_returned_to_chat",
    "protocol_violation_detected",
    "graph_viewer_opened",
    "graph_node_inspected",
    "graph_edge_inspected",
    "retrieval_inspector_opened",
    "retrieval_query_replayed",
    "structure_phase_entered",
    "clarify_phase_entered",
    "laddering_step_completed",
    "card_sort_completed",
    "teach_back_completed",
    "scope_segment_changed",
    "cq_authored",
    "cq_candidate_accepted",
    "cq_candidate_rejected",
    # D234 — Chunk 30 catalog extension (claim review + LLM config + sources).
    "claim_disposition_accepted",
    "claim_disposition_rejected",
    "llm_provider_switched",
    "sources_configured",
    "airgap_mode_toggled",
    # D282 — Chunk 36 catalog extension (Reconciliation Layer foundation).
    "gap_report_generated",
    "gap_report_viewed",
    # D290 — Chunk 37 catalog extension (cross-executive Reconciliation).
    "divergence_map_generated",
    "divergence_map_viewed",
    "documented_reality_report_generated",
    "documented_reality_report_viewed",
    # D298 — Chunk 38 catalog extension (Change_Directives foundation).
    "change_directive_created",
    "change_directive_transitioned",
    "change_directive_flagged_from_review",
    "change_directive_evidence_criterion_added",
    # D307/D308 — Chunk 39 realization telemetry.
    "change_directive_metadata_edited",
    "change_directive_detail_viewed",
    # D318 — Chunk 40 decomposition pipeline lifecycle.
    "decomposition_run_started",
    "decomposition_run_completed",
    "decomposition_run_failed",
    # D330 — Chunk 41 decomposition Layer 5/6/7 + re-run telemetry.
    "decomposition_layer5_decision_recorded",
    "decomposition_layer6_validation_recorded",
    "segmentation_map_ratified",
    "decomposition_rerun_triggered",
    # D331/D333/D337 — Chunk 42 Permission Matrix telemetry.
    # CF1 lockstep mirrored in frontend/lib/api/types.ts,
    # frontend/lib/telemetry/bridge.ts, frontend/lib/telemetry/events.ts.
    "permission_matrix_hypothesis_generated",
    "permission_matrix_ratified",
    "permission_cluster_decision_recorded",
    "permission_matrix_auto_assigned",
    # D342/D343/D348 — Chunk 43 Sensitivity Gate telemetry.
    # CF1 lockstep mirrored in frontend/lib/api/types.ts,
    # frontend/lib/telemetry/bridge.ts, frontend/lib/telemetry/events.ts.
    "sensitivity_report_generated",
    "sensitivity_report_viewed",
    "sensitivity_audit_trail_viewed",
    # D364/D365/D366/D367 — Chunk 44 MCP write-tool telemetry (CF1 lockstep).
    "mcp_session_started",
    "mcp_session_phase_advanced",
    "mcp_session_closed",
    "mcp_review_decided",
    "mcp_laddering_followup_emitted",
    "mcp_teachback_captured",
    "mcp_deep_link_generated",
    # D375 — Chunk 45 Remote Support Session telemetry (CF1 lockstep).
    "support_session_granted",
    "support_session_revoked",
    "support_banner_viewed",
    # D387/D389 — Chunk 47 Signal→Proposal pipeline telemetry (CF1 lockstep).
    "proposal_generated",
    "proposal_decided",
    "proposal_viewed",
    # D392/D393 — Chunk 48 KGCL Change Executor telemetry (CF1 lockstep).
    "proposal_executed",
    # D394–D397 — Chunk 49 Earned Autonomy Calibration telemetry (CF1 lockstep).
    "calibration_decision_recorded",
    "calibration_dashboard_viewed",
    # D398–D401 — Chunk 50 Agent Daemon telemetry (CF1 lockstep).
    "agent_tick_started",
    "agent_tick_completed",
    "autonomous_proposal_applied",
    "cooling_period_finalized",
    "kill_switch_engaged",
    "kill_switch_disengaged",
    # D402–D405 — Chunk 51 Federation Infrastructure telemetry (CF1 lockstep).
    "federation_namespace_registered",
    "federation_entity_resolved",
    # Chunk 60 — Phase 7 Communication Ingestion frontend surfaces (CF1 lockstep).
    "ingestion_dashboard_viewed",
    "ingestion_source_detail_viewed",
    "profile_browser_viewed",
    "profile_detail_viewed",
    "curation_submitted",
    "ingestion_settings_changed",
    "recon_source_filter_applied",
]


class SessionStartedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: str | None = None
    instrument_selected: str | None = None
    rationale_string: str | None = None


class PhaseEnteredPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entered_phase: PhaseName
    entered_at: datetime


class PhaseExitedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exited_phase: PhaseName
    exited_at: datetime
    phase_duration_ms: int = Field(ge=0)
    phase_signals_json: dict[str, Any] = Field(default_factory=dict)


class SessionPausedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paused_from_phase: PhaseName
    paused_at: datetime


class SessionResumedPayload(BaseModel):
    """EC-5 audit: MUST NOT contain cooldown/penalty/decay fields.

    `extra="forbid"` guarantees any such field is rejected by Pydantic
    as an unknown-field validation error rather than silently accepted.
    """

    model_config = ConfigDict(extra="forbid")
    resumed_to_phase: PhaseName
    resumed_at: datetime
    paused_duration_ms: int = Field(ge=0)


class SessionClosedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary_edited: bool
    summary_rejected: bool
    session_duration_ms: int = Field(ge=0)
    phase_duration_distribution: dict[str, int] = Field(default_factory=dict)


class CloseReturnedToChatPayload(BaseModel):
    """D201 canonical event — not aliased to `session_paused`."""

    model_config = ConfigDict(extra="forbid")
    prior_phase: Literal["close"]
    resumed_phase: Literal["open"]
    summary_discarded: bool
    session_duration_ms: int = Field(ge=0)


class ProtocolViolationDetectedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    violation_type: str
    details: dict[str, Any] = Field(default_factory=dict)


class GraphViewerOpenedPayload(BaseModel):
    """D215 — fires on GraphViewer mount."""

    model_config = ConfigDict(extra="forbid")
    scope: str
    entity_count_estimated: int | None = None


class GraphNodeInspectedPayload(BaseModel):
    """D215 — fires on NodeDetailPanel open. `grace_id_hash` is hex SHA-256 of the entity grace_id."""

    model_config = ConfigDict(extra="forbid")
    entity_type: str
    grace_id_hash: str


class GraphEdgeInspectedPayload(BaseModel):
    """D215 — fires on EdgeDetailPanel open. `grace_id_hash` is hex SHA-256 of the relationship (edge) grace_id — not source/target entity ids."""

    model_config = ConfigDict(extra="forbid")
    relationship_type: str
    grace_id_hash: str


class RetrievalInspectorOpenedPayload(BaseModel):
    """D215 — fires on RetrievalInspector mount."""

    model_config = ConfigDict(extra="forbid")
    source: Literal["chat_link", "direct_nav", "replay_button"]


class RetrievalQueryReplayedPayload(BaseModel):
    """D215 — fires on successful replay mutation only (spec §18 #7).

    D267 (Chunk 35b) extends the payload with two backward-compatible
    fields used by the inspector replay-diff flow:

    * ``replay_differed`` — whether the replay produced a different result
      set vs. the original query.
    * ``original_query_event_id`` — UUID of the original ``Query_Event``
      (server-generated, threaded from client browser-session state).
    """

    model_config = ConfigDict(extra="forbid")
    strategies_fired: list[str]
    latency_ms_total: float
    # D267 — Chunk 35b additions (defaults preserve backward compat).
    replay_differed: bool = False
    original_query_event_id: str | None = None


# ---------- Chunk 29 D228 payloads (Structure / Clarify phases) ----------


class StructureDecisionPayload(BaseModel):
    """D228 — Structure-phase decision with Reconciliation-Layer-aware fields."""

    model_config = ConfigDict(extra="forbid")
    evidence_items_viewed: list[str]
    evidence_items_available: list[str]
    declared_certainty_band: Literal["high", "medium", "low", "insufficient_evidence"]


class ClarifyDecisionPayload(BaseModel):
    """D228 — Clarify-phase decision tracking position changes."""

    model_config = ConfigDict(extra="forbid")
    decision_id_hash: str
    position_changed: bool
    prior_decision_id: str | None = None
    clarify_duration_ms: int


class StructurePhaseEnteredPayload(BaseModel):
    """D228 — fires on Structure phase entry."""

    model_config = ConfigDict(extra="forbid")
    entered_phase: Literal["structure"]
    entered_at: datetime
    mode: str
    mode_rationale: str


class ClarifyPhaseEnteredPayload(BaseModel):
    """D228 — fires on Clarify phase entry."""

    model_config = ConfigDict(extra="forbid")
    entered_phase: Literal["clarify"]
    entered_at: datetime
    unresolved_decision_count: int


class LadderingStepCompletedPayload(BaseModel):
    """D228 — fires on laddering step completion."""

    model_config = ConfigDict(extra="forbid")
    step_index: int
    parent_grace_id_hash: str
    child_grace_id_hashes: list[str]
    step_duration_ms: int


class CardSortCompletedPayload(BaseModel):
    """D228 — fires on card sort completion."""

    model_config = ConfigDict(extra="forbid")
    card_count: int
    category_count: int
    recategorization_count: int
    duration_ms: int


class TeachBackCompletedPayload(BaseModel):
    """D228 — fires on teach-back completion."""

    model_config = ConfigDict(extra="forbid")
    item_index: int
    sentence_count: int
    correct_count: int
    wrong_count: int
    missing_something_count: int
    correction_chars_total: int


class ScopeSegmentChangedPayload(BaseModel):
    """D228 — fires on scope segment change."""

    model_config = ConfigDict(extra="forbid")
    prior_scope: str
    new_scope: str
    segment_count: int


class CQAuthoredPayload(BaseModel):
    """D228 — fires when a CQ is authored."""

    model_config = ConfigDict(extra="forbid")
    cq_id_hash: str
    cq_type: str
    domain: str
    authoring_source: Literal["from_scratch", "from_candidate"]


class CQCandidateAcceptedPayload(BaseModel):
    """D228 — fires when a CQ candidate is accepted."""

    model_config = ConfigDict(extra="forbid")
    candidate_id_hash: str
    source_origin: Literal["local_documents", "web_presence", "ontology_seed"]
    edited_before_accept: bool


class CQCandidateRejectedPayload(BaseModel):
    """D228 — fires when a CQ candidate is rejected."""

    model_config = ConfigDict(extra="forbid")
    candidate_id_hash: str
    source_origin: Literal["local_documents", "web_presence", "ontology_seed"]
    reject_reason_category: str


# ---------- Chunk 30 D234 payloads (claim review + LLM config + sources) ----------


class ClaimDispositionAcceptedPayload(BaseModel):
    """D234 — fires when a quarantined claim is accepted (with or without modification)."""

    model_config = ConfigDict(extra="forbid")
    claim_id_hash: str
    reviewer_hash: str
    was_modified: bool
    ontology_module: str


class ClaimDispositionRejectedPayload(BaseModel):
    """D234 — fires when a quarantined claim is rejected."""

    model_config = ConfigDict(extra="forbid")
    claim_id_hash: str
    reviewer_hash: str
    ontology_module: str


class LLMProviderSwitchedPayload(BaseModel):
    """D234 — fires when the active LLM provider is changed in Settings."""

    model_config = ConfigDict(extra="forbid")
    from_provider_id: str
    to_provider_id: str
    airgap_mode_after: bool


class SourcesConfiguredPayload(BaseModel):
    """D234 — fires after the user confirms a source set in /sources."""

    model_config = ConfigDict(extra="forbid")
    file_count: int = Field(ge=0)
    total_size_mb: float = Field(ge=0)
    estimated_processing_minutes: float = Field(ge=0)


class AirgapModeToggledPayload(BaseModel):
    """D234 — fires whenever airgap_mode is toggled in Settings."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool


# ---------- Chunk 36 D282 payloads (Reconciliation Layer catalog extension) ----------


class GapReportGeneratedPayload(BaseModel):
    """D282 — fires server-side after a Gap Report is persisted.

    The raw ``evidence_grounding_score`` float is permitted in the telemetry
    payload because telemetry is internal append-only data, not user-facing
    DOM rendering (Q7.1). D120/D217 numeric-score discipline applies to
    rendered HTML only.
    """

    model_config = ConfigDict(extra="forbid")
    reviewer_hash: str
    evidence_grounding_score: float | None = None
    evidence_grounding_threshold: int
    generated_at: datetime


class GapReportViewedPayload(BaseModel):
    """D282 — reserved catalog entry for Chunk 37's frontend emission.

    Chunk 36 does not emit this event from the API; the catalog entry
    lets Chunk 37 ship the UI without a lockstep PR at UI-ship time.
    """

    model_config = ConfigDict(extra="forbid")
    reviewer_hash: str
    viewed_at: datetime
    sections_expanded: list[
        Literal[
            "emphasized_with_evidence",
            "emphasized_without_evidence",
            "unemphasized_in_evidence",
        ]
    ]


# ---------- Chunk 37 D290 payloads (Reconciliation cross-executive) ----------


class DivergenceMapGeneratedPayload(BaseModel):
    """D290 — fires server-side after a Cross-Executive Divergence Map
    is persisted. Aggregate per-bucket counts only; no per-element
    leakage. Reviewer identifiers are SHA-256 hashed (D215 precedent)."""

    model_config = ConfigDict(extra="forbid")
    reviewer_a_hash: str
    reviewer_b_hash: str
    segment_id: str | None = None
    additive_a_count: int
    additive_b_count: int
    contradictory_count: int
    consensus_count: int
    generated_at: datetime


class DivergenceMapViewedPayload(BaseModel):
    """D290 — fires from the frontend ``DivergenceMap`` mount."""

    model_config = ConfigDict(extra="forbid")
    reviewer_hash: str
    divergence_map_id: str
    viewed_at: datetime


class DocumentedRealityReportGeneratedPayload(BaseModel):
    """D290 — fires after a Documented Reality Report is persisted
    (scheduled or on-demand). ``report_id`` is the wire string form
    of the UUID."""

    model_config = ConfigDict(extra="forbid")
    report_id: str
    trigger: Literal["scheduled", "on_demand"]
    corpus_below_floor: bool
    generated_at: datetime


class DocumentedRealityReportViewedPayload(BaseModel):
    """D290 — fires from the frontend ``DocumentedRealityReport`` mount."""

    model_config = ConfigDict(extra="forbid")
    reviewer_hash: str
    report_id: str
    viewed_at: datetime


# D298 — Chunk 38 Change_Directives payloads.
class ChangeDirectiveCreatedPayload(BaseModel):
    """Server-side after ``POST /api/change-directives`` returns 201."""

    model_config = ConfigDict(extra="forbid")
    directive_id: str
    tier: Literal["Operational_Adjustment", "Strategic_Initiative"]
    visibility: str
    created_at: datetime


class ChangeDirectiveTransitionedPayload(BaseModel):
    """Server-side after ``POST /{id}/transition`` returns 200."""

    model_config = ConfigDict(extra="forbid")
    directive_id: str
    from_state: str
    to_state: str
    transitioned_at: datetime


class ChangeDirectiveFlaggedFromReviewPayload(BaseModel):
    """Server-side after ``POST /api/change-directives`` with
    ``flagged_from_session_id`` populated."""

    model_config = ConfigDict(extra="forbid")
    directive_id: str
    flagged_from_session_id: str
    flagged_from_element_name: str | None = None
    created_at: datetime


class EvidenceCriterionAddedPayload(BaseModel):
    """Server-side after ``POST /{id}/criteria`` returns 201."""

    model_config = ConfigDict(extra="forbid")
    directive_id: str
    criterion_id: str
    compilation_status: Literal["proposed", "approved", "manually_authored"]
    has_compiled_query: bool
    created_at: datetime


class ChangeDirectiveMetadataEditedPayload(BaseModel):
    """D307 — emitted after draft PATCH when fields actually change."""

    model_config = ConfigDict(extra="forbid")

    directive_id: UUID
    editor_user_id: UUID
    fields_changed: list[str]
    before_values: dict[str, Any]
    after_values: dict[str, Any]
    edited_at: datetime


class ChangeDirectiveDetailViewedPayload(BaseModel):
    """D308 — emitted from frontend change-directives detail mount."""

    model_config = ConfigDict(extra="forbid")

    directive_id: UUID
    tier: Literal["Operational_Adjustment", "Strategic_Initiative"]
    viewer_user_id: UUID
    viewed_at: datetime


class DecompositionRunStartedPayload(BaseModel):
    """D318 — emitted at decomposition pipeline INSERT (status=running)."""

    model_config = ConfigDict(extra="forbid")
    run_id: UUID
    archive_root_hash: str
    started_at: datetime


class DecompositionRunCompletedPayload(BaseModel):
    """D318 — emitted at completed lifecycle transition."""

    model_config = ConfigDict(extra="forbid")
    run_id: UUID
    archive_root_hash: str
    total_documents: int = Field(ge=0)
    completed_at: datetime


class DecompositionRunFailedPayload(BaseModel):
    """D318 — emitted at failed or paused-with-error transitions."""

    model_config = ConfigDict(extra="forbid")
    run_id: UUID
    archive_root_hash: str
    error_summary: str
    failed_at: datetime


class DecompositionLayer5DecisionRecordedPayload(BaseModel):
    """D330 — emitted when an operator records a Layer 5 decision."""

    model_config = ConfigDict(extra="forbid")
    run_id: UUID
    decision_kind: Literal[
        "accepted_segmented",
        "accepted_null",
        "rerun_finer",
        "rerun_coarser",
        "reject_all_reformulate",
    ]
    modifications_count: int = Field(ge=0)
    rationale_length: int = Field(ge=0)


class DecompositionLayer6ValidationRecordedPayload(BaseModel):
    """D330 — emitted when an operator records Layer 6 sample-CQ validation."""

    model_config = ConfigDict(extra="forbid")
    run_id: UUID
    segment_count: int = Field(ge=0)
    approved_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)


class SegmentationMapRatifiedPayload(BaseModel):
    """D330 — emitted at Layer 7 Segmentation Map ratification."""

    model_config = ConfigDict(extra="forbid")
    run_id: UUID
    map_id: UUID
    payload_hash: str = Field(min_length=1)
    previous_hash: str | None = None
    null_hypothesis_accepted: bool


class DecompositionRerunTriggeredPayload(BaseModel):
    """D330 — emitted at ±1.5x re-run successor INSERT."""

    model_config = ConfigDict(extra="forbid")
    run_id: UUID
    predecessor_run_id: UUID
    direction: Literal["finer", "coarser"]
    lineage_depth: int = Field(ge=1)
    resolution_target: float | None = None


class PermissionMatrixHypothesisGeneratedPayload(BaseModel):
    """D333 — emitted at hypothesis-generator CLI completion.

    `cluster_count` excludes the mandatory NullHypothesis. `has_null_hypothesis`
    is always `True` post-CP4 but is carried explicitly so downstream consumers
    can reason about the field without re-deriving it from the run artifact.
    """

    model_config = ConfigDict(extra="forbid")
    run_id: UUID
    cluster_count: int = Field(ge=0)
    has_null_hypothesis: bool


class PermissionMatrixRatifiedPayload(BaseModel):
    """D331 — emitted at ratify route success.

    `payload_hash` is the SHA-256 canonical-JSON hash computed server-side;
    `cluster_count` excludes any NullHypothesis admitted into the matrix
    (matrix `RoleCluster` rows are concrete clusters only).
    """

    model_config = ConfigDict(extra="forbid")
    matrix_id: UUID
    version_label: str | None = None
    payload_hash: str = Field(min_length=1)
    cluster_count: int = Field(ge=0)


class PermissionClusterDecisionRecordedPayload(BaseModel):
    """D333 — emitted per-cluster during the ratification flow.

    Sub-event of `permission_matrix_ratified` (N2 counter-event 4:3 mapping):
    shares `grace_permission_matrix_ratifications_total` with the parent
    ratify event. `decision_kind` is the operator's per-cluster verdict.
    """

    model_config = ConfigDict(extra="forbid")
    matrix_id: UUID
    cluster_id: str = Field(min_length=1)
    decision_kind: Literal[
        "accept_cluster",
        "reject_cluster",
        "reassign_members",
        "rename_cluster",
    ]


class PermissionMatrixAutoAssignedPayload(BaseModel):
    """D337 — emitted by the drift detector for high-band auto-assignments.

    `drift_band` is the band label only — never a numeric distance score
    (D120/D217). Bands are pinned to the kNN-over-centroids three-band
    classification.
    """

    model_config = ConfigDict(extra="forbid")
    person_grace_id: str = Field(min_length=1)
    cluster_id: str = Field(min_length=1)
    drift_band: Literal["high", "medium", "low"]


class SensitivityReportGeneratedPayload(BaseModel):
    """D342/D343 — emitted at `POST /api/sensitivity/report/generate` success.

    `coverage_band` is the band label only — never the underlying numeric
    score (D120/D217). `corpus_below_floor` mirrors the V<50 carve-out used
    by Documented Reality Reports (D286 precedent).
    """

    model_config = ConfigDict(extra="forbid")
    report_id: UUID
    matrix_id: UUID
    coverage_band: Literal["high", "medium", "low"] | None = None
    tag_count: int = Field(ge=0)
    untagged_rule_count: int = Field(ge=0)
    corpus_below_floor: bool = False


class SensitivityReportViewedPayload(BaseModel):
    """D342 — emitted from the frontend Sensitivity Coverage page when an
    operator opens a specific report. Mirrors `gap_report_viewed` shape."""

    model_config = ConfigDict(extra="forbid")
    report_id: UUID


class SensitivityAuditTrailViewedPayload(BaseModel):
    """D348 — emitted at `GET /api/sensitivity/audit-trail` success.

    `tag` is the bar-delimited tag name passed in the query string;
    `matrix_id` is the active matrix UUID at view time (string form so
    a None / unresolvable matrix can be persisted explicitly).
    `result_count` reports rows AFTER per-row visibility-trim (D343).
    """

    model_config = ConfigDict(extra="forbid")
    tag: str = Field(min_length=1)
    matrix_id: str | None = None
    result_count: int = Field(ge=0)


# D364/D365/D366/D367 — Chunk 44 MCP write-tool payload models.

class McpSessionStartedPayload(BaseModel):
    """Payload for ``mcp_session_started``."""
    model_config = ConfigDict(extra="forbid")
    session_id: str
    phase_state: str | None = None
    agent_id: str | None = None


class McpSessionPhaseAdvancedPayload(BaseModel):
    """Payload for ``mcp_session_phase_advanced``."""
    model_config = ConfigDict(extra="forbid")
    session_id: str
    target_phase: str
    agent_id: str | None = None


class McpSessionClosedPayload(BaseModel):
    """Payload for ``mcp_session_closed``."""
    model_config = ConfigDict(extra="forbid")
    session_id: str
    agent_id: str | None = None


class McpReviewDecidedPayload(BaseModel):
    """Payload for ``mcp_review_decided``."""
    model_config = ConfigDict(extra="forbid")
    session_id: str
    element_name: str
    decision: str
    rationale: str | None = None
    agent_id: str | None = None


class McpLadderingFollowupEmittedPayload(BaseModel):
    """Payload for ``mcp_laddering_followup_emitted``."""
    model_config = ConfigDict(extra="forbid")
    session_id: str
    element_name: str
    question: str
    agent_id: str | None = None


class McpTeachbackCapturedPayload(BaseModel):
    """Payload for ``mcp_teachback_captured``."""
    model_config = ConfigDict(extra="forbid")
    session_id: str
    element_name: str
    narrative: str
    agent_id: str | None = None


class McpDeepLinkGeneratedPayload(BaseModel):
    """Payload for ``mcp_deep_link_generated``."""
    model_config = ConfigDict(extra="forbid")
    session_id: str
    deep_link_url: str
    step: str | None = None
    agent_id: str | None = None


# D375 — Chunk 45 Remote Support Session payload models.


class SupportSessionGrantedPayload(BaseModel):
    """Payload for ``support_session_granted``."""
    model_config = ConfigDict(extra="forbid")
    session_id: str
    granted_to_email: str
    granted_at: str


class SupportSessionRevokedPayload(BaseModel):
    """Payload for ``support_session_revoked``."""
    model_config = ConfigDict(extra="forbid")
    session_id: str
    revoked_at: str


class SupportBannerViewedPayload(BaseModel):
    """Payload for ``support_banner_viewed``."""
    model_config = ConfigDict(extra="forbid")
    session_email: str | None = None
    expires_at: str | None = None


# ---------- Chunk 47 D387/D389 payloads (Signal→Proposal pipeline) ----------


class ProposalGeneratedPayload(BaseModel):
    """Payload for ``proposal_generated`` — emitted by CLI generator."""
    model_config = ConfigDict(extra="forbid")
    proposal_id: str
    signal_type: str
    change_tier: int
    ontology_module: str


class ProposalDecidedPayload(BaseModel):
    """Payload for ``proposal_decided`` — emitted by decide route."""
    model_config = ConfigDict(extra="forbid")
    proposal_id: str
    decision: str
    reviewer_hash: str


class ProposalViewedPayload(BaseModel):
    """Payload for ``proposal_viewed`` — emitted by frontend detail mount."""
    model_config = ConfigDict(extra="forbid")
    proposal_id: str
    change_tier: int


class ProposalExecutedPayload(BaseModel):
    """Payload for ``proposal_executed`` — emitted by execute route (Chunk 48, D392/D393)."""
    model_config = ConfigDict(extra="forbid")
    proposal_id: str
    tier: int
    outcome: str


class CalibrationDecisionRecordedPayload(BaseModel):
    """Payload for ``calibration_decision_recorded`` — emitted by post-decision hook (Chunk 49, D394)."""
    model_config = ConfigDict(extra="forbid")
    proposal_id: str
    tier: int
    decision: str


class CalibrationDashboardViewedPayload(BaseModel):
    """Payload for ``calibration_dashboard_viewed`` — emitted by /autonomy page mount (Chunk 49, D397)."""
    model_config = ConfigDict(extra="forbid")
    tiers_loaded: int


# ---------------------------------------------------------------------------
# Chunk 50 — Agent Daemon telemetry payloads (D398–D401)
# ---------------------------------------------------------------------------


class AgentTickStartedPayload(BaseModel):
    """Payload for ``agent_tick_started``."""
    model_config = ConfigDict(extra="forbid")
    agent_id: str
    observation_time: str


class AgentTickCompletedPayload(BaseModel):
    """Payload for ``agent_tick_completed``."""
    model_config = ConfigDict(extra="forbid")
    agent_id: str
    proposals_evaluated: int
    proposals_applied: int
    suspended_tiers: list[int]
    cooling_finalized: int


class AutonomousProposalAppliedPayload(BaseModel):
    """Payload for ``autonomous_proposal_applied``."""
    model_config = ConfigDict(extra="forbid")
    agent_id: str
    proposal_id: str
    tier: int
    kgcl_command: str
    outcome: str


class CoolingPeriodFinalizedPayload(BaseModel):
    """Payload for ``cooling_period_finalized``."""
    model_config = ConfigDict(extra="forbid")
    proposal_id: str
    outcome: str
    duration_hours: float


class KillSwitchEngagedPayload(BaseModel):
    """Payload for ``kill_switch_engaged``.

    Chunk 65 (D446) extends with ``reason`` and ``previous_state``.
    Defaults ensure backwards-compatible deserialization of pre-Chunk-65 rows.
    Schema version stays at 1 (R5).
    """
    model_config = ConfigDict(extra="forbid")
    actor: str
    all_tiers_disabled: bool
    reason: str = ""
    previous_state: dict[str, bool] = {}


class KillSwitchDisengagedPayload(BaseModel):
    """Payload for ``kill_switch_disengaged``.

    Chunk 65 (D446) extends with ``reason`` and ``restored_state``.
    Defaults ensure backwards-compatible deserialization of pre-Chunk-65 rows.
    Schema version stays at 1 (R5).
    """
    model_config = ConfigDict(extra="forbid")
    actor: str
    all_tiers_enabled: bool
    reason: str = ""
    restored_state: dict[str, bool] = {}


class FederationNamespaceRegisteredPayload(BaseModel):
    """Payload for ``federation_namespace_registered``."""
    model_config = ConfigDict(extra="forbid")
    namespace_id: str
    namespace_type: str
    label_prefix: str | None
    database_name: str


class FederationEntityResolvedPayload(BaseModel):
    """Payload for ``federation_entity_resolved``."""
    model_config = ConfigDict(extra="forbid")
    canonical_grace_id: str | None
    name: str
    entity_type: str
    resolution_method: str
    namespace: str | None


# ---------- Chunk 60 Phase 7 Communication Ingestion frontend payloads ----------


class IngestionDashboardViewedPayload(BaseModel):
    """Payload for ``ingestion_dashboard_viewed``."""
    model_config = ConfigDict(extra="forbid")
    active_runs_count: int = Field(ge=0)


class IngestionSourceDetailViewedPayload(BaseModel):
    """Payload for ``ingestion_source_detail_viewed``."""
    model_config = ConfigDict(extra="forbid")
    source_id: UUID


class ProfileBrowserViewedPayload(BaseModel):
    """Payload for ``profile_browser_viewed``."""
    model_config = ConfigDict(extra="forbid")
    profiles_visible_count: int = Field(ge=0)


class ProfileDetailViewedPayload(BaseModel):
    """Payload for ``profile_detail_viewed``."""
    model_config = ConfigDict(extra="forbid")
    person_id: UUID


class CurationSubmittedPayload(BaseModel):
    """Payload for ``curation_submitted``."""
    model_config = ConfigDict(extra="forbid")
    source_id: UUID
    selected_count: int = Field(ge=1)


class IngestionSettingsChangedPayload(BaseModel):
    """Payload for ``ingestion_settings_changed``."""
    model_config = ConfigDict(extra="forbid")
    setting_key: Literal["deployment_path", "organization_domains", "tier3_band"]


class ReconSourceFilterAppliedPayload(BaseModel):
    """Payload for ``recon_source_filter_applied``."""
    model_config = ConfigDict(extra="forbid")
    filter_type: str
    filter_value: str


_PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "session_started": SessionStartedPayload,
    "phase_entered": PhaseEnteredPayload,
    "phase_exited": PhaseExitedPayload,
    "session_paused": SessionPausedPayload,
    "session_resumed": SessionResumedPayload,
    "session_closed": SessionClosedPayload,
    "close_returned_to_chat": CloseReturnedToChatPayload,
    "protocol_violation_detected": ProtocolViolationDetectedPayload,
    "graph_viewer_opened": GraphViewerOpenedPayload,
    "graph_node_inspected": GraphNodeInspectedPayload,
    "graph_edge_inspected": GraphEdgeInspectedPayload,
    "retrieval_inspector_opened": RetrievalInspectorOpenedPayload,
    "retrieval_query_replayed": RetrievalQueryReplayedPayload,
    "structure_phase_entered": StructurePhaseEnteredPayload,
    "clarify_phase_entered": ClarifyPhaseEnteredPayload,
    "laddering_step_completed": LadderingStepCompletedPayload,
    "card_sort_completed": CardSortCompletedPayload,
    "teach_back_completed": TeachBackCompletedPayload,
    "scope_segment_changed": ScopeSegmentChangedPayload,
    "cq_authored": CQAuthoredPayload,
    "cq_candidate_accepted": CQCandidateAcceptedPayload,
    "cq_candidate_rejected": CQCandidateRejectedPayload,
    # D234 — Chunk 30 entries.
    "claim_disposition_accepted": ClaimDispositionAcceptedPayload,
    "claim_disposition_rejected": ClaimDispositionRejectedPayload,
    "llm_provider_switched": LLMProviderSwitchedPayload,
    "sources_configured": SourcesConfiguredPayload,
    "airgap_mode_toggled": AirgapModeToggledPayload,
    # D282 — Chunk 36 entries (Reconciliation Layer foundation).
    "gap_report_generated": GapReportGeneratedPayload,
    "gap_report_viewed": GapReportViewedPayload,
    # D290 — Chunk 37 entries (cross-executive Reconciliation).
    "divergence_map_generated": DivergenceMapGeneratedPayload,
    "divergence_map_viewed": DivergenceMapViewedPayload,
    "documented_reality_report_generated": DocumentedRealityReportGeneratedPayload,
    "documented_reality_report_viewed": DocumentedRealityReportViewedPayload,
    # D298 — Chunk 38 entries (Change_Directives foundation).
    "change_directive_created": ChangeDirectiveCreatedPayload,
    "change_directive_transitioned": ChangeDirectiveTransitionedPayload,
    "change_directive_flagged_from_review": ChangeDirectiveFlaggedFromReviewPayload,
    "change_directive_evidence_criterion_added": EvidenceCriterionAddedPayload,
    "change_directive_metadata_edited": ChangeDirectiveMetadataEditedPayload,
    "change_directive_detail_viewed": ChangeDirectiveDetailViewedPayload,
    # D318 — Chunk 40 decomposition pipeline lifecycle.
    "decomposition_run_started": DecompositionRunStartedPayload,
    "decomposition_run_completed": DecompositionRunCompletedPayload,
    "decomposition_run_failed": DecompositionRunFailedPayload,
    # D330 — Chunk 41 decomposition Layer 5/6/7 + re-run telemetry.
    "decomposition_layer5_decision_recorded": DecompositionLayer5DecisionRecordedPayload,
    "decomposition_layer6_validation_recorded": DecompositionLayer6ValidationRecordedPayload,
    "segmentation_map_ratified": SegmentationMapRatifiedPayload,
    "decomposition_rerun_triggered": DecompositionRerunTriggeredPayload,
    # D331/D333/D337 — Chunk 42 Permission Matrix entries.
    "permission_matrix_hypothesis_generated": PermissionMatrixHypothesisGeneratedPayload,
    "permission_matrix_ratified": PermissionMatrixRatifiedPayload,
    "permission_cluster_decision_recorded": PermissionClusterDecisionRecordedPayload,
    "permission_matrix_auto_assigned": PermissionMatrixAutoAssignedPayload,
    # D342/D343/D348 — Chunk 43 entries.
    "sensitivity_report_generated": SensitivityReportGeneratedPayload,
    "sensitivity_report_viewed": SensitivityReportViewedPayload,
    "sensitivity_audit_trail_viewed": SensitivityAuditTrailViewedPayload,
    # D364/D365/D366/D367 — Chunk 44 MCP write-tool entries (CF1 lockstep).
    "mcp_session_started": McpSessionStartedPayload,
    "mcp_session_phase_advanced": McpSessionPhaseAdvancedPayload,
    "mcp_session_closed": McpSessionClosedPayload,
    "mcp_review_decided": McpReviewDecidedPayload,
    "mcp_laddering_followup_emitted": McpLadderingFollowupEmittedPayload,
    "mcp_teachback_captured": McpTeachbackCapturedPayload,
    "mcp_deep_link_generated": McpDeepLinkGeneratedPayload,
    # D375 — Chunk 45 Remote Support Session entries (CF1 lockstep).
    "support_session_granted": SupportSessionGrantedPayload,
    "support_session_revoked": SupportSessionRevokedPayload,
    "support_banner_viewed": SupportBannerViewedPayload,
    # D387/D389 — Chunk 47 Signal→Proposal pipeline entries (CF1 lockstep).
    "proposal_generated": ProposalGeneratedPayload,
    "proposal_decided": ProposalDecidedPayload,
    "proposal_viewed": ProposalViewedPayload,
    # D392/D393 — Chunk 48 KGCL Change Executor entry (CF1 lockstep).
    "proposal_executed": ProposalExecutedPayload,
    # D394–D397 — Chunk 49 Earned Autonomy Calibration entries (CF1 lockstep).
    "calibration_decision_recorded": CalibrationDecisionRecordedPayload,
    "calibration_dashboard_viewed": CalibrationDashboardViewedPayload,
    # D398–D401 — Chunk 50 Agent Daemon entries (CF1 lockstep).
    "agent_tick_started": AgentTickStartedPayload,
    "agent_tick_completed": AgentTickCompletedPayload,
    "autonomous_proposal_applied": AutonomousProposalAppliedPayload,
    "cooling_period_finalized": CoolingPeriodFinalizedPayload,
    "kill_switch_engaged": KillSwitchEngagedPayload,
    "kill_switch_disengaged": KillSwitchDisengagedPayload,
    # D402–D405 — Chunk 51 Federation Infrastructure entries (CF1 lockstep).
    "federation_namespace_registered": FederationNamespaceRegisteredPayload,
    "federation_entity_resolved": FederationEntityResolvedPayload,
    # Chunk 60 — Phase 7 Communication Ingestion frontend entries (CF1 lockstep).
    "ingestion_dashboard_viewed": IngestionDashboardViewedPayload,
    "ingestion_source_detail_viewed": IngestionSourceDetailViewedPayload,
    "profile_browser_viewed": ProfileBrowserViewedPayload,
    "profile_detail_viewed": ProfileDetailViewedPayload,
    "curation_submitted": CurationSubmittedPayload,
    "ingestion_settings_changed": IngestionSettingsChangedPayload,
    "recon_source_filter_applied": ReconSourceFilterAppliedPayload,
}


class ElicitationEventEnvelope(BaseModel):
    """Outer envelope for every telemetry event (protocol §8.2).

    The `payload` field is validated against `event_type` in
    :func:`validate_payload_for_event_type`. Pydantic alone cannot
    enforce the cross-field constraint, so the API layer calls the
    helper after model validation.
    """

    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    event_type: EventType
    session_id: UUID
    actor_type: ActorType
    phase_name: PhaseName
    emitted_at: datetime
    schema_version: int = Field(ge=1)
    grace_version: str = Field(min_length=1)
    payload: dict[str, Any]
    payload_schema_version: int = Field(ge=1)
    # D364 — Chunk 44 agent identity fields (additive, optional).
    agent_id: str | None = None
    agent_display_name: str | None = None
    delegation_source: Literal[
        "user_direct", "agent_on_behalf", "system_scheduled"
    ] | None = None

    @field_validator("grace_version")
    @classmethod
    def _non_empty_grace_version(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("grace_version must be non-empty")
        return v


def payload_model_for(event_type: str) -> type[BaseModel] | None:
    return _PAYLOAD_MODELS.get(event_type)


def validate_payload_for_event_type(
    event_type: str, payload: dict[str, Any]
) -> BaseModel:
    """Validate `payload` against the model bound to `event_type`.

    Raises :class:`pydantic.ValidationError` on mismatch so FastAPI can
    report a 422 response with a structured error body.
    """
    model = payload_model_for(event_type)
    if model is None:
        raise ValidationError.from_exception_data(
            title="ElicitationEventEnvelope",
            line_errors=[
                {
                    "type": "value_error",
                    "loc": ("event_type",),
                    "msg": f"Unknown event_type: {event_type}",
                    "input": event_type,
                    "ctx": {"error": ValueError(f"Unknown event_type: {event_type}")},
                }
            ],
        )
    return model.model_validate(payload)


class ElicitationEventAck(BaseModel):
    """Response body for POST /api/elicitation/events."""

    event_id: UUID
    accepted_at: datetime
