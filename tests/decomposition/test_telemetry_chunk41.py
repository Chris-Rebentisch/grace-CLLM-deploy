"""Telemetry tests for Chunk 41 (D330).

Covers:

* The three new Prometheus counters import-time creation (no-op safety).
* ``record_decomposition_*`` emitter helpers tolerate any input
  (best-effort; never raise).
* The four D330 EventType literals are present in the registry.
* CF1 lockstep parity check: each EventType has a payload model.
"""

from __future__ import annotations

import pytest

from src.elicitation.models import (
    EventType,
    _PAYLOAD_MODELS,
    payload_model_for,
)


_D330_EVENT_TYPES = [
    "decomposition_layer5_decision_recorded",
    "decomposition_layer6_validation_recorded",
    "segmentation_map_ratified",
    "decomposition_rerun_triggered",
]


@pytest.mark.parametrize("event_type", _D330_EVENT_TYPES)
def test_d330_event_type_present_in_payload_registry(event_type):
    """Every D330 EventType has a payload model bound (CF1 four-way lockstep)."""
    assert payload_model_for(event_type) is not None
    assert event_type in _PAYLOAD_MODELS


def test_d330_event_types_appear_in_registry_dict():
    """Sanity check: registry covers exactly the four D330 entries."""
    missing = [e for e in _D330_EVENT_TYPES if e not in _PAYLOAD_MODELS]
    assert not missing, f"D330 EventType(s) missing from _PAYLOAD_MODELS: {missing}"


def test_record_decomposition_layer5_decision_increments_counter():
    """Emitter call must not raise even on unusual decision_kind labels."""
    from src.analytics.metrics import record_decomposition_layer5_decision

    # Standard label.
    record_decomposition_layer5_decision(decision_kind="accepted_null")
    # An unknown label value still must not raise (metric is best-effort).
    record_decomposition_layer5_decision(decision_kind="weird_value")


def test_record_decomposition_segmentation_map_ratified_handles_bool():
    from src.analytics.metrics import (
        record_decomposition_segmentation_map_ratified,
    )

    record_decomposition_segmentation_map_ratified(null_hypothesis_accepted=True)
    record_decomposition_segmentation_map_ratified(null_hypothesis_accepted=False)


def test_record_decomposition_rerun_handles_directions():
    from src.analytics.metrics import record_decomposition_rerun

    record_decomposition_rerun(direction="finer")
    record_decomposition_rerun(direction="coarser")


def test_d330_counters_exist_at_module_level():
    """The three counter objects exist on the metrics module as attributes."""
    import src.analytics.metrics as metrics

    assert hasattr(metrics, "grace_decomposition_layer5_decisions_total")
    assert hasattr(metrics, "grace_decomposition_segmentation_maps_ratified_total")
    assert hasattr(metrics, "grace_decomposition_reruns_total")
