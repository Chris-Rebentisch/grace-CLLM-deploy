"""D340 CF1 telemetry contract tests (Chunk 46, D378.b).

1 validation sweep (inner loop over 63 EventType literals) + 4 emit-site
integration tests for permission-matrix EventType literals + 3 edge-case
tests.  Total: 8 collected items.
"""

from __future__ import annotations

import typing
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest

from src.elicitation.models import (
    EventType,
    validate_payload_for_event_type,
)

_NOW = datetime.now(timezone.utc).isoformat()
_UUID = str(uuid4())

# Minimal valid payloads for every EventType literal.  Each value must pass
# validate_payload_for_event_type(key, value) without ValidationError.
_FACTORY: dict[str, dict] = {
    "session_started": {},
    "phase_entered": {"entered_phase": "prepare", "entered_at": _NOW},
    "phase_exited": {"exited_phase": "prepare", "exited_at": _NOW, "phase_duration_ms": 0},
    "session_paused": {"paused_from_phase": "prepare", "paused_at": _NOW},
    "session_resumed": {"resumed_to_phase": "prepare", "resumed_at": _NOW, "paused_duration_ms": 0},
    "session_closed": {"summary_edited": True, "summary_rejected": False, "session_duration_ms": 0},
    "close_returned_to_chat": {"prior_phase": "close", "resumed_phase": "open", "summary_discarded": True, "session_duration_ms": 1},
    "protocol_violation_detected": {"violation_type": "test"},
    "graph_viewer_opened": {"scope": "test"},
    "graph_node_inspected": {"entity_type": "test", "grace_id_hash": "test"},
    "graph_edge_inspected": {"relationship_type": "test", "grace_id_hash": "test"},
    "retrieval_inspector_opened": {"source": "chat_link"},
    "retrieval_query_replayed": {"strategies_fired": [], "latency_ms_total": 1.0},
    "structure_phase_entered": {"entered_phase": "structure", "entered_at": _NOW, "mode": "test", "mode_rationale": "test"},
    "clarify_phase_entered": {"entered_phase": "clarify", "entered_at": _NOW, "unresolved_decision_count": 1},
    "laddering_step_completed": {"step_index": 1, "parent_grace_id_hash": "test", "child_grace_id_hashes": [], "step_duration_ms": 1},
    "card_sort_completed": {"card_count": 1, "category_count": 1, "recategorization_count": 1, "duration_ms": 1},
    "teach_back_completed": {"item_index": 1, "sentence_count": 1, "correct_count": 1, "wrong_count": 1, "missing_something_count": 1, "correction_chars_total": 1},
    "scope_segment_changed": {"prior_scope": "test", "new_scope": "test", "segment_count": 1},
    "cq_authored": {"cq_id_hash": "test", "cq_type": "test", "domain": "test", "authoring_source": "from_scratch"},
    "cq_candidate_accepted": {"candidate_id_hash": "test", "source_origin": "local_documents", "edited_before_accept": True},
    "cq_candidate_rejected": {"candidate_id_hash": "test", "source_origin": "local_documents", "reject_reason_category": "test"},
    "claim_disposition_accepted": {"claim_id_hash": "test", "reviewer_hash": "test", "was_modified": True, "ontology_module": "test"},
    "claim_disposition_rejected": {"claim_id_hash": "test", "reviewer_hash": "test", "ontology_module": "test"},
    "llm_provider_switched": {"from_provider_id": "test", "to_provider_id": "test", "airgap_mode_after": True},
    "sources_configured": {"file_count": 0, "total_size_mb": 0.0, "estimated_processing_minutes": 0.0},
    "airgap_mode_toggled": {"enabled": True},
    "gap_report_generated": {"reviewer_hash": "test", "evidence_grounding_threshold": 1, "generated_at": _NOW},
    "gap_report_viewed": {"reviewer_hash": "test", "viewed_at": _NOW, "sections_expanded": []},
    "divergence_map_generated": {"reviewer_a_hash": "test", "reviewer_b_hash": "test", "additive_a_count": 1, "additive_b_count": 1, "contradictory_count": 1, "consensus_count": 1, "generated_at": _NOW},
    "divergence_map_viewed": {"reviewer_hash": "test", "divergence_map_id": "test", "viewed_at": _NOW},
    "documented_reality_report_generated": {"report_id": "test", "trigger": "scheduled", "corpus_below_floor": True, "generated_at": _NOW},
    "documented_reality_report_viewed": {"reviewer_hash": "test", "report_id": "test", "viewed_at": _NOW},
    "change_directive_created": {"directive_id": "test", "tier": "Operational_Adjustment", "visibility": "test", "created_at": _NOW},
    "change_directive_transitioned": {"directive_id": "test", "from_state": "test", "to_state": "test", "transitioned_at": _NOW},
    "change_directive_flagged_from_review": {"directive_id": "test", "flagged_from_session_id": "test", "created_at": _NOW},
    "change_directive_evidence_criterion_added": {"directive_id": "test", "criterion_id": "test", "compilation_status": "proposed", "has_compiled_query": True, "created_at": _NOW},
    "change_directive_metadata_edited": {"directive_id": _UUID, "editor_user_id": _UUID, "fields_changed": [], "before_values": {}, "after_values": {}, "edited_at": _NOW},
    "change_directive_detail_viewed": {"directive_id": _UUID, "tier": "Operational_Adjustment", "viewer_user_id": _UUID, "viewed_at": _NOW},
    "decomposition_run_started": {"run_id": _UUID, "archive_root_hash": "test", "started_at": _NOW},
    "decomposition_run_completed": {"run_id": _UUID, "archive_root_hash": "test", "total_documents": 0, "completed_at": _NOW},
    "decomposition_run_failed": {"run_id": _UUID, "archive_root_hash": "test", "error_summary": "test", "failed_at": _NOW},
    "decomposition_layer5_decision_recorded": {"run_id": _UUID, "decision_kind": "accepted_segmented", "modifications_count": 0, "rationale_length": 0},
    "decomposition_layer6_validation_recorded": {"run_id": _UUID, "segment_count": 0, "approved_count": 0, "rejected_count": 0},
    "segmentation_map_ratified": {"run_id": _UUID, "map_id": _UUID, "payload_hash": "test", "null_hypothesis_accepted": True},
    "decomposition_rerun_triggered": {"run_id": _UUID, "predecessor_run_id": _UUID, "direction": "finer", "lineage_depth": 1},
    "permission_matrix_hypothesis_generated": {"run_id": _UUID, "cluster_count": 0, "has_null_hypothesis": True},
    "permission_matrix_ratified": {"matrix_id": _UUID, "payload_hash": "test", "cluster_count": 0},
    "permission_cluster_decision_recorded": {"matrix_id": _UUID, "cluster_id": "test", "decision_kind": "accept_cluster"},
    "permission_matrix_auto_assigned": {"person_grace_id": "test", "cluster_id": "test", "drift_band": "high"},
    "sensitivity_report_generated": {"report_id": _UUID, "matrix_id": _UUID, "tag_count": 0, "untagged_rule_count": 0},
    "sensitivity_report_viewed": {"report_id": _UUID},
    "sensitivity_audit_trail_viewed": {"tag": "test", "result_count": 0},
    "mcp_session_started": {"session_id": "test"},
    "mcp_session_phase_advanced": {"session_id": "test", "target_phase": "test"},
    "mcp_session_closed": {"session_id": "test"},
    "mcp_review_decided": {"session_id": "test", "element_name": "test", "decision": "test"},
    "mcp_laddering_followup_emitted": {"session_id": "test", "element_name": "test", "question": "test"},
    "mcp_teachback_captured": {"session_id": "test", "element_name": "test", "narrative": "test"},
    "mcp_deep_link_generated": {"session_id": "test", "deep_link_url": "test"},
    "support_session_granted": {"session_id": "test", "granted_to_email": "test", "granted_at": _NOW},
    "support_session_revoked": {"session_id": "test", "revoked_at": _NOW},
    "support_banner_viewed": {},
    # D387/D389 — Chunk 47 Signal→Proposal pipeline entries (CF1 lockstep).
    "proposal_generated": {"proposal_id": "test", "signal_type": "A", "change_tier": 2, "ontology_module": "test"},
    "proposal_decided": {"proposal_id": "test", "decision": "approved", "reviewer_hash": "test"},
    "proposal_viewed": {"proposal_id": "test", "change_tier": 2},
    "proposal_executed": {"proposal_id": "test", "tier": 2, "outcome": "success"},
    # D394–D397 — Chunk 49 Earned Autonomy Calibration entries (CF1 lockstep).
    "calibration_decision_recorded": {"proposal_id": "test", "tier": 1, "decision": "approved"},
    "calibration_dashboard_viewed": {"tiers_loaded": 3},
    # D398–D401 — Chunk 50 Agent Daemon entries (CF1 lockstep).
    "agent_tick_started": {"agent_id": "test", "observation_time": _NOW},
    "agent_tick_completed": {"agent_id": "test", "proposals_evaluated": 0, "proposals_applied": 0, "suspended_tiers": [], "cooling_finalized": 0},
    "autonomous_proposal_applied": {"agent_id": "test", "proposal_id": "test", "tier": 1, "kgcl_command": "test", "outcome": "applied"},
    "cooling_period_finalized": {"proposal_id": "test", "outcome": "confirmed", "duration_hours": 48.0},
    "kill_switch_engaged": {"actor": "test", "all_tiers_disabled": True},
    "kill_switch_disengaged": {"actor": "test", "all_tiers_enabled": True},
    # D402–D405 — Chunk 51 Federation Infrastructure entries (CF1 lockstep).
    "federation_namespace_registered": {"namespace_id": "test", "namespace_type": "child", "label_prefix": None, "database_name": "test"},
    "federation_entity_resolved": {"canonical_grace_id": None, "name": "test", "entity_type": "test", "resolution_method": "exact", "namespace": None},
    # Chunk 60 — Communication Ingestion frontend entries (CF1 lockstep).
    "ingestion_dashboard_viewed": {"active_runs_count": 0},
    "ingestion_source_detail_viewed": {"source_id": _UUID},
    "profile_browser_viewed": {"profiles_visible_count": 0},
    "profile_detail_viewed": {"person_id": _UUID},
    "curation_submitted": {"source_id": _UUID, "selected_count": 1},
    "recon_source_filter_applied": {"filter_type": "test", "filter_value": "test"},
    "ingestion_settings_changed": {"setting_key": "deployment_path"},
}


