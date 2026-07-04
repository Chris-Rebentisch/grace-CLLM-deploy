// D204 contract mirror of src/regeneration/regeneration_models.py.
// Field names must match the Pydantic models verbatim. Enforced by
// scripts/check-api-contract.sh.

export type PhaseState =
  | "prepare"
  | "open"
  | "structure"
  | "clarify"
  | "close"
  | "none";

export type SpanDetectorMode =
  | "sentence_fallback"
  | "llm_judged"
  | "hybrid";

export type CertaintyBand =
  | "high"
  | "medium"
  | "low"
  | "insufficient_evidence";

export type SpanConfidence = "high" | "medium" | "low";

export type RetrievalQuery = {
  query_text: string;
  seed_entity_ids: string[];
  temporal_start: string | null;
  temporal_end: string | null;
  entity_types: string[];
  top_k: number;
  iterative_mode: string | null;
};

// ---------- Chunk 28 D213 retrieval contract mirror ----------
// Field names mirror src/retrieval/retrieval_models.py verbatim.

export type RetrievalCandidate = {
  grace_id: string;
  entity_type: string;
  name: string;
  properties: Record<string, unknown>;
  score: number;
  strategy: string;
  rank: number;
  hop_distance: number | null;
  path: string[] | null;
};

export type FusedCandidate = {
  grace_id: string;
  entity_type: string;
  name: string;
  properties: Record<string, unknown>;
  rrf_score: number;
  contributing_strategies: string[];
  strategy_ranks: Record<string, number>;
};

export type RankedResult = {
  grace_id: string;
  entity_type: string;
  name: string;
  properties: Record<string, unknown>;
  rerank_score: number;
  rrf_score: number;
  contributing_strategies: string[];
  hop_distance: number | null;
};

export type RetrievalResponse = {
  query: string;
  results: RankedResult[];
  serialized_context: string;
  serialization_format: string;
  total_candidates: number;
  strategy_contributions: Record<string, number>;
  latency_ms: Record<string, number>;
  retrieval_mode: string;
  query_intents: string[];
  properties_omitted_count: number;
  multi_hop_proxy_score: number;
  latency_p95_by_mode_ms: Record<string, number>;
  query_event_id?: string;
};

// ---------- Chunk 28 D213 graph read contract mirror ----------
// Field names mirror src/graph/graph_read_models.py verbatim.

export type EntityRecord = {
  grace_id: string;
  entity_type: string;
  properties: Record<string, unknown>;
  source_document_id: string | null;
  extraction_event_id: string | null;
  ontology_module: string | null;
  human_validated: boolean;
  valid_from: string | null;
  valid_to: string | null;
  extraction_confidence: number | null;
};

export type RelationshipRecord = {
  grace_id: string;
  relationship_type: string;
  source_grace_id: string;
  target_grace_id: string;
  properties: Record<string, unknown>;
  source_document_id: string | null;
  extraction_event_id: string | null;
  ontology_module: string | null;
  human_validated: boolean;
  extraction_confidence: number | null;
};

export type PagedEntitiesResponse = {
  entities: EntityRecord[];
  next_cursor: string | null;
};

export type PagedRelationshipsResponse = {
  relationships: RelationshipRecord[];
  next_cursor: string | null;
};

export type NeighborhoodResponse = {
  seed: Record<string, unknown>;
  neighbors: Record<string, unknown>[];
  edges: Record<string, unknown>[];
};

export type RegenOverrides = {
  regeneration_model: string | null;
  temperature: number | null;
  response_max_tokens: number | null;
};

export type RegenerationQuery = {
  query_text: string;
  retrieval_query: RetrievalQuery | null;
  phase_state: PhaseState;
  overrides: RegenOverrides | null;
};

export type ClaimSpan = {
  text: string;
  sentence_indices: number[];
  start_char: number | null;
  end_char: number | null;
  certainty_band: CertaintyBand;
  span_confidence: SpanConfidence;
  supporting_grace_ids: string[];
};

export type ResponseMetadata = {
  context_truncated: boolean;
  span_detector_mode: SpanDetectorMode;
  phase_style_applied: string;
  span_detection_note: string | null;
  model_override_applied: boolean;
};

export type RegenerationResponse = {
  query: string;
  response_text: string;
  claim_spans: ClaimSpan[];
  phase_state: PhaseState;
  contributing_grace_ids: string[];
  strategy_contributions: Record<string, number>;
  latency_ms: Record<string, number>;
  token_usage: Record<string, number>;
  model: string;
  provider: string;
  retrieval_mode: string;
  response_metadata: ResponseMetadata;
};

export type RegenerationError = {
  stage: "retrieve" | "assemble" | "synthesize" | "span_detect";
  error_type: string;
  error_message: string;
  partial_response: string | null;
  request_id: string | null;
  stage_latencies_ms: Record<string, number>;
};

