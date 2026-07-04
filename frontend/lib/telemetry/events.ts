"use client";

import type {
  ElicitationActorType,
  ElicitationEventEnvelope,
  ElicitationEventType,
  PhaseState,
} from "@/lib/api/types";
import { newSessionId } from "@/lib/ids/session-id";

const GRACE_VERSION =
  (typeof process !== "undefined" &&
    process.env?.NEXT_PUBLIC_GRACE_APP_VERSION) ||
  "0.27.0";

type BuildArgs = {
  session_id: string;
  phase_name: PhaseState;
  event_type: ElicitationEventType;
  payload: Record<string, unknown>;
  actor_type?: ElicitationActorType;
};

export function buildEnvelope(args: BuildArgs): ElicitationEventEnvelope {
  return {
    event_id: newSessionId(),
    event_type: args.event_type,
    session_id: args.session_id,
    actor_type: args.actor_type ?? "human",
    phase_name: args.phase_name,
    emitted_at: new Date().toISOString(),
    schema_version: 1,
    grace_version: GRACE_VERSION,
    payload: args.payload,
    payload_schema_version: 1,
  };
}

export const EventFactory = {
  sessionStarted(session_id: string, phase_name: PhaseState) {
    return buildEnvelope({
      session_id,
      phase_name,
      event_type: "session_started",
      payload: {
        plan_id: null,
        instrument_selected: null,
        rationale_string: null,
      },
    });
  },
  phaseEntered(session_id: string, phase_name: PhaseState) {
    return buildEnvelope({
      session_id,
      phase_name,
      event_type: "phase_entered",
      payload: {
        entered_phase: phase_name,
        entered_at: new Date().toISOString(),
      },
    });
  },
  phaseExited(
    session_id: string,
    phase_name: PhaseState,
    phase_duration_ms: number,
  ) {
    return buildEnvelope({
      session_id,
      phase_name,
      event_type: "phase_exited",
      payload: {
        exited_phase: phase_name,
        exited_at: new Date().toISOString(),
        phase_duration_ms,
        phase_signals_json: {},
      },
    });
  },
  sessionPaused(session_id: string, paused_from_phase: PhaseState) {
    return buildEnvelope({
      session_id,
      phase_name: paused_from_phase,
      event_type: "session_paused",
      payload: {
        paused_from_phase,
        paused_at: new Date().toISOString(),
      },
    });
  },
  sessionResumed(
    session_id: string,
    resumed_to_phase: PhaseState,
    paused_duration_ms: number,
  ) {
    return buildEnvelope({
      session_id,
      phase_name: resumed_to_phase,
      event_type: "session_resumed",
      payload: {
        resumed_to_phase,
        resumed_at: new Date().toISOString(),
        paused_duration_ms,
      },
    });
  },
  sessionClosed(
    session_id: string,
    args: {
      summary_edited: boolean;
      summary_rejected: boolean;
      session_duration_ms: number;
      phase_duration_distribution: Record<string, number>;
    },
  ) {
    return buildEnvelope({
      session_id,
      phase_name: "close",
      event_type: "session_closed",
      payload: args,
    });
  },
  closeReturnedToChat(
    session_id: string,
    args: {
      summary_discarded: boolean;
      session_duration_ms: number;
    },
  ) {
    return buildEnvelope({
      session_id,
      phase_name: "open",
      event_type: "close_returned_to_chat",
      payload: {
        prior_phase: "close",
        resumed_phase: "open",
        summary_discarded: args.summary_discarded,
        session_duration_ms: args.session_duration_ms,
      },
    });
  },
  protocolViolationDetected(
    session_id: string,
    phase_name: PhaseState,
    violation_type: string,
    details: Record<string, unknown> = {},
  ) {
    return buildEnvelope({
      session_id,
      phase_name,
      event_type: "protocol_violation_detected",
      payload: {
        violation_type,
        details,
      },
    });
  },

  // ---------- Chunk 28 D215 factories ----------

  graphViewerOpened(
    session_id: string,
    phase_name: PhaseState,
    args: { scope: string; entity_count_estimated: number | null },
  ) {
    return buildEnvelope({
      session_id,
      phase_name,
      event_type: "graph_viewer_opened",
      payload: {
        scope: args.scope,
        entity_count_estimated: args.entity_count_estimated,
      },
    });
  },

  graphNodeInspected(
    session_id: string,
    phase_name: PhaseState,
    args: { entity_type: string; grace_id_hash: string },
  ) {
    return buildEnvelope({
      session_id,
      phase_name,
      event_type: "graph_node_inspected",
      payload: {
        entity_type: args.entity_type,
        grace_id_hash: args.grace_id_hash,
      },
    });
  },

  graphEdgeInspected(
    session_id: string,
    phase_name: PhaseState,
    args: { relationship_type: string; grace_id_hash: string },
  ) {
    return buildEnvelope({
      session_id,
      phase_name,
      event_type: "graph_edge_inspected",
      payload: {
        relationship_type: args.relationship_type,
        grace_id_hash: args.grace_id_hash,
      },
    });
  },

  retrievalInspectorOpened(
    session_id: string,
    phase_name: PhaseState,
    args: { source: "chat_link" | "direct_nav" | "replay_button" },
  ) {
    return buildEnvelope({
      session_id,
      phase_name,
      event_type: "retrieval_inspector_opened",
      payload: { source: args.source },
    });
  },

  retrievalQueryReplayed(
    session_id: string,
    phase_name: PhaseState,
    args: { strategies_fired: string[]; latency_ms_total: number },
  ) {
    return buildEnvelope({
      session_id,
      phase_name,
      event_type: "retrieval_query_replayed",
      payload: {
        strategies_fired: args.strategies_fired,
        latency_ms_total: args.latency_ms_total,
      },
    });
  },

  // ---------- Chunk 29 D228 factories ----------

  structurePhaseEntered(
    session_id: string,
    args: { mode: string; mode_rationale: string },
  ) {
    return buildEnvelope({
      session_id,
      phase_name: "structure",
      event_type: "structure_phase_entered",
      payload: {
        entered_phase: "structure",
        entered_at: new Date().toISOString(),
        mode: args.mode,
        mode_rationale: args.mode_rationale,
      },
    });
  },

  clarifyPhaseEntered(
    session_id: string,
    args: { unresolved_decision_count: number },
  ) {
    return buildEnvelope({
      session_id,
      phase_name: "clarify",
      event_type: "clarify_phase_entered",
      payload: {
        entered_phase: "clarify",
        entered_at: new Date().toISOString(),
        unresolved_decision_count: args.unresolved_decision_count,
      },
    });
  },

  ladderingStepCompleted(
    session_id: string,
    phase_name: PhaseState,
    args: { step_index: number; parent_grace_id_hash: string; child_grace_id_hashes: string[]; step_duration_ms: number },
  ) {
    return buildEnvelope({
      session_id,
      phase_name,
      event_type: "laddering_step_completed",
      payload: args,
    });
  },

  cardSortCompleted(
    session_id: string,
    phase_name: PhaseState,
    args: { card_count: number; category_count: number; recategorization_count: number; duration_ms: number },
  ) {
    return buildEnvelope({
      session_id,
      phase_name,
      event_type: "card_sort_completed",
      payload: args,
    });
  },

  teachBackCompleted(
    session_id: string,
    phase_name: PhaseState,
    args: { item_index: number; sentence_count: number; correct_count: number; wrong_count: number; missing_something_count: number; correction_chars_total: number },
  ) {
    return buildEnvelope({
      session_id,
      phase_name,
      event_type: "teach_back_completed",
      payload: args,
    });
  },

  scopeSegmentChanged(
    session_id: string,
    phase_name: PhaseState,
    args: { prior_scope: string; new_scope: string; segment_count: number },
  ) {
    return buildEnvelope({
      session_id,
      phase_name,
      event_type: "scope_segment_changed",
      payload: args,
    });
  },

  cqAuthored(
    session_id: string,
    phase_name: PhaseState,
    args: { cq_id_hash: string; cq_type: string; domain: string; authoring_source: "from_scratch" | "from_candidate" },
  ) {
    return buildEnvelope({
      session_id,
      phase_name,
      event_type: "cq_authored",
      payload: args,
    });
  },

  cqCandidateAccepted(
    session_id: string,
    phase_name: PhaseState,
    args: { candidate_id_hash: string; source_origin: string; edited_before_accept: boolean },
  ) {
    return buildEnvelope({
      session_id,
      phase_name,
      event_type: "cq_candidate_accepted",
      payload: args,
    });
  },

  cqCandidateRejected(
    session_id: string,
    phase_name: PhaseState,
    args: { candidate_id_hash: string; source_origin: string; reject_reason_category: string },
  ) {
    return buildEnvelope({
      session_id,
      phase_name,
      event_type: "cq_candidate_rejected",
      payload: args,
    });
  },

  // ---------- Chunk 30 D234 factories ----------

  claimDispositionAccepted(
    session_id: string,
    phase_name: PhaseState,
    args: {
      claim_id_hash: string;
      reviewer_hash: string;
      was_modified: boolean;
      ontology_module: string;
    },
  ) {
    return buildEnvelope({
      session_id,
      phase_name,
      event_type: "claim_disposition_accepted",
      payload: args,
    });
  },

  claimDispositionRejected(
    session_id: string,
    phase_name: PhaseState,
    args: {
      claim_id_hash: string;
      reviewer_hash: string;
      ontology_module: string;
    },
  ) {
    return buildEnvelope({
      session_id,
      phase_name,
      event_type: "claim_disposition_rejected",
      payload: args,
    });
  },

  llmProviderSwitched(
    session_id: string,
    phase_name: PhaseState,
    args: {
      from_provider_id: string;
      to_provider_id: string;
      airgap_mode_after: boolean;
    },
  ) {
    return buildEnvelope({
      session_id,
      phase_name,
      event_type: "llm_provider_switched",
      payload: args,
    });
  },

  sourcesConfigured(
    session_id: string,
    phase_name: PhaseState,
    args: {
      file_count: number;
      total_size_mb: number;
      estimated_processing_minutes: number;
    },
  ) {
    return buildEnvelope({
      session_id,
      phase_name,
      event_type: "sources_configured",
      payload: args,
    });
  },

  airgapModeToggled(
    session_id: string,
    phase_name: PhaseState,
    args: { enabled: boolean },
  ) {
    return buildEnvelope({
      session_id,
      phase_name,
      event_type: "airgap_mode_toggled",
      payload: args,
    });
  },

  // ---------- Chunk 42 D331/D333/D337 factories ----------

  permissionMatrixHypothesisGenerated(
    session_id: string,
    args: {
      run_id: string;
      cluster_count: number;
      has_null_hypothesis: boolean;
    },
  ) {
    return buildEnvelope({
      session_id,
      phase_name: "none",
      event_type: "permission_matrix_hypothesis_generated",
      payload: args,
    });
  },

  permissionMatrixRatified(
    session_id: string,
    args: {
      matrix_id: string;
      version_label: string | null;
      payload_hash: string;
      cluster_count: number;
    },
  ) {
    return buildEnvelope({
      session_id,
      phase_name: "none",
      event_type: "permission_matrix_ratified",
      payload: args,
    });
  },

  permissionClusterDecisionRecorded(
    session_id: string,
    args: {
      matrix_id: string;
      cluster_id: string;
      decision_kind:
        | "accept_cluster"
        | "reject_cluster"
        | "reassign_members"
        | "rename_cluster";
    },
  ) {
    return buildEnvelope({
      session_id,
      phase_name: "none",
      event_type: "permission_cluster_decision_recorded",
      payload: args,
    });
  },

  permissionMatrixAutoAssigned(
    session_id: string,
    args: {
      person_grace_id: string;
      cluster_id: string;
      drift_band: "high" | "medium" | "low";
    },
  ) {
    return buildEnvelope({
      session_id,
      phase_name: "none",
      event_type: "permission_matrix_auto_assigned",
      payload: args,
    });
  },

  changeDirectiveDetailViewed(
    session_id: string,
    args: {
      directive_id: string;
      tier: "Operational_Adjustment" | "Strategic_Initiative";
      viewer_user_id: string;
    },
  ) {
    return buildEnvelope({
      session_id,
      phase_name: "none",
      event_type: "change_directive_detail_viewed",
      payload: {
        directive_id: args.directive_id,
        tier: args.tier,
        viewer_user_id: args.viewer_user_id,
        viewed_at: new Date().toISOString(),
      },
    });
  },

  // ---------- Chunk 43 sensitivity factories (CF1 lockstep) ----------

  sensitivityReportGenerated(
    session_id: string,
    args: {
      report_id: string;
      matrix_id: string;
      coverage_band: "high" | "medium" | "low" | null;
      tag_count: number;
      untagged_rule_count: number;
      corpus_below_floor: boolean;
    },
  ) {
    return buildEnvelope({
      session_id,
      phase_name: "none",
      event_type: "sensitivity_report_generated",
      payload: args,
    });
  },

  sensitivityReportViewed(
    session_id: string,
    args: { report_id: string; matrix_id: string },
  ) {
    return buildEnvelope({
      session_id,
      phase_name: "none",
      event_type: "sensitivity_report_viewed",
      payload: args,
    });
  },

  sensitivityAuditTrailViewed(
    session_id: string,
    args: { tag: string; matrix_id: string | null; result_count: number },
  ) {
    return buildEnvelope({
      session_id,
      phase_name: "none",
      event_type: "sensitivity_audit_trail_viewed",
      payload: args,
    });
  },

  // ---------- Chunk 47 D387/D389 proposal factories (CF1 lockstep) ----------

  proposalViewed(
    session_id: string,
    args: { proposal_id: string; change_tier: number },
  ) {
    return buildEnvelope({
      session_id,
      phase_name: "none",
      event_type: "proposal_viewed",
      payload: args,
    });
  },

  // ---------- Chunk 48 D392/D393 change executor factories (CF1 lockstep) ----------
  // proposal_executed is emitted by the backend (execute route), not by
  // the frontend. Factory listed here for CF1 lockstep completeness.

  // ---------- Chunk 45 D375 support session factories (CF1 lockstep) ----------

  supportBannerViewed(
    session_id: string,
    args: { session_email: string | null; expires_at: string | null },
  ) {
    return buildEnvelope({
      session_id,
      phase_name: "none",
      event_type: "support_banner_viewed",
      payload: args,
    });
  },
};

