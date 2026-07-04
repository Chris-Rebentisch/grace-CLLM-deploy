"""Point-in-time firing-mode tests for Signals D and F (F-0037 / ISS-0028).

Pure unit tests — the Postgres substrate is replaced with a fake session
factory returning canned rows (no live DB, no services). The finding: both
detectors were trend-only (Mann-Kendall over >=10 points), so a single
deprecated-type claim (D) or a single failing cq-test run (F) could
mathematically never fire. These tests pin the new point-in-time modes:

- D: one recent quarantined deprecated-type claim fires with mode marker;
  trend does not fire on the same data.
- F: one completed cq-test run with failures fires; strength = failure rate.
- Config off -> no point-in-time emission (trend behavior untouched).
- Strengths stay within [0, 1] (D245).
- Same-module trend + point-in-time merge into ONE record (DB unique
  constraint uq_analytics_signals_run_signal_module).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src.analytics.signal_pipeline.base import SignalRunContext
from src.analytics.signal_pipeline.config import (
    SignalDConfig,
    SignalFConfig,
    SignalPipelineConfig,
)
from src.analytics.signal_pipeline.signals.signal_d import SignalDDetector
from src.analytics.signal_pipeline.signals.signal_f import SignalFDetector


# ---------------------------------------------------------------------------
# Fake substrate — mocks the sqlalchemy session the detectors open via
# run_context.session_factory(). Dispatches on SQL text so the trend and
# point-in-time queries can return independent canned row sets.
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


def _make_context(
    rows_by_sql_fragment: dict[str, list[tuple]],
    config: SignalPipelineConfig,
    target_ontology_modules: list[str] | None = None,
) -> SignalRunContext:
    return SignalRunContext(
        run_id=uuid4(),
        started_at=datetime.now(UTC),
        prometheus_reader=None,  # D and F never touch Prometheus
        session_factory=lambda: _FakeSession(rows_by_sql_fragment),
        config=config,
        target_ontology_modules=target_ontology_modules,
    )


# SQL fragments unique to each detector query (see signal_d.py / signal_f.py).
D_TREND_FRAGMENT = "date_trunc"
D_PIT_FRAGMENT = "constraint_violations"
F_RUNS_FRAGMENT = "FROM cq_test_runs"


def _cq_entry(j: int, *, fail: bool, gap_type: str | None = None) -> dict:
    return {
        "cq_id": f"cq-{j}",
        "cq_text": f"Can the graph answer question {j}?",
        "domain": "other",
        "result": "fail" if fail else "pass",
        "confidence": 0.5,
        "reasoning": "",
        "gap_type": gap_type,
        "gap_severity": None,
        "gap_details": None,
    }


# ---------------------------------------------------------------------------
# Signal D — point-in-time
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signal_d_point_in_time_fires_on_single_deprecated_claim():
    """F-0037 core repro: ONE deprecated-type claim fires point-in-time;
    the trend mode (which needs >=10 daily points) does not fire."""
    rows = {
        D_TREND_FRAGMENT: [],  # no daily-count history at all
        D_PIT_FRAGMENT: [("finance", "Legacy_Account", 1)],
    }
    ctx = _make_context(rows, SignalPipelineConfig())
    records = await SignalDDetector().detect(ctx)

    assert len(records) == 1
    rec = records[0]
    assert rec.signal_type == "D"
    assert rec.ontology_module == "finance"
    ev = rec.evidence_snapshot
    assert ev["mode"] == "point_in_time"
    assert ev["entity_type"] == "Legacy_Account"
    assert ev["count"] == 1
    # Trend evidence keys must be absent — this is not a trend firing.
    assert "trend" not in ev
    assert "p_value" not in ev
    # min(1.0, 1/5) with the default threshold of 5.
    assert rec.strength == pytest.approx(0.2)
    assert 0.0 <= rec.strength <= 1.0


@pytest.mark.asyncio
async def test_signal_d_point_in_time_disabled_no_emission():
    """Config gate off -> deprecated-type claims produce nothing."""
    cfg = SignalPipelineConfig(signal_d=SignalDConfig(point_in_time_enabled=False))
    rows = {
        D_TREND_FRAGMENT: [],
        D_PIT_FRAGMENT: [("finance", "Legacy_Account", 3)],
    }
    ctx = _make_context(rows, cfg)
    records = await SignalDDetector().detect(ctx)
    assert records == []


@pytest.mark.asyncio
async def test_signal_d_point_in_time_strength_saturates_at_one():
    """Counts far past the threshold clamp to 1.0 (D245 bounds)."""
    rows = {
        D_TREND_FRAGMENT: [],
        D_PIT_FRAGMENT: [("legal", "Old_Matter", 50)],
    }
    ctx = _make_context(rows, SignalPipelineConfig())
    records = await SignalDDetector().detect(ctx)
    assert len(records) == 1
    assert records[0].strength == 1.0


@pytest.mark.asyncio
async def test_signal_d_trend_and_point_in_time_merge_single_record():
    """Same module firing in BOTH modes yields ONE record (the DB unique
    constraint on (run_id, signal_type, ontology_module) forbids two rows)
    with trend evidence at top level and point-in-time nested."""
    module = "finance"
    base = datetime.now(UTC) - timedelta(days=12)
    # Strictly decreasing daily counts, 12 points -> MK trend "decreasing".
    trend_rows = [
        (module, "DyingType", base + timedelta(days=i), 12 - i) for i in range(12)
    ]
    rows = {
        D_TREND_FRAGMENT: trend_rows,
        D_PIT_FRAGMENT: [(module, "DyingType", 2)],
    }
    ctx = _make_context(rows, SignalPipelineConfig())
    records = await SignalDDetector().detect(ctx)

    assert len(records) == 1, "must collapse to one record per module"
    rec = records[0]
    ev = rec.evidence_snapshot
    # Trend evidence unchanged at top level.
    assert ev["trend"] == "decreasing"
    assert "p_value" in ev
    # Point-in-time nested additively with its mode marker.
    assert ev["point_in_time"]["mode"] == "point_in_time"
    assert ev["point_in_time"]["entity_type"] == "DyingType"
    assert ev["point_in_time"]["count"] == 2
    assert 0.0 <= rec.strength <= 1.0


@pytest.mark.asyncio
async def test_signal_d_point_in_time_respects_module_filter():
    """target_ontology_modules trims point-in-time rows like trend rows."""
    rows = {
        D_TREND_FRAGMENT: [],
        D_PIT_FRAGMENT: [
            ("finance", "Legacy_Account", 2),
            ("legal", "Old_Matter", 4),
        ],
    }
    ctx = _make_context(
        rows, SignalPipelineConfig(), target_ontology_modules=["legal"]
    )
    records = await SignalDDetector().detect(ctx)
    assert len(records) == 1
    assert records[0].ontology_module == "legal"


# ---------------------------------------------------------------------------
# Signal F — point-in-time
# ---------------------------------------------------------------------------


def _f_run_row(*, total: int, failing: int, age_days: int) -> tuple:
    results = [_cq_entry(j, fail=j < failing) for j in range(total)]
    return (
        uuid4(),
        datetime.now(UTC) - timedelta(days=age_days),
        total,
        failing,
        results,
    )


@pytest.mark.asyncio
async def test_signal_f_point_in_time_fires_on_single_run_with_failures():
    """F-0037 core repro: ONE completed cq-test run with failures fires;
    the trend mode (which needs >=10 runs) does not."""
    rows = {F_RUNS_FRAGMENT: [_f_run_row(total=10, failing=3, age_days=0)]}
    ctx = _make_context(rows, SignalPipelineConfig())
    records = await SignalFDetector().detect(ctx)

    assert len(records) == 1
    rec = records[0]
    assert rec.signal_type == "F"
    assert rec.ontology_module == "__global__"
    ev = rec.evidence_snapshot
    assert ev["mode"] == "point_in_time"
    assert ev["failing"] == 3
    assert ev["total_cqs"] == 10
    # Strength = failure rate of the latest run.
    assert rec.strength == pytest.approx(0.3)
    assert 0.0 <= rec.strength <= 1.0
    # Failing CQ ids/texts are present.
    failing_ids = {e["cq_id"] for e in ev["failing_cqs"]}
    assert failing_ids == {"cq-0", "cq-1", "cq-2"}
    assert all(e["cq_text"] for e in ev["failing_cqs"])
    # Trend evidence keys must be absent — this is not a trend firing.
    assert "trend" not in ev
    assert "failure_rate_series" not in ev
    # Deliberate-gap limitation documented in evidence (F-0037 known limit).
    assert "limitation" in ev


@pytest.mark.asyncio
async def test_signal_f_point_in_time_disabled_no_emission():
    """Config gate off + short window -> nothing emitted at all."""
    cfg = SignalPipelineConfig(signal_f=SignalFConfig(point_in_time_enabled=False))
    rows = {F_RUNS_FRAGMENT: [_f_run_row(total=10, failing=3, age_days=0)]}
    ctx = _make_context(rows, cfg)
    records = await SignalFDetector().detect(ctx)
    assert records == []


@pytest.mark.asyncio
async def test_signal_f_point_in_time_no_failures_no_emission():
    """Latest run fully passing -> no point-in-time record."""
    rows = {F_RUNS_FRAGMENT: [_f_run_row(total=10, failing=0, age_days=0)]}
    ctx = _make_context(rows, SignalPipelineConfig())
    records = await SignalFDetector().detect(ctx)
    assert records == []


@pytest.mark.asyncio
async def test_signal_f_trend_and_point_in_time_merge_single_record():
    """Both modes under '__global__' MUST merge into one record (DB unique
    constraint) — trend evidence top-level, point-in-time nested."""
    # 12 runs, monotonically increasing failure rate -> MK "increasing".
    rows = {
        F_RUNS_FRAGMENT: [
            _f_run_row(total=20, failing=i + 1, age_days=12 - i) for i in range(12)
        ]
    }
    ctx = _make_context(rows, SignalPipelineConfig())
    records = await SignalFDetector().detect(ctx)

    assert len(records) == 1, "must merge to one record for __global__"
    rec = records[0]
    ev = rec.evidence_snapshot
    # Trend evidence unchanged at top level.
    assert ev["trend"] == "increasing"
    assert "failure_rate_series" in ev
    assert "p_value" in ev
    # Point-in-time nested additively; latest run is 12/20 failing.
    pit = ev["point_in_time"]
    assert pit["mode"] == "point_in_time"
    assert pit["failing"] == 12
    assert pit["failure_rate"] == pytest.approx(0.6)
    assert rec.strength >= 0.6  # max(trend, point-in-time)
    assert 0.0 <= rec.strength <= 1.0


@pytest.mark.asyncio
async def test_signal_f_trend_only_when_pit_disabled_matches_legacy_shape():
    """With point_in_time_enabled=False the trend record keeps its exact
    legacy evidence shape (no point_in_time key, no mode marker)."""
    cfg = SignalPipelineConfig(signal_f=SignalFConfig(point_in_time_enabled=False))
    rows = {
        F_RUNS_FRAGMENT: [
            _f_run_row(total=20, failing=i + 1, age_days=12 - i) for i in range(12)
        ]
    }
    ctx = _make_context(rows, cfg)
    records = await SignalFDetector().detect(ctx)
    assert len(records) == 1
    ev = records[0].evidence_snapshot
    assert set(ev.keys()) == {
        "failure_rate_series",
        "trend",
        "p_value",
        "top_failing_cqs",
    }
