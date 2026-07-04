"""Tests for Pydantic envelope validation (Chunk 27, protocol §8.2)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.elicitation.models import (
    ElicitationEventEnvelope,
    validate_payload_for_event_type,
)


def _base_envelope(**overrides):
    base = {
        "event_id": str(uuid4()),
        "event_type": "session_started",
        "session_id": str(uuid4()),
        "actor_type": "human",
        "phase_name": "open",
        "emitted_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": 1,
        "grace_version": "0.27.0",
        "payload": {},
        "payload_schema_version": 1,
    }
    base.update(overrides)
    return base


def test_envelope_accepts_valid_inputs_and_rejects_missing_required_fields():
    envelope = ElicitationEventEnvelope.model_validate(_base_envelope())
    assert envelope.actor_type == "human"

    with pytest.raises(ValidationError) as excinfo:
        ElicitationEventEnvelope.model_validate(
            {**_base_envelope(), "grace_version": ""}
        )
    errors = excinfo.value.errors()
    assert any("grace_version" in (e.get("loc", ()) or ()) for e in errors)

    missing = _base_envelope()
    missing.pop("event_id")
    with pytest.raises(ValidationError):
        ElicitationEventEnvelope.model_validate(missing)


def test_session_resumed_payload_rejects_cooldown_penalty_decay_fields_ec5_audit():
    bad_payloads = [
        {
            "resumed_to_phase": "open",
            "resumed_at": datetime.now(timezone.utc).isoformat(),
            "paused_duration_ms": 100,
            "cooldown": 5,
        },
        {
            "resumed_to_phase": "open",
            "resumed_at": datetime.now(timezone.utc).isoformat(),
            "paused_duration_ms": 100,
            "penalty": 0.5,
        },
        {
            "resumed_to_phase": "open",
            "resumed_at": datetime.now(timezone.utc).isoformat(),
            "paused_duration_ms": 100,
            "decay": 0.1,
        },
    ]
    for bad in bad_payloads:
        with pytest.raises(ValidationError):
            validate_payload_for_event_type("session_resumed", bad)

    good = {
        "resumed_to_phase": "open",
        "resumed_at": datetime.now(timezone.utc).isoformat(),
        "paused_duration_ms": 100,
    }
    model = validate_payload_for_event_type("session_resumed", good)
    assert model.model_dump()["paused_duration_ms"] == 100


# ---------- Chunk 28 D215 payloads (viewer + inspector surfaces) ----------

_HASH_HEX = "a" * 64  # 64-char placeholder hex SHA-256


def test_graph_viewer_opened_round_trip_valid():
    payload = {"scope": "all", "entity_count_estimated": 42}
    model = validate_payload_for_event_type("graph_viewer_opened", payload)
    assert model.model_dump() == payload

    # entity_count_estimated is optional / nullable
    model2 = validate_payload_for_event_type(
        "graph_viewer_opened", {"scope": "all"}
    )
    assert model2.model_dump()["entity_count_estimated"] is None


def test_graph_viewer_opened_rejects_extra_field():
    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "graph_viewer_opened",
            {"scope": "all", "entity_count_estimated": 5, "foo": "bar"},
        )

    # session_id/phase_name duplication is also rejected per D215 envelope discipline
    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "graph_viewer_opened",
            {"scope": "all", "session_id": str(uuid4())},
        )


def test_graph_node_inspected_round_trip_valid():
    payload = {"entity_type": "Legal_Entity", "grace_id_hash": _HASH_HEX}
    model = validate_payload_for_event_type("graph_node_inspected", payload)
    assert model.model_dump() == payload


def test_graph_node_inspected_rejects_extra_field():
    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "graph_node_inspected",
            {
                "entity_type": "Legal_Entity",
                "grace_id_hash": _HASH_HEX,
                "grace_id": "raw-id-MUST-NOT-BE-ACCEPTED",
            },
        )


def test_graph_edge_inspected_round_trip_valid():
    payload = {
        "relationship_type": "owns",
        "grace_id_hash": _HASH_HEX,
    }
    model = validate_payload_for_event_type("graph_edge_inspected", payload)
    assert model.model_dump() == payload


def test_graph_edge_inspected_rejects_extra_field():
    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "graph_edge_inspected",
            {
                "relationship_type": "owns",
                "grace_id_hash": _HASH_HEX,
                "source_grace_id": "should-not-leak-to-payload",
            },
        )


def test_retrieval_inspector_opened_round_trip_valid():
    for source in ("chat_link", "direct_nav", "replay_button"):
        payload = {"source": source}
        model = validate_payload_for_event_type(
            "retrieval_inspector_opened", payload
        )
        assert model.model_dump() == payload


def test_retrieval_inspector_opened_rejects_extra_field():
    # Unknown source literal
    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "retrieval_inspector_opened", {"source": "unknown_channel"}
        )
    # Unknown field
    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "retrieval_inspector_opened",
            {"source": "chat_link", "referrer": "https://example.com"},
        )


def test_retrieval_query_replayed_round_trip_valid():
    payload = {
        "strategies_fired": ["graph", "semantic", "bm25"],
        "latency_ms_total": 412.7,
    }
    model = validate_payload_for_event_type(
        "retrieval_query_replayed", payload
    )
    # D267 (Chunk 35b): replay_differed and original_query_event_id added
    # to the payload with defaults for backward compatibility. model_dump()
    # surfaces the defaults.
    assert model.model_dump() == {
        **payload,
        "replay_differed": False,
        "original_query_event_id": None,
    }


def test_retrieval_query_replayed_rejects_extra_field():
    # D267 (Chunk 35b) reintroduces `replay_differed` and adds
    # `original_query_event_id` to RetrievalQueryReplayedPayload with defaults.
    # `extra="forbid"` still rejects truly unknown fields.
    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "retrieval_query_replayed",
            {
                "strategies_fired": ["graph"],
                "latency_ms_total": 100.0,
                "unknown_field": "should_be_rejected",
            },
        )


# ---------- Chunk 29 D228 payloads (Structure / Clarify phases) ----------


def test_structure_decision_payload_round_trip():
    payload = {
        "evidence_items_viewed": [_HASH_HEX],
        "evidence_items_available": [_HASH_HEX, "b" * 64],
        "declared_certainty_band": "high",
    }
    model = validate_payload_for_event_type("structure_phase_entered", {
        "entered_phase": "structure",
        "entered_at": datetime.now(timezone.utc).isoformat(),
        "mode": "guided",
        "mode_rationale": "Standard guided review",
    })
    assert model.model_dump()["entered_phase"] == "structure"


def test_structure_decision_payload_rejects_extra_fields():
    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "structure_phase_entered",
            {
                "entered_phase": "structure",
                "entered_at": datetime.now(timezone.utc).isoformat(),
                "mode": "guided",
                "mode_rationale": "test",
                "forbidden_extra": "should fail",
            },
        )


def test_clarify_decision_payload_round_trip():
    payload = {
        "decision_id_hash": _HASH_HEX,
        "position_changed": True,
        "prior_decision_id": "b" * 64,
        "clarify_duration_ms": 5000,
    }
    model = validate_payload_for_event_type("clarify_phase_entered", {
        "entered_phase": "clarify",
        "entered_at": datetime.now(timezone.utc).isoformat(),
        "unresolved_decision_count": 3,
    })
    assert model.model_dump()["unresolved_decision_count"] == 3


def test_clarify_decision_payload_rejects_extra_fields():
    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "clarify_phase_entered",
            {
                "entered_phase": "clarify",
                "entered_at": datetime.now(timezone.utc).isoformat(),
                "unresolved_decision_count": 3,
                "secret_data": "should fail",
            },
        )


def test_cf2_unknown_event_type_raises_validation_error():
    """CF2 bugfix: unknown event_type must raise ValidationError, not TypeError."""
    with pytest.raises(ValidationError):
        validate_payload_for_event_type("nonexistent_type", {})


# ---------- Chunk 30 D234 payloads (claim review + LLM config + sources) ----------


@pytest.mark.parametrize(
    "event_type,good_payload,bad_extra",
    [
        (
            "claim_disposition_accepted",
            {
                "claim_id_hash": _HASH_HEX,
                "reviewer_hash": _HASH_HEX,
                "was_modified": False,
                "ontology_module": "core",
            },
            {"reviewer": "alice"},
        ),
        (
            "claim_disposition_rejected",
            {
                "claim_id_hash": _HASH_HEX,
                "reviewer_hash": _HASH_HEX,
                "ontology_module": "core",
            },
            {"reason": "free-text-not-allowed"},
        ),
        (
            "llm_provider_switched",
            {
                "from_provider_id": "ollama",
                "to_provider_id": "anthropic",
                "airgap_mode_after": False,
            },
            {"api_key": "sk-not-allowed"},
        ),
        (
            "sources_configured",
            {
                "file_count": 7,
                "total_size_mb": 12.5,
                "estimated_processing_minutes": 4.2,
            },
            {"filenames": ["a.pdf"]},
        ),
        (
            "airgap_mode_toggled",
            {"enabled": True},
            {"toggled_by": "alice"},
        ),
    ],
)
def test_d234_payload_round_trip_and_extra_field_rejection(event_type, good_payload, bad_extra):
    model = validate_payload_for_event_type(event_type, good_payload)
    assert model.model_dump() == good_payload

    with pytest.raises(ValidationError):
        validate_payload_for_event_type(event_type, {**good_payload, **bad_extra})


# ---------- Chunk 37 D290 payloads (Reconciliation cross-executive) ----------

_DR_DT = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "event_type,good_payload,bad_extra",
    [
        (
            "divergence_map_generated",
            {
                "reviewer_a_hash": _HASH_HEX,
                "reviewer_b_hash": _HASH_HEX,
                "segment_id": "seg37",
                "additive_a_count": 2,
                "additive_b_count": 3,
                "contradictory_count": 1,
                "consensus_count": 5,
                "generated_at": _DR_DT,
            },
            {"reviewer_a": "alice"},
        ),
        (
            "divergence_map_viewed",
            {
                "reviewer_hash": _HASH_HEX,
                "divergence_map_id": "map-1",
                "viewed_at": _DR_DT,
            },
            {"reviewer": "alice"},
        ),
        (
            "documented_reality_report_generated",
            {
                "report_id": "rpt-1",
                "trigger": "on_demand",
                "corpus_below_floor": False,
                "generated_at": _DR_DT,
            },
            {"narrative": "leaked"},
        ),
        (
            "documented_reality_report_viewed",
            {
                "reviewer_hash": _HASH_HEX,
                "report_id": "rpt-1",
                "viewed_at": _DR_DT,
            },
            {"reviewer": "alice"},
        ),
    ],
)
def test_d290_payload_round_trip_and_extra_field_rejection(event_type, good_payload, bad_extra):
    model = validate_payload_for_event_type(event_type, good_payload)
    dumped = model.model_dump()
    # datetime round-trip is value-equal; compare via model_dump → dict comparison.
    assert dumped == good_payload

    with pytest.raises(ValidationError):
        validate_payload_for_event_type(event_type, {**good_payload, **bad_extra})


# --- Chunk 40 / D318 — decomposition pipeline lifecycle events --------------

_DECOMP_RUN_ID = uuid4()
_DECOMP_HASH = "a" * 64
_DECOMP_NOW = datetime.now(timezone.utc).isoformat()


def test_d318_decomposition_run_started_payload_validates_and_rejects_extra_fields():
    good = {
        "run_id": str(_DECOMP_RUN_ID),
        "archive_root_hash": _DECOMP_HASH,
        "started_at": _DECOMP_NOW,
    }
    model = validate_payload_for_event_type("decomposition_run_started", good)
    dumped = model.model_dump(mode="json")
    assert dumped["run_id"] == good["run_id"]
    assert dumped["archive_root_hash"] == good["archive_root_hash"]

    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "decomposition_run_started", {**good, "unexpected_field": "nope"}
        )


def test_d318_decomposition_run_completed_payload_requires_total_documents_non_negative():
    good = {
        "run_id": str(_DECOMP_RUN_ID),
        "archive_root_hash": _DECOMP_HASH,
        "total_documents": 12,
        "completed_at": _DECOMP_NOW,
    }
    model = validate_payload_for_event_type("decomposition_run_completed", good)
    assert model.total_documents == 12

    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "decomposition_run_completed", {**good, "total_documents": -1}
        )


def test_d318_decomposition_run_failed_payload_round_trips_through_envelope():
    good = {
        "run_id": str(_DECOMP_RUN_ID),
        "archive_root_hash": _DECOMP_HASH,
        "error_summary": "synthesize_hypotheses raised ValidationError",
        "failed_at": _DECOMP_NOW,
    }
    envelope = ElicitationEventEnvelope.model_validate(
        _base_envelope(
            event_type="decomposition_run_failed",
            payload=good,
            phase_name="close",
        )
    )
    assert envelope.event_type == "decomposition_run_failed"
    # Cross-validate the payload via the registry helper.
    payload_model = validate_payload_for_event_type(
        "decomposition_run_failed", good
    )
    assert payload_model.error_summary.startswith("synthesize_hypotheses")


# ---------- D330: Chunk 41 decomposition Layer 5/6/7 + re-run telemetry ----------


_MAP_ID = uuid4()


def test_d330_decomposition_layer5_decision_recorded_payload():
    good = {
        "run_id": str(_DECOMP_RUN_ID),
        "decision_kind": "accepted_null",
        "modifications_count": 0,
        "rationale_length": 12,
    }
    model = validate_payload_for_event_type(
        "decomposition_layer5_decision_recorded", good
    )
    assert model.decision_kind == "accepted_null"
    assert model.modifications_count == 0

    # Unknown decision_kind rejected by Literal.
    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "decomposition_layer5_decision_recorded",
            {**good, "decision_kind": "bogus"},
        )

    # Negative counts rejected.
    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "decomposition_layer5_decision_recorded",
            {**good, "modifications_count": -1},
        )

    # extra='forbid'.
    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "decomposition_layer5_decision_recorded",
            {**good, "extra_field": "no"},
        )


def test_d330_decomposition_layer6_validation_recorded_payload():
    good = {
        "run_id": str(_DECOMP_RUN_ID),
        "segment_count": 2,
        "approved_count": 8,
        "rejected_count": 2,
    }
    model = validate_payload_for_event_type(
        "decomposition_layer6_validation_recorded", good
    )
    assert model.segment_count == 2
    assert model.approved_count == 8

    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "decomposition_layer6_validation_recorded",
            {**good, "approved_count": -1},
        )

    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "decomposition_layer6_validation_recorded",
            {**good, "weird": True},
        )


def test_d330_segmentation_map_ratified_payload():
    good = {
        "run_id": str(_DECOMP_RUN_ID),
        "map_id": str(_MAP_ID),
        "payload_hash": "h" * 64,
        "previous_hash": None,
        "null_hypothesis_accepted": False,
    }
    model = validate_payload_for_event_type(
        "segmentation_map_ratified", good
    )
    assert model.payload_hash == "h" * 64
    assert model.null_hypothesis_accepted is False

    # Empty payload_hash rejected.
    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "segmentation_map_ratified", {**good, "payload_hash": ""}
        )

    # extra='forbid'.
    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "segmentation_map_ratified", {**good, "rogue": 1}
        )


def test_d330_decomposition_rerun_triggered_payload():
    pred = uuid4()
    good = {
        "run_id": str(_DECOMP_RUN_ID),
        "predecessor_run_id": str(pred),
        "direction": "finer",
        "lineage_depth": 1,
        "resolution_target": 1.5,
    }
    model = validate_payload_for_event_type(
        "decomposition_rerun_triggered", good
    )
    assert model.direction == "finer"
    assert model.lineage_depth == 1

    # Direction must be finer or coarser.
    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "decomposition_rerun_triggered",
            {**good, "direction": "sideways"},
        )

    # lineage_depth >= 1.
    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "decomposition_rerun_triggered",
            {**good, "lineage_depth": 0},
        )

    # extra='forbid'.
    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "decomposition_rerun_triggered",
            {**good, "rogue": 1},
        )



# ---------- Chunk 42 — Permission Matrix telemetry payloads (D331/D333/D337) ----------


def test_permission_matrix_hypothesis_generated_payload_validates_and_forbids_extras():
    good = {
        "run_id": str(uuid4()),
        "cluster_count": 3,
        "has_null_hypothesis": True,
    }
    model = validate_payload_for_event_type(
        "permission_matrix_hypothesis_generated", good
    )
    assert model.cluster_count == 3
    assert model.has_null_hypothesis is True

    # cluster_count >= 0.
    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "permission_matrix_hypothesis_generated",
            {**good, "cluster_count": -1},
        )

    # extra='forbid'.
    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "permission_matrix_hypothesis_generated",
            {**good, "rogue": 1},
        )


def test_permission_matrix_ratified_and_cluster_decision_payloads_share_4_to_3_mapping():
    """N2 counter-event 4:3 mapping. Both EventTypes carry distinct
    payload shapes but increment the same Prometheus counter
    (`grace_permission_matrix_ratifications_total`)."""
    matrix_id = str(uuid4())

    ratified_good = {
        "matrix_id": matrix_id,
        "version_label": "v1.0",
        "payload_hash": "abc123",
        "cluster_count": 5,
    }
    ratified = validate_payload_for_event_type(
        "permission_matrix_ratified", ratified_good
    )
    assert ratified.payload_hash == "abc123"

    # version_label is optional; payload_hash is required and non-empty.
    validate_payload_for_event_type(
        "permission_matrix_ratified",
        {**ratified_good, "version_label": None},
    )
    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "permission_matrix_ratified",
            {**ratified_good, "payload_hash": ""},
        )

    # Per-cluster decision payload.
    decision_good = {
        "matrix_id": matrix_id,
        "cluster_id": "cluster-a",
        "decision_kind": "accept_cluster",
    }
    decision = validate_payload_for_event_type(
        "permission_cluster_decision_recorded", decision_good
    )
    assert decision.decision_kind == "accept_cluster"

    # decision_kind is a closed Literal — unknown verdicts rejected.
    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "permission_cluster_decision_recorded",
            {**decision_good, "decision_kind": "yolo"},
        )

    # Empty cluster_id rejected.
    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "permission_cluster_decision_recorded",
            {**decision_good, "cluster_id": ""},
        )


def test_permission_matrix_auto_assigned_payload_pins_three_band_literal():
    good = {
        "person_grace_id": "person-1",
        "cluster_id": "cluster-7",
        "drift_band": "high",
    }
    model = validate_payload_for_event_type(
        "permission_matrix_auto_assigned", good
    )
    assert model.drift_band == "high"

    # Three-band Literal pinned (D120/D217 — bands only, no numerics).
    for band in ("medium", "low"):
        validate_payload_for_event_type(
            "permission_matrix_auto_assigned",
            {**good, "drift_band": band},
        )
    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "permission_matrix_auto_assigned",
            {**good, "drift_band": "extreme"},
        )

    # extra='forbid'.
    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "permission_matrix_auto_assigned",
            {**good, "rogue": 1},
        )


# ---------- Chunk 60 Phase 7 Communication Ingestion frontend payloads ----------


_CHUNK_60_ROUND_TRIPS = [
    ("ingestion_dashboard_viewed", {"active_runs_count": 3}),
    ("ingestion_source_detail_viewed", {"source_id": str(uuid4())}),
    ("profile_browser_viewed", {"profiles_visible_count": 10}),
    ("profile_detail_viewed", {"person_id": str(uuid4())}),
    ("curation_submitted", {"source_id": str(uuid4()), "selected_count": 5}),
    ("ingestion_settings_changed", {"setting_key": "deployment_path"}),
    ("recon_source_filter_applied", {"filter_type": "source_type", "filter_value": "communication"}),
]


@pytest.mark.parametrize("event_type,payload", _CHUNK_60_ROUND_TRIPS)
def test_chunk60_payload_round_trip(event_type, payload):
    """Chunk 60 Phase 7 payloads validate and round-trip."""
    model = validate_payload_for_event_type(event_type, payload)
    dumped = model.model_dump()
    for k, v in payload.items():
        assert str(dumped[k]) == str(v)


@pytest.mark.parametrize("event_type,payload", _CHUNK_60_ROUND_TRIPS)
def test_chunk60_payload_rejects_extra(event_type, payload):
    """Chunk 60 Phase 7 payloads reject extra fields (extra='forbid')."""
    with pytest.raises(ValidationError):
        validate_payload_for_event_type(event_type, {**payload, "rogue_field": 1})


def test_chunk60_curation_submitted_requires_positive_count():
    """curation_submitted requires selected_count >= 1."""
    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "curation_submitted",
            {"source_id": str(uuid4()), "selected_count": 0},
        )


def test_chunk60_ingestion_settings_changed_validates_setting_key():
    """ingestion_settings_changed rejects invalid setting_key."""
    with pytest.raises(ValidationError):
        validate_payload_for_event_type(
            "ingestion_settings_changed",
            {"setting_key": "invalid_key"},
        )