// ---------- Chunk 45 D375 support session event type constants ----------
// support_session_granted and support_session_revoked are emitted by
// the backend (support_routes.py), not by the frontend. Listed here
// for CF1 lockstep completeness.
export const SUPPORT_EVENT_TYPES = [
  "support_session_granted",
  "support_session_revoked",
  "support_banner_viewed",
] as const;

// ---------- Chunk 47 D387/D389 proposal event type constants ----------
// proposal_generated and proposal_decided are emitted by the backend
// (CLI generator + decide route), not by the frontend. Listed here
// for CF1 lockstep completeness.
export const PROPOSAL_EVENT_TYPES = [
  "proposal_generated",
  "proposal_decided",
  "proposal_viewed",
  // D392/D393 — Chunk 48 KGCL Change Executor telemetry (CF1 lockstep).
  "proposal_executed",
] as const;

// ---------- Chunk 48 D392/D393 change executor payload ----------
export type ProposalExecutedPayload = {
  proposal_id: string;
  tier: number;
  outcome: string;
};

// ---------- Chunk 49 D394–D397 Earned Autonomy Calibration payloads ----------
export type CalibrationDecisionRecordedPayload = {
  proposal_id: string;
  tier: number;
  decision: string;
};

export type CalibrationDashboardViewedPayload = {
  tiers_loaded: number;
};