export type RegenerationConfigResponse = {
  system_budget_tokens: number;
  context_budget_tokens: number;
  query_budget_tokens: number;
  response_budget_tokens: number;
  total_input_budget_tokens: number;
  regeneration_model: string;
  regeneration_temperature: number;
  chars_per_token: number;
  enable_claim_span_detection: boolean;
  span_detector_mode: SpanDetectorMode;
  phase_style_overrides_applied: string[];
};

// ---------- Chunk 27 additions: close-summary + close-confirm ----------

export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  claim_spans: ClaimSpan[] | null;
  sent_at: string;
};

export type SessionSummary = {
  narrative: string;
  ontology_changes: Record<string, unknown>[];
  cqs_flipped_state: Record<string, unknown>[];
  decisions_recorded: Record<string, unknown>[];
  deferred_items: Record<string, unknown>[];
  certainty_band_shifts: Record<string, unknown>[];
};

export type CloseSummaryRequest = {
  session_id: string;
  phase_state: "close";
  messages: ChatMessage[];
  phase_durations_ms: Record<string, number>;
};

export type CloseSummaryResponse = {
  session_id: string;
  summary: SessionSummary;
  request_id: string;
};

export type CloseConfirmRequest = {
  session_id: string;
  final_summary: SessionSummary;
  summary_edited: boolean;
  summary_rejected: boolean;
};

export type CloseConfirmResponse = {
  session_id: string;
  session_status: "closed";
  recorded_at: string;
};

// ---------- Elicitation telemetry envelope (protocol §8.2) ----------

export type ElicitationActorType = "human" | "system" | "agent";

export type ElicitationEventType =
  | "session_started"
  | "phase_entered"
  | "phase_exited"
  | "session_paused"
  | "session_resumed"
  | "session_closed"
  | "close_returned_to_chat"
  | "protocol_violation_detected"
  | "graph_viewer_opened"
  | "graph_node_inspected"
  | "graph_edge_inspected"
  | "retrieval_inspector_opened"
  | "retrieval_query_replayed"
  | "structure_phase_entered"
  | "clarify_phase_entered"
  | "laddering_step_completed"
  | "card_sort_completed"
  | "teach_back_completed"
  | "scope_segment_changed"
  | "cq_authored"
  | "cq_candidate_accepted"
  | "cq_candidate_rejected"
  // D234 — Chunk 30 catalog extension (claim review + LLM config + sources).
  | "claim_disposition_accepted"
  | "claim_disposition_rejected"
  | "llm_provider_switched"
  | "sources_configured"
  | "airgap_mode_toggled"
  // D282 — Chunk 36 catalog extension (Reconciliation Layer foundation).
  | "gap_report_generated"
  | "gap_report_viewed"
  // D290 — Chunk 37 catalog extension (Reconciliation cross-executive).
  | "divergence_map_generated"
  | "divergence_map_viewed"
  | "documented_reality_report_generated"
  | "documented_reality_report_viewed"
  // D298 — Chunk 38 catalog extension (Change_Directives foundation).
  | "change_directive_created"
  | "change_directive_transitioned"
  | "change_directive_flagged_from_review"
  | "change_directive_evidence_criterion_added"
  // D307/D308 — Chunk 39 realization telemetry.
  | "change_directive_metadata_edited"
  | "change_directive_detail_viewed"
  // D318 — Chunk 40 decomposition pipeline lifecycle (CLI-only, CF1 sync).
  | "decomposition_run_started"
  | "decomposition_run_completed"
  | "decomposition_run_failed"
  // D330 — Chunk 41 decomposition Layer 5/6/7 + rerun events (CF1 lockstep).
  | "decomposition_layer5_decision_recorded"
  | "decomposition_layer6_validation_recorded"
  | "segmentation_map_ratified"
  | "decomposition_rerun_triggered"
  // D331/D333/D337 — Chunk 42 Permission Matrix telemetry (CF1 lockstep).
  | "permission_matrix_hypothesis_generated"
  | "permission_matrix_ratified"
  | "permission_cluster_decision_recorded"
  | "permission_matrix_auto_assigned"
  // Chunk 43 — Sensitivity Gate Compliance Surface (CF1 lockstep).
  | "sensitivity_report_generated"
  | "sensitivity_report_viewed"
  | "sensitivity_audit_trail_viewed"
  // D364/D365/D366/D367 — Chunk 44 MCP write-tool telemetry (CF1 lockstep).
  | "mcp_session_started"
  | "mcp_session_phase_advanced"
  | "mcp_session_closed"
  | "mcp_review_decided"
  | "mcp_laddering_followup_emitted"
  | "mcp_teachback_captured"
  | "mcp_deep_link_generated"
  // D375 — Chunk 45 Remote Support Session telemetry (CF1 lockstep).
  | "support_session_granted"
  | "support_session_revoked"
  | "support_banner_viewed"
  // D387/D389 — Chunk 47 Signal→Proposal pipeline telemetry (CF1 lockstep).
  | "proposal_generated"
  | "proposal_decided"
  | "proposal_viewed"
  // D392/D393 — Chunk 48 KGCL Change Executor telemetry (CF1 lockstep).
  | "proposal_executed"
  // D394–D397 — Chunk 49 Earned Autonomy Calibration telemetry (CF1 lockstep).
  | "calibration_decision_recorded"
  | "calibration_dashboard_viewed"
  // D398–D401 — Chunk 50 Agent Daemon (CF1 lockstep).
  | "agent_tick_started"
  | "agent_tick_completed"
  | "autonomous_proposal_applied"
  | "cooling_period_finalized"
  | "kill_switch_engaged"
  | "kill_switch_disengaged"
  // Chunk 51 (D402–D405) — Federation Infrastructure telemetry.
  | "federation_namespace_registered"
  | "federation_entity_resolved"
  // Chunk 60 — Phase 7 Communication Ingestion frontend surfaces (CF1 lockstep).
  | "ingestion_dashboard_viewed"
  | "ingestion_source_detail_viewed"
  | "profile_browser_viewed"
  | "profile_detail_viewed"
  | "curation_submitted"
  | "ingestion_settings_changed"
  | "recon_source_filter_applied";

