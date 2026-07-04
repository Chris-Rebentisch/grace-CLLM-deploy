"use client";

import { onTelemetry, type TelemetryEvent } from "./bus";
import { buildEnvelope } from "./events";
import { postElicitationEvent } from "./emit";
import { useSessionStore } from "@/lib/state/session-store";
import type { ElicitationEventType, PhaseState } from "@/lib/api/types";

// The protocol event types that land in elicitation_events (8 from
// Chunk 27 + … + Chunk 37 + Chunk 38 + Chunk 39 detail/metadata).
// Missing any entry here causes silent drops at the filter below.
const ELICITATION_EVENT_TYPES: ReadonlySet<string> = new Set<
  ElicitationEventType
>([
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
  // D234 — Chunk 30 catalog extension.
  "claim_disposition_accepted",
  "claim_disposition_rejected",
  "llm_provider_switched",
  "sources_configured",
  "airgap_mode_toggled",
  // D282 — Chunk 36 catalog extension (Reconciliation Layer foundation).
  "gap_report_generated",
  "gap_report_viewed",
  // D290 — Chunk 37 catalog extension (Reconciliation cross-executive).
  "divergence_map_generated",
  "divergence_map_viewed",
  "documented_reality_report_generated",
  "documented_reality_report_viewed",
  // D298 — Chunk 38 catalog extension (Change_Directives foundation).
  "change_directive_created",
  "change_directive_transitioned",
  "change_directive_flagged_from_review",
  "change_directive_evidence_criterion_added",
  // D307/D308 — Chunk 39 (bridge must list full Python EventType union for CF1).
  "change_directive_metadata_edited",
  "change_directive_detail_viewed",
  // D318 — Chunk 40 decomposition pipeline lifecycle (CLI-only; CF1 sync).
  "decomposition_run_started",
  "decomposition_run_completed",
  "decomposition_run_failed",
  // D330 — Chunk 41 decomposition Layer 5/6/7 + rerun (CF1 lockstep).
  "decomposition_layer5_decision_recorded",
  "decomposition_layer6_validation_recorded",
  "segmentation_map_ratified",
  "decomposition_rerun_triggered",
  // D331/D333/D337 — Chunk 42 Permission Matrix telemetry (CF1 lockstep).
  "permission_matrix_hypothesis_generated",
  "permission_matrix_ratified",
  "permission_cluster_decision_recorded",
  "permission_matrix_auto_assigned",
  // Chunk 43 — Sensitivity Gate Compliance Surface (CF1 lockstep).
  "sensitivity_report_generated",
  "sensitivity_report_viewed",
  "sensitivity_audit_trail_viewed",
  // D364/D365/D366/D367 — Chunk 44 MCP write-tool telemetry (CF1 lockstep).
  "mcp_session_started",
  "mcp_session_phase_advanced",
  "mcp_session_closed",
  "mcp_review_decided",
  "mcp_laddering_followup_emitted",
  "mcp_teachback_captured",
  "mcp_deep_link_generated",
  // D375 — Chunk 45 Remote Support Session telemetry (CF1 lockstep).
  "support_session_granted",
  "support_session_revoked",
  "support_banner_viewed",
  // D387/D389 — Chunk 47 Signal→Proposal pipeline telemetry (CF1 lockstep).
  "proposal_generated",
  "proposal_decided",
  "proposal_viewed",
  // D392/D393 — Chunk 48 KGCL Change Executor telemetry (CF1 lockstep).
  "proposal_executed",
  // D394–D397 — Chunk 49 Earned Autonomy Calibration telemetry (CF1 lockstep).
  "calibration_decision_recorded",
  "calibration_dashboard_viewed",
  // D398–D401 — Chunk 50 Agent Daemon (CF1 lockstep).
  "agent_tick_started",
  "agent_tick_completed",
  "autonomous_proposal_applied",
  "cooling_period_finalized",
  "kill_switch_engaged",
  "kill_switch_disengaged",
  // Chunk 51 (D402–D405) — Federation Infrastructure telemetry.
  "federation_namespace_registered",
  "federation_entity_resolved",
  // Chunk 60 — Phase 7 Communication Ingestion frontend surfaces (CF1 lockstep).
  "ingestion_dashboard_viewed",
  "ingestion_source_detail_viewed",
  "profile_browser_viewed",
  "profile_detail_viewed",
  "curation_submitted",
  "ingestion_settings_changed",
  "recon_source_filter_applied",
]);

function isElicitationEventType(t: string): t is ElicitationEventType {
  return ELICITATION_EVENT_TYPES.has(t);
}

// Observation 5 ratification (2026-04-23): the envelope `phase_name`
// column must describe the event's subject phase, not the session's
// post-transition active phase. For events that ARE phase transitions
// we read the authoritative phase from the payload; for non-transition
// events we fall back to activePhase.
function derivePhaseName(
  event: TelemetryEvent,
  activePhase: PhaseState,
): PhaseState {
  const payload = event.payload ?? {};
  const read = (key: string): PhaseState | null => {
    const v = (payload as Record<string, unknown>)[key];
    return typeof v === "string" ? (v as PhaseState) : null;
  };
  switch (event.type) {
    case "phase_entered":
      return read("entered_phase") ?? activePhase;
    case "phase_exited":
      return read("exited_phase") ?? activePhase;
    case "session_paused":
      return read("paused_from_phase") ?? activePhase;
    case "session_resumed":
      return read("resumed_to_phase") ?? activePhase;
    case "close_returned_to_chat":
      return read("prior_phase") ?? activePhase;
    case "structure_phase_entered":
      return read("entered_phase") ?? activePhase;
    case "clarify_phase_entered":
      return read("entered_phase") ?? activePhase;
    default:
      return activePhase;
  }
}

// Forwards local bus events to the backend ingest endpoint. Events fired
// before the session store has a sessionId are dropped (the backend would
// 422 them anyway). Fire-and-forget — transport errors are logged inside
// postElicitationEvent.
export function startTelemetryBridge(): () => void {
  return onTelemetry((event: TelemetryEvent) => {
    if (!isElicitationEventType(event.type)) return;

    const state = useSessionStore.getState();
    const sessionId = state.sessionId;
    if (!sessionId) return;

    const envelope = buildEnvelope({
      session_id: sessionId,
      phase_name: derivePhaseName(event, state.activePhase),
      event_type: event.type,
      payload: event.payload ?? {},
    });
    void postElicitationEvent(envelope);
  });
}