export const CALIBRATION_EVENT_TYPES = [
  "calibration_decision_recorded",
  "calibration_dashboard_viewed",
] as const;

// ---------- Chunk 50 D398–D401 Agent Daemon payloads ----------
export type AgentTickStartedPayload = {
  agent_id: string;
  tick_sequence: number;
};

export type AgentTickCompletedPayload = {
  agent_id: string;
  tick_sequence: number;
  proposals_evaluated: number;
  proposals_applied: number;
  cooling_finalized: number;
};

export type AutonomousProposalAppliedPayload = {
  proposal_id: string;
  tier: number;
  agent_id: string;
};

export type CoolingPeriodFinalizedPayload = {
  proposal_id: string;
  outcome: string;
  agent_id: string;
};

export type KillSwitchEngagedPayload = {
  engaged_by: string;
  reason?: string;
};

export type KillSwitchDisengagedPayload = {
  disengaged_by: string;
  reason?: string;
};

// ---------- Chunk 51 D402–D405 Federation Infrastructure payload types ----------

export type FederationNamespaceRegisteredPayload = {
  namespace_id: string;
  namespace_type: string;
  label_prefix: string | null;
  database_name: string;
};

export type FederationEntityResolvedPayload = {
  canonical_grace_id: string | null;
  name: string;
  entity_type: string;
  resolution_method: string;
  namespace: string | null;
};