// ---------- Chunk 28 D215 payloads ----------

export type GraphViewerOpenedPayload = {
  scope: string;
  entity_count_estimated: number | null;
};

export type GraphNodeInspectedPayload = {
  entity_type: string;
  grace_id_hash: string;
};

export type GraphEdgeInspectedPayload = {
  relationship_type: string;
  grace_id_hash: string;
};

export type RetrievalInspectorOpenedPayload = {
  source: "chat_link" | "direct_nav" | "replay_button";
};

export type RetrievalQueryReplayedPayload = {
  strategies_fired: string[];
  latency_ms_total: number;
};

// ---------- Chunk 29 D228 payloads ----------

export type StructureDecisionPayload = {
  evidence_items_viewed: string[];
  evidence_items_available: string[];
  declared_certainty_band: CertaintyBand;
};

export type ClarifyDecisionPayload = {
  decision_id_hash: string;
  position_changed: boolean;
  prior_decision_id: string | null;
  clarify_duration_ms: number;
};

export type StructurePhaseEnteredPayload = {
  entered_phase: "structure";
  entered_at: string;
  mode: string;
  mode_rationale: string;
};

export type ClarifyPhaseEnteredPayload = {
  entered_phase: "clarify";
  entered_at: string;
  unresolved_decision_count: number;
};

export type LadderingStepCompletedPayload = {
  step_index: number;
  parent_grace_id_hash: string;
  child_grace_id_hashes: string[];
  step_duration_ms: number;
};

export type CardSortCompletedPayload = {
  card_count: number;
  category_count: number;
  recategorization_count: number;
  duration_ms: number;
};

export type TeachBackCompletedPayload = {
  item_index: number;
  sentence_count: number;
  correct_count: number;
  wrong_count: number;
  missing_something_count: number;
  correction_chars_total: number;
};

export type ScopeSegmentChangedPayload = {
  prior_scope: string;
  new_scope: string;
  segment_count: number;
};

export type CQAuthoredPayload = {
  cq_id_hash: string;
  cq_type: string;
  domain: string;
  authoring_source: "from_scratch" | "from_candidate";
};

export type CQCandidateAcceptedPayload = {
  candidate_id_hash: string;
  source_origin: "local_documents" | "web_presence" | "ontology_seed";
  edited_before_accept: boolean;
};

export type CQCandidateRejectedPayload = {
  candidate_id_hash: string;
  source_origin: "local_documents" | "web_presence" | "ontology_seed";
  reject_reason_category: string;
};

// ---------- Chunk 29 review session types ----------

export type ReviewDecisionType =
  | "approved"
  | "renamed"
  | "edited"
  | "split"
  | "merged"
  | "rejected"
  | "redirected"
  | "reclassified"
  | "auto_approved";

export type ReviewSession = {
  session_id: string;
  status: string;
  reviewer: string;
  seed_schema_data: Record<string, unknown>;
  created_at: string;
};

export type ReviewElement = {
  element_type: string;
  element_name: string;
  decision: ReviewDecisionType | null;
  notes?: string | null;
  // D522 session — plain-language presentation fields surfaced by /elements.
  name?: string;
  display_label?: string;
  description?: string;
  plain_description?: string;
  example_snippet?: string | null;
  evidence_document_count?: number;
  answerable_questions?: string[];
  answerable_cq_count?: number;
  status?: string;
};