# ── Test 1: CF1 validation sweep (1 collected item, inner loop) ──────────


def test_cf1_all_63_event_types_validate():
    """Every EventType literal has a valid factory payload — inner loop, NOT parametrize."""
    all_types = typing.get_args(EventType)
    assert len(all_types) == 84, f"Expected 84 EventType literals, got {len(all_types)}"
    missing = set(all_types) - set(_FACTORY)
    assert not missing, f"Factory missing entries: {missing}"
    errors: list[str] = []
    for et in all_types:
        try:
            validate_payload_for_event_type(et, _FACTORY[et])
        except Exception as exc:
            errors.append(f"{et}: {exc}")
    assert not errors, f"Validation failures:\n" + "\n".join(errors)


# ── Tests 2–5: emit-site integration for permission-matrix EventTypes ──


def test_emit_permission_matrix_hypothesis_generated():
    """permission_matrix_hypothesis_generated validates and would persist."""
    validate_payload_for_event_type(
        "permission_matrix_hypothesis_generated",
        {"run_id": str(uuid4()), "cluster_count": 3, "has_null_hypothesis": True},
    )


def test_emit_permission_matrix_ratified():
    """permission_matrix_ratified validates — same payload shape as ratify route."""
    validate_payload_for_event_type(
        "permission_matrix_ratified",
        {"matrix_id": str(uuid4()), "payload_hash": "abc123", "cluster_count": 2},
    )