export const DAEMON_EVENT_TYPES = [
  "agent_tick_started",
  "agent_tick_completed",
  "autonomous_proposal_applied",
  "cooling_period_finalized",
  "kill_switch_engaged",
  "kill_switch_disengaged",
  // Chunk 51 (D402–D405) — Federation Infrastructure telemetry.
  "federation_namespace_registered",
  "federation_entity_resolved",
] as const;

// ---------- Chunk 60 — Phase 7 Communication Ingestion frontend payload types ----------

export type IngestionDashboardViewedPayload = {
  active_runs_count: number;
};

export type IngestionSourceDetailViewedPayload = {
  source_id: string;
};

export type ProfileBrowserViewedPayload = {
  profiles_visible_count: number;
};

export type ProfileDetailViewedPayload = {
  person_id: string;
};

export type CurationSubmittedPayload = {
  source_id: string;
  selected_count: number;
};

export type IngestionSettingsChangedPayload = {
  setting_key: "deployment_path" | "organization_domains" | "tier3_band";
};

export type ReconSourceFilterAppliedPayload = {
  filter_type: string;
  filter_value: string;
};

export const INGESTION_EVENT_TYPES = [
  "ingestion_dashboard_viewed",
  "ingestion_source_detail_viewed",
  "profile_browser_viewed",
  "profile_detail_viewed",
  "curation_submitted",
  "ingestion_settings_changed",
  "recon_source_filter_applied",
] as const;

// ---------- Chunk 44 D364/D365/D366/D367 MCP event type constants ----------
// These events are emitted by MCP write tools (backend-side), not by
// the frontend. Listed here for CF1 lockstep completeness.
export const MCP_EVENT_TYPES = [
  "mcp_session_started",
  "mcp_session_phase_advanced",
  "mcp_session_closed",
  "mcp_review_decided",
  "mcp_laddering_followup_emitted",
  "mcp_teachback_captured",
  "mcp_deep_link_generated",
] as const;

export const REQUIRED_ENVELOPE_FIELDS: ReadonlyArray<keyof ElicitationEventEnvelope> = [
  "event_id",
  "event_type",
  "session_id",
  "actor_type",
  "phase_name",
  "emitted_at",
  "schema_version",
  "grace_version",
  "payload",
  "payload_schema_version",
];