// D522 session — conversational review assistant (the "Something's off?" drawer).
export type ReviewAssistTurn = { role: "user" | "assistant"; content: string };

export type ReviewAssistAction = {
  action: "keep" | "rename" | "merge" | "skip" | "none";
  button_label: string;
  rationale: string;
  new_name: string | null;
  merge_with: string | null;
};

export type ReviewAssistResponse = {
  reply: string;
  suggested_action: ReviewAssistAction | null;
};

export type ReviewDecision = {
  element_type: string;
  element_name: string;
  decision: ReviewDecisionType;
  notes: string | null;
  evidence_bundle_id: string | null;
};

export type ReviewProgress = {
  total_elements: number;
  reviewed_elements: number;
  entity_types_reviewed: number;
  relationship_types_reviewed: number;
};

export type CQImpactPreview = {
  element_name: string;
  hypothetical_decision: string;
  cqs_affected: Array<{
    cq_id: string;
    cq_text: string;
    impact: string;
  }>;
  coverage_before: number;
  coverage_after: number;
  cqs_that_lose_coverage: number;
  cqs_that_gain_coverage: number;
};

// ---------- Chunk 29 CQ canvas types ----------

export type CQCard = {
  cq_id: string;
  cq_text: string;
  cq_type: string;
  domain: string;
  coverage_band: "green" | "amber" | "red" | "gray";
  dependent_types: string[];
};

export type CQCandidate = {
  id: string;
  session_id: string;
  cq_text: string;
  cq_type: string;
  source_origin: "local_documents" | "web_presence" | "ontology_seed";
  validation_status: "quarantined" | "approved" | "rejected" | "human_authored";
  created_at: string;
  metadata: Record<string, unknown>;
};

export type SegmentRow = {
  module_name: string;
  entity_count: number;
};

export type ElicitationEventEnvelope = {
  event_id: string;
  event_type: ElicitationEventType;
  session_id: string;
  actor_type: ElicitationActorType;
  phase_name: PhaseState;
  emitted_at: string;
  schema_version: number;
  grace_version: string;
  payload: Record<string, unknown>;
  payload_schema_version: number;
  // D364 — Chunk 44 agent identity fields (additive, optional).
  agent_id?: string | null;
  agent_display_name?: string | null;
  delegation_source?: "user_direct" | "agent_on_behalf" | "system_scheduled" | null;
};

export type ElicitationEventAck = {
  event_id: string;
  accepted_at: string;
};

// ---------- Chunk 30 D230 claim review wire types ----------

export type ClaimEvidenceSpan = {
  text: string;
  start_char: number;
  end_char: number;
};

export type ClaimRecord = {
  claim_id: string;
  extraction_event_id: string | null;
  entity_type: string | null;
  relationship_type: string | null;
  subject_name: string;
  predicate: string | null;
  object_name: string | null;
  evidence_spans: ClaimEvidenceSpan[];
  status: string;
  verdict: string | null;
  decision_source: string | null;
  human_decided_at: string | null;
  ontology_module: string | null;
  source_document_id: string | null;
  constraint_violations: Record<string, unknown>[] | null;
  verifier_contradiction_reason: string | null;
  supersedes_claim_id: string | null;
  created_at: string;
};

export type ClaimListResponse = {
  items: ClaimRecord[];
  next_cursor: string | null;
  total_count: number;
};

export type ClaimListFilters = {
  status?: string;
  verdict?: string;
  ontology_module?: string;
  source_document_id?: string;
};

export type AcceptClaimModified = {
  subject_name: string;
  predicate?: string | null;
  object_name?: string | null;
  properties_json?: Record<string, unknown> | null;
};

export type AcceptClaimRequest = {
  reviewer: string;
  notes?: string | null;
  modified_claim?: AcceptClaimModified | null;
};

export type AcceptClaimResponse = {
  claim_id: string;
  status: string;
  graph_write_result: Record<string, unknown>;
  superseded_claim_id: string | null;
};

export type RejectClaimRequest = {
  reviewer: string;
  notes?: string | null;
};

export type RejectClaimResponse = {
  claim_id: string;
  status: string;
};

// ---------- Chunk 30 D233 source-selector wire types ----------
// Mirror src/discovery/source_scanner.py — flat list of directory rows.

export type SourcesScanDirectoryNode = {
  name: string;
  path: string;
  total_files: number;
  document_files: number;
  total_size_bytes: number;
  document_size_bytes: number;
  suggested_include: boolean;
};

export type ConfigureSourcesRequest = {
  selected_paths: string[];
  file_type_filters?: string[] | null;
};

export type ConfigureSourcesResponse = {
  manifest_path: string;
  total_files: number;
  by_extension: Record<string, number>;
  estimated_processing_minutes: number;
};

// ---------- In-app file browser + processing (Sources UI) ----------