def test_emit_permission_cluster_decision_recorded():
    """permission_cluster_decision_recorded — sub-event of ratify (N2 4:3)."""
    validate_payload_for_event_type(
        "permission_cluster_decision_recorded",
        {"matrix_id": str(uuid4()), "cluster_id": "ops-team", "decision_kind": "accept_cluster"},
    )


def test_emit_permission_matrix_auto_assigned():
    """permission_matrix_auto_assigned validates — drift auto-assignment."""
    validate_payload_for_event_type(
        "permission_matrix_auto_assigned",
        {"person_grace_id": "grace-id-1", "cluster_id": "eng", "drift_band": "high"},
    )


# ── Tests 6–8: edge-case / boundary tests ───────────────────────────────


def test_unknown_event_type_raises():
    """validate_payload_for_event_type rejects unknown event types."""
    with pytest.raises(Exception):
        validate_payload_for_event_type("nonexistent_event", {})


def test_invalid_payload_raises():
    """Valid event type with wrong payload shape raises ValidationError."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        validate_payload_for_event_type("phase_entered", {"wrong_field": 42})


def test_permission_cluster_decision_invalid_kind_raises():
    """decision_kind outside Literal raises."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "permission_cluster_decision_recorded",
            {"matrix_id": str(uuid4()), "cluster_id": "x", "decision_kind": "bogus"},
        )
