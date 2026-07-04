"""Cross-type collision sub-mode tests for Signal A (F-0041 / ISS-0034).

Pure unit tests — Postgres substrate replaced with a fake session factory
returning canned rows; Prometheus replaced with a canned in-memory reader
(no live DB, no services, no httpx). The finding: the ISS-0034 ER fix flags
same-normalized-name different-type collisions at mint time
(``entity_resolution_log`` rows with ``resolution_note`` containing
``cross_type_name_collision``) but nothing surfaced them to operators — the
validation-run duplicate ("Crestline Water Authority" as BOTH Legal_Entity AND
Vendor) was only found by a human reviewer. These tests pin the new
Signal A sub-mode (template: test_signal_point_in_time.py, F-0037):

- collision log rows -> signal fires with mode marker + name/types/grace_ids;
- none -> silent; config off -> silent;
- strength = min(1.0, distinct_collisions / threshold), D245 bounds;
- same-module trend + collision merge into ONE record (DB unique constraint
  uq_analytics_signals_run_signal_module — F-0037 merge discipline);
- collision mode fires even when Prometheus prerequisites are not met.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.analytics.prometheus_reader import PromVectorEntry, PromVectorResult
from src.analytics.signal_pipeline.base import SignalRunContext
from src.analytics.signal_pipeline.config import (
    SignalAConfig,
    SignalPipelineConfig,
)
from src.analytics.signal_pipeline.signals.signal_a import SignalADetector


# ---------------------------------------------------------------------------
# Fake substrates.
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeSession:
    """Dispatch canned rows by matching substrings of the executed SQL."""

    def __init__(self, rows_by_sql_fragment: dict[str, list[tuple]]):
        self._rows_by_fragment = rows_by_sql_fragment
        self.closed = False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        for fragment, rows in self._rows_by_fragment.items():
            if fragment in sql:
                return _FakeResult(rows)
        return _FakeResult([])

    def close(self):
        self.closed = True


class _FakePromReader:
    """Instant-query stub dispatching on PromQL window fragments."""

    def __init__(self, entries_by_promql_fragment: dict[str, list[PromVectorEntry]]):
        self._by_fragment = entries_by_promql_fragment

    async def query_instant(self, promql: str) -> PromVectorResult:
        for fragment, entries in self._by_fragment.items():
            if fragment in promql:
                return PromVectorResult(entries=list(entries))
        return PromVectorResult()


def _prom_entry(value: float, metric: dict[str, str] | None = None) -> PromVectorEntry:
    return PromVectorEntry(metric=metric or {}, value_at=0.0, value=value)


def _make_context(
    rows_by_sql_fragment: dict[str, list[tuple]],
    config: SignalPipelineConfig,
    prom: dict[str, list[PromVectorEntry]] | None = None,
    target_ontology_modules: list[str] | None = None,
) -> SignalRunContext:
    return SignalRunContext(
        run_id=uuid4(),
        started_at=datetime.now(UTC),
        prometheus_reader=_FakePromReader(prom or {}),
        session_factory=lambda: _FakeSession(rows_by_sql_fragment),
        config=config,
        target_ontology_modules=target_ontology_modules,
    )


# SQL fragments unique to each Signal A query (see signal_a.py).
A_COLLISION_FRAGMENT = "FROM entity_resolution_log"
A_EVIDENCE_FRAGMENT = "FROM extraction_claims"


def _collision_row(
    name: str,
    extracted_type: str,
    other_type: str,
    grace_ids: tuple[str, str] = ("gid-a", "gid-b"),
) -> tuple:
    """One entity_resolution_log row as flagged by the ISS-0034 ER fix:
    (extracted_name, extracted_type, candidates_json)."""
    return (
        name,
        extracted_type,
        [
            # Non-collision candidate from an earlier tier — must be ignored.
            {"grace_id": "gid-noise", "name": name, "source": "tier1"},
            {
                "grace_id": grace_ids[0],
                "name": name,
                "entity_type": other_type,
                "flag": "cross_type_name_collision",
            },
            {
                "grace_id": grace_ids[1],
                "name": name,
                "entity_type": other_type,
                "flag": "cross_type_name_collision",
            },
        ],
    )


CROSS_TYPE_COLLISION = _collision_row(
    "Crestline Water Authority", "Vendor", "Legal_Entity",
    grace_ids=("gid-legal-1", "gid-vendor-1"),
)


# ---------------------------------------------------------------------------
# Firing + evidence.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_type_collision_fires_with_evidence():
    """F-0041 core repro: flagged log rows fire Signal A with the collision
    name, the types involved, grace_ids, and the mode marker."""
    rows = {A_COLLISION_FRAGMENT: [CROSS_TYPE_COLLISION]}
    ctx = _make_context(rows, SignalPipelineConfig())
    records = await SignalADetector().detect(ctx)

    assert len(records) == 1
    rec = records[0]
    assert rec.signal_type == "A"
    # entity_resolution_log has no ontology_module column -> "__global__".
    assert rec.ontology_module == "__global__"
    ev = rec.evidence_snapshot
    assert ev["mode"] == "cross_type_collision"
    assert ev["distinct_collisions"] == 1
    assert len(ev["collisions"]) == 1
    col = ev["collisions"][0]
    assert col["name"] == "Crestline Water Authority"
    assert col["types"] == ["Legal_Entity", "Vendor"]
    assert set(col["grace_ids"]) == {"gid-legal-1", "gid-vendor-1"}
    # The non-collision tier1 candidate must NOT leak into grace_ids.
    assert "gid-noise" not in col["grace_ids"]
    # Trend evidence keys must be absent — this is not a trend firing.
    assert "current_rate_per_sec" not in ev
    # min(1.0, 1/5) with the default threshold of 5 (D245 bounds).
    assert rec.strength == pytest.approx(0.2)
    assert 0.0 <= rec.strength <= 1.0
    # (b)-substrate backfill gap is documented in the evidence itself.
    assert "limitation" in ev


@pytest.mark.asyncio
async def test_cross_type_collision_no_rows_silent():
    """No flagged log rows (and cold Prometheus) -> nothing emitted."""
    ctx = _make_context({A_COLLISION_FRAGMENT: []}, SignalPipelineConfig())
    records = await SignalADetector().detect(ctx)
    assert records == []


@pytest.mark.asyncio
async def test_cross_type_collision_disabled_no_emission():
    """Config gate off -> flagged rows produce nothing (F-0037 gate pattern)."""
    cfg = SignalPipelineConfig(
        signal_a=SignalAConfig(cross_type_collision_enabled=False)
    )
    ctx = _make_context({A_COLLISION_FRAGMENT: [CROSS_TYPE_COLLISION]}, cfg)
    records = await SignalADetector().detect(ctx)
    assert records == []


@pytest.mark.asyncio
async def test_cross_type_collision_strength_saturates_at_one():
    """Distinct-name counts past the threshold clamp to 1.0 (D245 bounds)."""
    rows = {
        A_COLLISION_FRAGMENT: [
            _collision_row(f"Entity {i}", "Vendor", "Legal_Entity")
            for i in range(8)  # 8 distinct names, default threshold 5
        ]
    }
    ctx = _make_context(rows, SignalPipelineConfig())
    records = await SignalADetector().detect(ctx)
    assert len(records) == 1
    assert records[0].strength == 1.0
    assert records[0].evidence_snapshot["distinct_collisions"] == 8


@pytest.mark.asyncio
async def test_cross_type_collision_dedupes_repeat_log_rows():
    """N log rows for the SAME normalized name are one collision — strength
    counts distinct names, not resolver invocations."""
    rows = {
        A_COLLISION_FRAGMENT: [
            CROSS_TYPE_COLLISION,
            _collision_row(
                "crestline water authority", "Vendor", "Legal_Entity"
            ),
            CROSS_TYPE_COLLISION,
        ]
    }
    ctx = _make_context(rows, SignalPipelineConfig())
    records = await SignalADetector().detect(ctx)
    assert len(records) == 1
    ev = records[0].evidence_snapshot
    assert ev["distinct_collisions"] == 1
    assert ev["collisions"][0]["log_rows"] == 3
    assert records[0].strength == pytest.approx(0.2)


@pytest.mark.asyncio
async def test_cross_type_collision_threshold_knob():
    """The count_threshold config knob scales strength (F-0041)."""
    cfg = SignalPipelineConfig(
        signal_a=SignalAConfig(cross_type_count_threshold=2)
    )
    rows = {A_COLLISION_FRAGMENT: [CROSS_TYPE_COLLISION]}
    ctx = _make_context(rows, cfg)
    records = await SignalADetector().detect(ctx)
    assert len(records) == 1
    assert records[0].strength == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_cross_type_collision_respects_module_filter():
    """target_ontology_modules without '__global__' trims the collision
    record, matching the trend-mode filter discipline."""
    rows = {A_COLLISION_FRAGMENT: [CROSS_TYPE_COLLISION]}
    ctx = _make_context(
        rows, SignalPipelineConfig(), target_ontology_modules=["finance"]
    )
    records = await SignalADetector().detect(ctx)
    assert records == []


# ---------------------------------------------------------------------------
# Merge discipline with the trend mode (F-0037 template).
# ---------------------------------------------------------------------------


def _trend_prom() -> dict[str, list[PromVectorEntry]]:
    """Prometheus data that fires the trend mode under '__global__' (no
    ontology_module label): baseline 0.001/s x 14d >> 100 samples, current
    1.0/s >> baseline * sigma."""
    return {
        "[1d]": [_prom_entry(1.0)],
        "[14d]": [_prom_entry(0.001)],
    }


@pytest.mark.asyncio
async def test_trend_and_collision_merge_single_record():
    """Same module ('__global__') firing in BOTH modes yields ONE record (the
    DB unique constraint on (run_id, signal_type, ontology_module) forbids
    two rows) with trend evidence at top level and collision nested."""
    rows = {
        A_COLLISION_FRAGMENT: [CROSS_TYPE_COLLISION],
        A_EVIDENCE_FRAGMENT: [("Vendor", 7)],
    }
    ctx = _make_context(rows, SignalPipelineConfig(), prom=_trend_prom())
    records = await SignalADetector().detect(ctx)

    assert len(records) == 1, "must merge to one record for __global__"
    rec = records[0]
    assert rec.ontology_module == "__global__"
    ev = rec.evidence_snapshot
    # Trend evidence unchanged at top level.
    assert ev["current_rate_per_sec"] == pytest.approx(1.0)
    assert ev["baseline_rate_per_sec"] == pytest.approx(0.001)
    assert ev["top_entity_types"] == [{"entity_type": "Vendor", "count": 7}]
    # Collision evidence nested additively with its mode marker.
    nested = ev["cross_type_collision"]
    assert nested["mode"] == "cross_type_collision"
    assert nested["collisions"][0]["name"] == "Crestline Water Authority"
    # Strength is the max of the two modes, still D245-bounded.
    assert rec.strength >= 0.2
    assert 0.0 <= rec.strength <= 1.0


@pytest.mark.asyncio
async def test_trend_only_when_collision_mode_disabled_matches_legacy_shape():
    """With cross_type_collision_enabled=False the trend record keeps its
    exact legacy evidence shape (no cross_type_collision key, no marker)."""
    cfg = SignalPipelineConfig(
        signal_a=SignalAConfig(cross_type_collision_enabled=False)
    )
    rows = {
        A_COLLISION_FRAGMENT: [CROSS_TYPE_COLLISION],
        A_EVIDENCE_FRAGMENT: [("Vendor", 7)],
    }
    ctx = _make_context(rows, cfg, prom=_trend_prom())
    records = await SignalADetector().detect(ctx)
    assert len(records) == 1
    ev = records[0].evidence_snapshot
    assert set(ev.keys()) == {
        "top_entity_types",
        "current_rate_per_sec",
        "baseline_rate_per_sec",
        "sigma_multiplier",
    }


@pytest.mark.asyncio
async def test_collision_fires_even_when_prometheus_prerequisites_not_met():
    """F-0041 restructure guarantee: cold Prometheus previously returned []
    before any Postgres access; the collision sub-mode must still fire, while
    the trend prerequisites no-op stays visible in diagnostics (C1 contract
    unchanged)."""
    rows = {A_COLLISION_FRAGMENT: [CROSS_TYPE_COLLISION]}
    ctx = _make_context(rows, SignalPipelineConfig())  # empty Prometheus
    records = await SignalADetector().detect(ctx)

    assert len(records) == 1
    assert records[0].evidence_snapshot["mode"] == "cross_type_collision"
    noop = ctx.diagnostics.get("prerequisites_not_met", {})
    assert "A" in noop
    assert "prometheus_current_window_data" in noop["A"]