export type BrowseEntry = {
  name: string;
  path: string;
  is_dir: boolean;
  size_bytes: number;
  supported: boolean;
};

export type BrowseResponse = {
  path: string;
  parent: string | null;
  error?: string;
  entries: BrowseEntry[];
};

export type ProcessStartResponse = {
  status: string;
  message: string;
};

export type ProcessingStatus = {
  by_status?: Record<string, number>;
  by_domain?: Record<string, number>;
  by_file_type?: Record<string, number>;
  total_documents?: number;
  total_words?: number;
};

// ---------- CQ generation from documents (CQ-first discovery) ----------

export type CQGenerationStartResponse = {
  status: string;
  run_id: string;
  message?: string;
};

export type CQGenerationStatus = {
  run_id: string;
  completed_at?: string | null;
  total_cqs_generated?: number;
  total_duration_ms?: number;
  pass_results?: unknown[];
  cancelled?: boolean;
  error?: string | null;
};

export type CQMergeStartResponse = {
  status: string;
  run_id: string;
  message?: string;
};

// Status of the three-tier CQ merge run (clusters near-duplicate CQs into a
// collapsed, schema-only canonical review set). Served from the in-memory
// MergeRun via GET /api/discovery/merge-status/{run_id}.
export type CQMergeStatus = {
  run_id: string;
  status?: string; // "running" | "completed" | "failed"
  completed_at?: string | null;
  total_cqs_input?: number;
  total_clusters?: number;
  total_singletons?: number;
  total_gap_fills?: number;
  canonical_count?: number;
  quality_distribution?: Record<string, number>;
  error_message?: string | null;
  error?: string; // present only when the run_id is unknown
};

// Latest completed CQ merge run (DB-backed; survives restart). Lets the
// onboarding header lead with the collapsed canonical review-set size.
export type CQMergeLatest = {
  has_merge: boolean;
  run_id?: string;
  canonical_count?: number;
  total_cqs_input?: number;
  total_gap_fills?: number;
  completed_at?: string | null;
};

export type CQSummary = {
  total: number;
  by_status?: Record<string, number>;
  by_domain?: Record<string, number>;
  [k: string]: unknown;
};

// ---------- Ontology proposal bootstrap (schema extract -> merge -> review) ----------

export type SchemaRunStartResponse = {
  status: string;
  run_id: string;
  message?: string;
  total_entity_types?: number;
  total_relationships?: number;
  merged_entity_types?: number;
  merged_relationships?: number;
  dry_run?: boolean;
};

export type SchemaRunStatus = {
  status: string;
  run_id?: string;
  total_entity_types?: number;
  total_relationships?: number;
  merged_entity_types?: number;
  merged_relationships?: number;
  error?: string;
};

export type SeedSchemaData = {
  entity_types?: unknown[];
  relationships?: unknown[];
  [k: string]: unknown;
};

export type StartReviewRequest = {
  merge_run_id: string;
  reviewer: string;
  seed_schema_data: SeedSchemaData;
};

export type ReviewSessionResponse = {
  id: string;
  status: string;
  reviewer: string;
  total_entity_types: number;
  total_relationships: number;
  [k: string]: unknown;
};

// ---------- Chunk 30 D232 LLM config wire types ----------

export type LLMConfig = {
  provider: string;
  model: string;
  base_url: string;
  timeout: number;
  api_key_set: boolean;
  api_key_preview: string;
  airgap_mode: boolean;
};

export type SaveLLMConfigRequest = {
  provider: string;
  model: string;
  base_url: string;
  timeout: number;
  api_key?: string | null;
  airgap_mode?: boolean | null;
};

export type TestLLMConfigRequest = {
  provider: string;
  model: string;
  base_url?: string;
  timeout?: number;
  api_key?: string;
};

export type TestLLMConfigResponse = {
  healthy: boolean;
  model_available: boolean;
  provider: string;
  model: string;
  test_response: string;
  response_time_ms: number;
  error: string;
};

export type ProviderRegistryEntry = {
  id: string;
  label: string;
  description: string;
  requires_api_key: boolean;
  requires_base_url: boolean;
  default_model: string;
  default_base_url: string;
  popular_models: string[];
  preset_endpoints?: { label: string; base_url: string; default_model: string }[];
};

// ---------- Chunk 35a D266 retrieval feedback ----------

export type FeedbackVote = "up" | "down";

export type FeedbackRequest = {
  query_event_id: string;
  vote: FeedbackVote;
  freetext?: string;
};

export type FeedbackResponse = {
  feedback_id: string;
  query_event_id: string;
  vote: FeedbackVote;
  submitted_at: string;
};

// ---------- Chunk 36 D280 / D283 reconciliation surface re-exports ----------
// Recon types live in their own module to keep diffing localized.

