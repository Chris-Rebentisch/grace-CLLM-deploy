"""Unit tests for the F-0038 / ISS-0027 signal lookback-window fix.

Pure unit — mocked session/rows, no Postgres, no ArcadeDB, no services.

F-0038 (validation run 2026-07-03): ``fetch_latest_signal_strengths``
joined only the single most-recent ``signal_runs`` row, so per-signal
invocations (``run-all --signal X``) fragmented signals across runs and
silently blanked every conjunction pattern. The fix windows across ALL
successful runs within ``CorrelationEngineConfig.signal_lookback_hours``
(default 24h), taking the latest signal per signal_type/ontology_module.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from src.analytics.correlation_engine.base import CorrelationRunContext
from src.analytics.correlation_engine.config import CorrelationEngineConfig
from src.analytics.correlation_engine.patterns._helpers import (
    fetch_latest_signal_strengths,
)
from src.analytics.correlation_engine.patterns.ontology_constraint_conflict import (
    OntologyConstraintConflictDetector,
)
from src.analytics.correlation_engine.patterns.schema_drift_per_module import (
    SchemaDriftPerModuleDetector,
)


NOW = datetime.now(UTC)


def _row(
    module: str,
    strength: float,
    run_id,
    detected_at: datetime,
    evidence: dict[str, Any] | None = None,
) -> tuple:
    """One mocked analytics_signals row in the helper's SELECT column order:
    (ontology_module, strength, evidence_snapshot, run_id, detected_at)."""
    return (module, strength, evidence or {}, run_id, detected_at)


class _FakeResult:
    def __init__(self, rows: list[tuple]):
        self._rows = rows

    def all(self) -> list[tuple]:
        return self._rows


class _FakeSession:
    """Mocked SQLAlchemy session: dispatches rows by :signal_type param."""

    def __init__(self, rows_by_signal_type: dict[str, list[tuple]]):
        self._rows = rows_by_signal_type
        self.captured: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    def execute(self, stmt, params: dict[str, Any]) -> _FakeResult:
        self.captured.append((str(stmt), dict(params)))
        return _FakeResult(list(self._rows.get(params["signal_type"], [])))

    def close(self) -> None:
        self.closed = True


class _UnusedPrometheusReader:
    """Sentinel — pure-DB paths under test must never touch Prometheus."""

    def __getattr__(self, name: str):  # pragma: no cover - defensive
        raise AssertionError(f"Prometheus must not be queried (attr {name!r})")


def _make_context(
    rows_by_signal_type: dict[str, list[tuple]],
    config: CorrelationEngineConfig | None = None,
) -> tuple[CorrelationRunContext, _FakeSession]:
    session = _FakeSession(rows_by_signal_type)
    ctx = CorrelationRunContext(
        run_id=uuid4(),
        started_at=NOW,
        prometheus_reader=_UnusedPrometheusReader(),
        session_factory=lambda: session,
        config=config or CorrelationEngineConfig(),
        target_ontology_modules=None,
    )
    return ctx, session


# ---------------------------------------------------------------------------
# Config surface
# ---------------------------------------------------------------------------


def test_config_default_signal_lookback_hours_is_24():
    assert CorrelationEngineConfig().signal_lookback_hours == 24.0


def test_config_signal_lookback_hours_operator_tunable():
    cfg = CorrelationEngineConfig.model_validate({"signal_lookback_hours": 6.5})
    assert cfg.signal_lookback_hours == 6.5


# ---------------------------------------------------------------------------
# Helper: window semantics
# ---------------------------------------------------------------------------


def test_six_signals_across_six_runs_all_visible():
    """F-0038 repro: per-signal runs — each of A–F persisted in its OWN
    signal_runs row. The windowed helper must see all six (the old
    latest-run-only join saw at most one)."""
    run_ids = {sig: uuid4() for sig in "ABCDEF"}
    rows = {
        sig: [
            _row(
                "finance",
                0.6,
                run_ids[sig],
                NOW - timedelta(minutes=10 * i),
            )
        ]
        for i, sig in enumerate("ABCDEF")
    }
    ctx, session = _make_context(rows)

    for sig in "ABCDEF":
        by_module = fetch_latest_signal_strengths(ctx, sig)
        assert "finance" in by_module, f"signal {sig} blanked"
        entry = by_module["finance"]
        assert entry["strength"] == 0.6
        # Evidence honesty: each entry names its originating signal_run.
        assert entry["signal_run_id"] == str(run_ids[sig])
    assert session.closed


def test_stale_signal_outside_window_excluded():
    """A signal older than signal_lookback_hours is excluded; a fresher
    in-window signal for the same type/module is kept."""
    fresh_run, stale_run, stale_only_run = uuid4(), uuid4(), uuid4()
    rows = {
        "A": [
            _row("finance", 0.9, fresh_run, NOW - timedelta(hours=1)),
            _row("finance", 0.2, stale_run, NOW - timedelta(hours=30)),
            _row("legal", 0.8, stale_only_run, NOW - timedelta(hours=25)),
        ]
    }
    ctx, _ = _make_context(rows)

    by_module = fetch_latest_signal_strengths(ctx, "A")
    assert set(by_module) == {"finance"}  # stale-only 'legal' excluded
    assert by_module["finance"]["strength"] == 0.9
    assert by_module["finance"]["signal_run_id"] == str(fresh_run)


def test_stale_exclusion_respects_configured_lookback():
    """With a 2h window, a 3h-old signal is stale even though < 24h."""
    run_id = uuid4()
    rows = {"A": [_row("finance", 0.9, run_id, NOW - timedelta(hours=3))]}
    cfg = CorrelationEngineConfig(signal_lookback_hours=2.0)
    ctx, session = _make_context(rows, config=cfg)

    assert fetch_latest_signal_strengths(ctx, "A") == {}
    # And the SQL-side cutoff param matches now - 2h (belt-and-braces:
    # the same window is enforced in the query AND in Python).
    _, params = session.captured[0]
    expected_cutoff = datetime.now(UTC) - timedelta(hours=2.0)
    assert abs((params["cutoff"] - expected_cutoff).total_seconds()) < 60


def test_latest_per_module_wins_across_runs():
    """Within the window, the NEWEST signal per module wins (latest
    semantics, not max-strength), even when mocked rows arrive unordered."""
    old_run, new_run = uuid4(), uuid4()
    rows = {
        "C": [
            # unordered on purpose: older-but-stronger listed first
            _row("finance", 0.9, old_run, NOW - timedelta(hours=5)),
            _row("finance", 0.4, new_run, NOW - timedelta(hours=1)),
        ]
    }
    ctx, _ = _make_context(rows)

    by_module = fetch_latest_signal_strengths(ctx, "C")
    assert by_module["finance"]["strength"] == 0.4
    assert by_module["finance"]["signal_run_id"] == str(new_run)


def test_single_run_behavior_unchanged():
    """All signals persisted by ONE run (the pre-F-0038 happy path) —
    strengths and evidence come back exactly as before."""
    run_id = uuid4()
    ts = NOW - timedelta(minutes=5)
    rows = {
        "C": [_row("finance", 0.7, run_id, ts, {"k": "v"})],
        "D": [_row("finance", 0.6, run_id, ts)],
    }
    ctx, _ = _make_context(rows)

    c = fetch_latest_signal_strengths(ctx, "C")
    d = fetch_latest_signal_strengths(ctx, "D")
    assert c["finance"]["strength"] == 0.7
    assert c["finance"]["evidence_snapshot"] == {"k": "v"}
    assert d["finance"]["strength"] == 0.6
    assert c["finance"]["signal_run_id"] == d["finance"]["signal_run_id"] == str(run_id)


def test_query_windows_all_runs_not_latest_only():
    """The emitted SQL must window on detected_at across all successful
    runs — no LIMIT 1 latest-run CTE (the F-0038 root cause)."""
    ctx, session = _make_context({"A": []})
    fetch_latest_signal_strengths(ctx, "A")

    sql, params = session.captured[0]
    assert "LIMIT 1" not in sql
    assert ":cutoff" in sql
    assert "status = 'success'" in sql
    assert params["signal_type"] == "A"


def test_naive_detected_at_treated_as_utc():
    """Naive timestamps (driver quirk) are treated as UTC, not dropped."""
    run_id = uuid4()
    naive_ts = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    rows = {"A": [_row("finance", 0.5, run_id, naive_ts)]}
    ctx, _ = _make_context(rows)

    by_module = fetch_latest_signal_strengths(ctx, "A")
    assert by_module["finance"]["strength"] == 0.5


# ---------------------------------------------------------------------------
# Conjunction detectors see cross-run signals (the F-0038 symptom)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schema_drift_conjunction_fires_across_runs():
    """Signal C and Signal D persisted by DIFFERENT runs within the window
    → the C∧D conjunction still fires (it returned 0 patterns pre-fix)."""
    run_c, run_d = uuid4(), uuid4()
    rows = {
        "C": [_row("finance", 0.7, run_c, NOW - timedelta(hours=2))],
        "D": [_row("finance", 0.6, run_d, NOW - timedelta(hours=1))],
    }
    ctx, _ = _make_context(rows)

    records = await SchemaDriftPerModuleDetector().detect(ctx)

    assert len(records) == 1
    rec = records[0]
    assert rec.ontology_module == "finance"
    assert rec.correlation_strength == pytest.approx(0.65)
    # Evidence honesty: contributing signals name their originating runs.
    by_signal = {c["signal"]: c for c in rec.contributing_signals}
    assert by_signal["C"]["signal_run_id"] == str(run_c)
    assert by_signal["D"]["signal_run_id"] == str(run_d)


@pytest.mark.asyncio
async def test_ontology_constraint_conjunction_fires_across_runs():
    """E∧B conjunction across two per-signal runs fires (F-0038 repro for
    the D535 sixth pattern)."""
    run_e, run_b = uuid4(), uuid4()
    rows = {
        "E": [_row("legal", 0.8, run_e, NOW - timedelta(hours=3))],
        "B": [_row("legal", 0.55, run_b, NOW - timedelta(minutes=30))],
    }
    ctx, _ = _make_context(rows)

    records = await OntologyConstraintConflictDetector().detect(ctx)

    assert len(records) == 1
    rec = records[0]
    assert rec.ontology_module == "legal"
    by_signal = {c["signal"]: c for c in rec.contributing_signals}
    assert by_signal["E"]["signal_run_id"] == str(run_e)
    assert by_signal["B"]["signal_run_id"] == str(run_b)


@pytest.mark.asyncio
async def test_conjunction_ignores_stale_partner_signal():
    """C fresh but D outside the window → conjunction must NOT fire."""
    run_c, run_d = uuid4(), uuid4()
    rows = {
        "C": [_row("finance", 0.7, run_c, NOW - timedelta(hours=1))],
        "D": [_row("finance", 0.6, run_d, NOW - timedelta(hours=48))],
    }
    ctx, _ = _make_context(rows)

    records = await SchemaDriftPerModuleDetector().detect(ctx)
    assert records == []


@pytest.mark.asyncio
async def test_schema_drift_single_run_conjunction_unchanged():
    """Both signals from ONE run (legacy path) still fires identically."""
    run_id = uuid4()
    ts = NOW - timedelta(minutes=5)
    rows = {
        "C": [_row("finance", 0.7, run_id, ts)],
        "D": [_row("finance", 0.6, run_id, ts)],
    }
    ctx, _ = _make_context(rows)

    records = await SchemaDriftPerModuleDetector().detect(ctx)
    assert len(records) == 1
    assert records[0].correlation_strength == pytest.approx(0.65)
