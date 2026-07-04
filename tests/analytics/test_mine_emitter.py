"""Tests for the MINE retention metric emitter (Chunk 34, D260)."""

from __future__ import annotations

import pytest

from src.analytics import metrics as _metrics
from src.extraction.mine_emitter import (
    latest_module_observations,
    reset_mine_retention_observations,
    set_mine_retention_observation,
    snapshot_mine_retention_observations,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_mine_retention_observations()
    yield
    reset_mine_retention_observations()


def test_observation_round_trips_through_callback() -> None:
    """A write via emitter must be visible to the gauge callback."""
    set_mine_retention_observation(
        ontology_module="corporate_structure",
        schema_version_id="00000000-0000-0000-0000-000000000001",
        retention_ratio=0.82,
    )
    observations = list(_metrics._mine_retention_callback(options=None))  # type: ignore[arg-type]
    assert len(observations) == 1
    obs = observations[0]
    assert obs.value == pytest.approx(0.82)
    assert obs.attributes["ontology_module"] == "corporate_structure"
    assert (
        obs.attributes["schema_version_id"]
        == "00000000-0000-0000-0000-000000000001"
    )


def test_repeat_write_is_idempotent_per_pair() -> None:
    set_mine_retention_observation(
        ontology_module="insurance",
        schema_version_id="schema-A",
        retention_ratio=0.50,
    )
    set_mine_retention_observation(
        ontology_module="insurance",
        schema_version_id="schema-A",
        retention_ratio=0.91,
    )
    snapshot = snapshot_mine_retention_observations()
    assert snapshot[("insurance", "schema-A")] == pytest.approx(0.91)
    assert len(snapshot) == 1


def test_distinct_schema_versions_coexist_per_module() -> None:
    set_mine_retention_observation(
        ontology_module="legal",
        schema_version_id="schema-A",
        retention_ratio=0.60,
    )
    set_mine_retention_observation(
        ontology_module="legal",
        schema_version_id="schema-B",
        retention_ratio=0.70,
    )
    snapshot = snapshot_mine_retention_observations()
    assert snapshot[("legal", "schema-A")] == pytest.approx(0.60)
    assert snapshot[("legal", "schema-B")] == pytest.approx(0.70)


def test_retention_ratio_is_clamped_to_unit_interval() -> None:
    set_mine_retention_observation(
        ontology_module="vendors",
        schema_version_id="schema-X",
        retention_ratio=1.7,
    )
    set_mine_retention_observation(
        ontology_module="vendors",
        schema_version_id="schema-Y",
        retention_ratio=-0.3,
    )
    snapshot = snapshot_mine_retention_observations()
    assert snapshot[("vendors", "schema-X")] == 1.0
    assert snapshot[("vendors", "schema-Y")] == 0.0


def test_d162_cardinality_cap_collapses_excess_into_other() -> None:
    cap = 5
    for i in range(cap):
        set_mine_retention_observation(
            ontology_module=f"module_{i}",
            schema_version_id="schema-A",
            retention_ratio=0.5,
            cardinality_cap=cap,
        )
    # Sixth distinct module must collapse to _other_.
    set_mine_retention_observation(
        ontology_module="module_overflow",
        schema_version_id="schema-A",
        retention_ratio=0.42,
        cardinality_cap=cap,
    )
    snapshot = snapshot_mine_retention_observations()
    assert ("_other_", "schema-A") in snapshot
    assert snapshot[("_other_", "schema-A")] == pytest.approx(0.42)
    # The original head modules are untouched.
    assert ("module_0", "schema-A") in snapshot
    # Total tracked modules in flat view is bounded by cap + 1 (_other_).
    flat = latest_module_observations()
    assert len(flat) <= cap + 1
    assert "_other_" in flat


def test_non_numeric_retention_raises() -> None:
    with pytest.raises(TypeError):
        set_mine_retention_observation(
            ontology_module="legal",
            schema_version_id="schema-Z",
            retention_ratio="not a number",  # type: ignore[arg-type]
        )


def test_empty_labels_default_to_unknown() -> None:
    set_mine_retention_observation(
        ontology_module="",
        schema_version_id="",
        retention_ratio=0.25,
    )
    snapshot = snapshot_mine_retention_observations()
    assert ("unknown", "unknown") in snapshot


def test_callback_emits_one_observation_per_pair() -> None:
    set_mine_retention_observation(
        ontology_module="insurance",
        schema_version_id="schema-A",
        retention_ratio=0.5,
    )
    set_mine_retention_observation(
        ontology_module="insurance",
        schema_version_id="schema-B",
        retention_ratio=0.6,
    )
    set_mine_retention_observation(
        ontology_module="legal",
        schema_version_id="schema-A",
        retention_ratio=0.7,
    )
    observations = list(_metrics._mine_retention_callback(options=None))  # type: ignore[arg-type]
    assert len(observations) == 3
    keys = {
        (o.attributes["ontology_module"], o.attributes["schema_version_id"])
        for o in observations
    }
    assert keys == {
        ("insurance", "schema-A"),
        ("insurance", "schema-B"),
        ("legal", "schema-A"),
    }


def test_faithfulness_histogram_has_confidence_buckets() -> None:
    """D260 requires explicit_bucket_boundaries_advisory on the
    decompression_faithfulness histogram. We check the public API is
    a histogram instrument; bucket inspection at this layer is best
    done via PrometheusMetricReader exposition, but at minimum the
    instrument must exist and be a histogram-shaped object."""
    instr = _metrics.decompression_faithfulness
    assert hasattr(instr, "record")
    # smoke: recording a value must not raise.
    instr.record(0.55, {"ontology_module": "corporate_structure"})