export type {
  EmphasizedWithEvidenceItem,
  EmphasizedWithoutEvidenceItem,
  UnemphasizedInEvidenceItem,
  GapReportSection,
  GapReportResponse,
  GenerateGapReportRequest,
  // Chunk 37 — Divergence Map (D284)
  DivergenceMapEntry,
  DivergenceMapBucket,
  DivergenceMapBucketName,
  DivergenceMapResponse,
  DivergenceMapGenerateRequest,
  // Chunk 37 — Documented Reality Report (D286/D287)
  DocumentedRealityAggregations,
  DocumentedRealityTrigger,
  DocumentedRealityReportResponse,
  DocumentedRealityCadence,
  DocumentedRealityScheduleResponse,
  DocumentedRealityScheduleRequest,
  DocumentedRealityScheduleUpdateRequest,
} from "./recon-types";

// ---------- Chunk 38 D291–D298 Change Directives ----------

export type DirectiveStatus =
  | "draft"
  | "active"
  | "realized"
  | "abandoned"
  | "superseded";

export type VisibilityMode =
  | "permission_matrix_default"
  | "private_to_self"
  | "private_to_named_list"
  | "scoped_to_role_cluster";

export type CompilationStatus =
  | "proposed"
  | "approved"
  | "manually_authored";

export type EvidenceCriterion = {
  criterion_id: string;
  directive_id: string;
  natural_language: string;
  measurement_kind?: string | null;
  target_value?: string | null;
  target_satisfied_when?: string | null;
  compiled_query?: string | null;
  compilation_status: CompilationStatus;
  error_detail?: string | null;
  created_at: string;
  updated_at: string;
};

export type ChangeDirectivePatchBody = {
  title?: string | null;
  description?: string | null;
  affected_segments?: string[] | null;
  extension_metadata?: Record<string, unknown> | null;
  effective_date?: string | null;
  target_state_description?: string | null;
  realization_horizon?: string | null;
  responsible_executive?: string | null;
};

export type VelocityBand = "accelerating" | "steady" | "slowing" | "stalled";

export type CoveringDirective = {
  directive_id: string;
  tier: string;
  title: string;
  status: DirectiveStatus;
  authored_at: string;
  affected_segments: string[];
  progress_percentage?: number | null;
  velocity_band?: VelocityBand | null;
  is_stalled?: boolean;
};

export type CriterionCounterEvidence = {
  first_seen_at?: string | null;
  last_seen_at?: string | null;
  sample_grace_ids: string[];
};

export type CriterionEvidenceResult = {
  criterion_id: string;
  satisfied: boolean;
  measured_value?: number | null;
  query_executed_at: string;
  result_hash: string;
  sample_grace_ids: string[];
  counter_evidence?: CriterionCounterEvidence | null;
};

export type RealizationSnapshotPayload = {
  id: string;
  directive_id: string;
  snapshot_at: string;
  criteria_results: CriterionEvidenceResult[];
  progress_percentage?: number | null;
  evidence_count_consistent?: number | null;
  evidence_count_counter?: number | null;
  first_evidence_seen_at?: string | null;
  last_counter_evidence_seen_at?: string | null;
  criteria_all_satisfied?: boolean | null;
  created_at: string;
  is_stalled: boolean;
  velocity_band?: VelocityBand | null;
};

export type TransitionRequest = {
  to_state: DirectiveStatus;
  reason?: string | null;
  superseded_by_directive_id?: string | null;
};

export type CriterionCreateRequest = {
  natural_language: string;
  measurement_kind?: string | null;
  target_value?: string | null;
  target_satisfied_when?: string | null;
};

export type CriterionPatchRequest = {
  action: "approve" | "edit" | "manual_override";
  compiled_query?: string | null;
};

export type ChangeDirectiveCreateRequest = {
  tier: "Operational_Adjustment" | "Strategic_Initiative";
  title: string;
  description: string;
  affected_segments: string[];
  visibility: VisibilityMode;
  visibility_named_list?: string[] | null;
  visibility_role_cluster?: string | null;
  extension_metadata?: Record<string, unknown> | null;
  effective_date?: string | null;
  target_state_description?: string | null;
  realization_horizon?: string | null;
  responsible_executive?: string | null;
  initial_evidence_criteria?: string[] | null;
  flagged_from_session_id?: string | null;
  flagged_from_element_name?: string | null;
};

// ---------- Chunk 41 D330 — Decomposition Layer 5/6/7 + rerun ----------

export type DecompositionLayer5DecisionRecordedPayload = {
  run_id: string;
  decision_kind:
    | "accepted_segmented"
    | "accepted_null"
    | "rerun_finer"
    | "rerun_coarser"
    | "reject_all_reformulate";
  modifications_count: number;
  rationale_length: number;
};

export type DecompositionLayer6ValidationRecordedPayload = {
  run_id: string;
  segment_count: number;
  approved_count: number;
  rejected_count: number;
};

export type SegmentationMapRatifiedPayload = {
  run_id: string;
  map_id: string;
  payload_hash: string;
  previous_hash: string | null;
  null_hypothesis_accepted: boolean;
};

export type DecompositionRerunTriggeredPayload = {
  run_id: string;
  predecessor_run_id: string;
  direction: "finer" | "coarser";
  lineage_depth: number;
  resolution_target: string | null;
};

// CF1 lockstep wire interfaces for the Decomposition surface (D319 carve-out).

export type Layer5DecisionPayload = {
  decision_kind:
    | "accepted_segmented"
    | "accepted_null"
    | "rerun_finer"
    | "rerun_coarser"
    | "reject_all_reformulate";
  segment_modifications?: Array<Record<string, unknown>> | null;
  rationale?: string | null;
};

export type Layer6ValidationPayload = {
  segments: Array<{
    segment_name: string;
    cq_sample: string[];
    approved: boolean;
    rationale?: string | null;
  }>;
};

export type SegmentationMap = {
  map_id: string;
  decomposition_run_id: string;
  schema_version: string;
  payload_hash: string;
  previous_hash: string | null;
  payload: Record<string, unknown>;
  null_hypothesis_accepted: boolean;
  created_at: string;
};

export type DecompositionRunDetail = {
  run_id: string;
  archive_root: string;
  archive_root_canonical_hash: string;
  status:
    | "running"
    | "completed"
    | "failed"
    | "paused_pre_layer4"
    | "paused_pre_layer5"
    | "paused_pre_layer6"
    | "paused_pre_layer7";
  triggered_at: string;
  completed_at: string | null;
  layer1_payload?: Record<string, unknown> | null;
  layer2_payload?: Record<string, unknown> | null;
  layer3_payload?: Record<string, unknown> | null;
  layer4_payload?: Record<string, unknown> | null;
  layer5_decision?: Record<string, unknown> | null;
  layer6_validation?: Record<string, unknown> | null;
  reformulated_predecessor_run_id?: string | null;
  reformulation_direction?: "finer" | "coarser" | null;
  reformulation_lineage_depth?: number | null;
  reformulation_count?: number | null;
};

// ---------- Chunk 42 — Permission Matrix telemetry payloads (D331/D333/D337) ----------

/**
 * Drift band label set — three-band kNN classification (D337).
 * Bands surface as label strings only (D120/D217: no numeric distance scores).
 */
export type DriftBand = "high" | "medium" | "low";

/**
 * Per-cluster operator decision verdict during ratification (D333).
 */
export type PermissionClusterDecisionKind =
  | "accept_cluster"
  | "reject_cluster"
  | "reassign_members"
  | "rename_cluster";

export type PermissionMatrixHypothesisGeneratedPayload = {
  run_id: string;
  cluster_count: number;
  has_null_hypothesis: boolean;
};

export type PermissionMatrixRatifiedPayload = {
  matrix_id: string;
  version_label: string | null;
  payload_hash: string;
  cluster_count: number;
};

export type PermissionClusterDecisionRecordedPayload = {
  matrix_id: string;
  cluster_id: string;
  decision_kind: PermissionClusterDecisionKind;
};

export type PermissionMatrixAutoAssignedPayload = {
  person_grace_id: string;
  cluster_id: string;
  drift_band: DriftBand;
};

// ---------- Chunk 42 — Permission Matrix API surface ----------

export type HypothesisConfidenceBand = "strong" | "moderate" | "weak";

export type PermissionMatrixVersion = {
  permission_matrix_id: string;
  payload: Record<string, unknown>;
  payload_hash: string;
  previous_hash: string | null;
  created_at: string;
  created_by: string | null;
  version_label: string | null;
};

export type PermissionMatrixListResponse = {
  versions: PermissionMatrixVersion[];
  active_payload_hash: string | null;
};

export type RoleClusterSummary = {
  cluster_id: string;
  display_name: string;
  member_grace_ids: string[];
  hypothesis_confidence_band: HypothesisConfidenceBand;
  sensitivity_tag?: string | null;
};

export type DriftQueueRow = {
  drift_queue_id: string;
  person_grace_id: string;
  proposed_cluster_id: string | null;
  drift_band: DriftBand;
  status: "pending" | "decided" | "deferred";
  rationale: string;
  auto_assigned: boolean;
  created_at: string;
};

// ---------- Chunk 43 — Sensitivity Gate Compliance Surface ----------
// Mirrors `src/permissions/models.py` + `src/api/sensitivity_routes.py`
// response shapes. `coverage_score` is intentionally absent — server
// strips it before serialization (D120/D217).

export type ComplianceFramework =
  | "iso_27001"
  | "soc_2"
  | "hipaa"
  | "gdpr"
  | "pci_dss"
  | "fhir"
  | "custom";

export type SensitivityCoverageBand = "high" | "medium" | "low";

export type SensitivityResourceKind =
  | "ontology_module"
  | "segment"
  | "change_directive"
  | "graph_entity"
  | "retrieval_query_event";

export type SensitivityAction = "view" | "edit" | "ratify";

export type SensitivityFrameworkMapping = {
  framework: ComplianceFramework;
  code: string;
};

export type SensitivityTag = {
  name: string;
  description?: string | null;
  framework_mappings: SensitivityFrameworkMapping[];
};

export type TaggedClusterDecision = {
  cluster_id: string;
  cluster_display_name: string;
  resource_kind: SensitivityResourceKind;
  resource_label: string;
  action: SensitivityAction;
  decision: "allow" | "deny";
  sensitivity_tags: SensitivityTag[];
};

export type TaggedSubset = {
  matrix_schema_version: string;
  cluster_decisions: TaggedClusterDecision[];
};

export type TagInventoryEntry = {
  tag_name: string;
  rule_count: number;
  cluster_count: number;
  framework_codes: SensitivityFrameworkMapping[];
};

export type CoverageBreakdownEntry = {
  resource_kind: SensitivityResourceKind;
  action: SensitivityAction;
  total_rule_count: number;
  tagged_rule_count: number;
};

export type UntaggedRuleEntry = {
  cluster_id: string;
  cluster_display_name: string;
  resource_kind: SensitivityResourceKind;
  resource_label: string;
  action: SensitivityAction;
};

export type TagHygieneFinding = {
  tag_name: string;
  similar_to: string;
  distance: number;
};

/**
 * Mirror of `SensitivityClassificationReport` minus `coverage_score`
 * (server-side only per D120/D217).
 */
export type SensitivityClassificationReportResponse = {
  report_id: string;
  permission_matrix_id: string;
  generated_at: string;
  tag_inventory: TagInventoryEntry[];
  coverage_breakdown: CoverageBreakdownEntry[];
  untagged_rules: UntaggedRuleEntry[];
  truncated: boolean;
  coverage_band: SensitivityCoverageBand | null;
  corpus_below_floor: boolean;
  tag_hygiene_findings: TagHygieneFinding[];
};

export type SensitivityReportListResponse = {
  reports: SensitivityClassificationReportResponse[];
  next_cursor: string | null;
};

export type SensitivityAuditTrailRow = {
  query_event_id: string;
  occurred_at: string;
  sensitivity_tags: string[];
};

export type SensitivityAuditTrailListResponse = {
  events: SensitivityAuditTrailRow[];
  next_cursor: string | null;
};

// ---------- Chunk 43 — telemetry payload shapes ----------

export type SensitivityReportGeneratedPayload = {
  report_id: string;
  matrix_id: string;
  coverage_band: SensitivityCoverageBand | null;
  tag_count: number;
  untagged_rule_count: number;
  corpus_below_floor: boolean;
};

export type SensitivityReportViewedPayload = {
  report_id: string;
  matrix_id: string;
};

export type SensitivityAuditTrailViewedPayload = {
  tag: string;
  matrix_id: string | null;
  result_count: number;
};

// ---------- Chunk 49 D394–D397 Earned Autonomy Calibration ----------

export type TrustIndicator = "high" | "building" | "insufficient";

export type CalibrationBand = {
  band_low: number;
  band_high: number;
  approval_rate: number;
  sample_count: number;
};

export type TierProgress = {
  total_decisions: number;
  min_reviews_for_calibration: number;
  progress_label: string;
};

export type TrustScoreState = {
  tier: number;
  trust_score: number;
  autonomy_threshold: number;
  autonomy_enabled: boolean;
  window_size: number;
  min_reviews_for_calibration: number;
  risk_tolerance: number;
  total_decisions: number;
  regression_detected: boolean;
  last_computed_at: string | null;
};

export type TierDashboard = {
  tier: number;
  bands: CalibrationBand[];
  trust_indicator: TrustIndicator;
  progress: TierProgress;
  trust_score_state: TrustScoreState;
};

export type CalibrationDashboardResponse = {
  tiers: TierDashboard[];
};

// ---------- Chunk 50 D398–D401 Agent Daemon types ----------

export type DaemonTierStatus = {
  tier: number;
  autonomy_enabled: boolean;
  regression_detected: boolean;
};

export type DaemonStatusResponse = {
  last_tick_at: string | null;
  proposals_in_cooling: number;
  kill_switch_engaged: boolean;
  tiers: DaemonTierStatus[];
  previous_state: Record<string, boolean> | null;
};

export type CoolingProposal = {
  id: string;
  proposal_type: string;
  change_tier: number;
  kgcl_command: string;
  status: string;
  cooling_period_expires_at: string | null;
  cooling_outcome: string | null;
};
