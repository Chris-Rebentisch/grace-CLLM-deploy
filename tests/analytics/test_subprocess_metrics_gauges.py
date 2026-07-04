"""F-0049/ISS-0040: last-value GAUGE mirroring through the multiproc transport.

Validation run F-0049: the signal pipeline's ``grace_signal_*_strength``
and correlation engine's ``grace_correlation_*_strength`` families are OTel
Gauges, which the F-15 counters-only mirror (and its F-0034 histogram
extension) silently dropped. These tests exercise the gauge path end-to-end
the same way tests/analytics/test_subprocess_metrics_f15.py does: a real
child process records gauges through the write-through; the parent aggregates
the mmap directory exactly like the /metrics route.

Pure unit tests — no Postgres, no ArcadeDB, no services.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = str(Path(__file__).resolve().parents[2])

_CHILD_SCRIPT = """
import os
# PROMETHEUS_MULTIPROC_DIR is inherited from the parent env.
from src.analytics.subprocess_metrics import init_subprocess_metrics
assert init_subprocess_metrics() is True

from opentelemetry import metrics as otel_metrics
meter = otel_metrics.get_meter("grace.analytics")

# Sync gauge, labeled — mirrors the signal-strength family shape. Two writes:
# last value (0.7) must win under multiprocess_mode="mostrecent".
gauge = meter.create_gauge(
    "grace_f0049_probe_strength", description="F-0049 gauge transport probe"
)
gauge.set(0.25, {"signal": "A"})
gauge.set(0.7, {"signal": "A"})

# Unlabeled gauge with a 0.0 value — 0.0 is a legitimate last value and must
# NOT be skipped the way zero counter deltas are.
zero_gauge = meter.create_gauge(
    "grace_f0049_probe_zero", description="F-0049 zero-value gauge probe"
)
zero_gauge.set(0.0)
# Process exit -> MeterProvider atexit shutdown -> exporter flush -> mmap file.
"""


@pytest.fixture()
def multiproc_dir(tmp_path, monkeypatch):
    d = tmp_path / "prom-multiproc"
    d.mkdir()
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(d))
    return d


def _run_child(multiproc_dir) -> None:
    result = subprocess.run(
        [sys.executable, "-c", _CHILD_SCRIPT],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "PROMETHEUS_MULTIPROC_DIR": str(multiproc_dir),
            "PYTHONPATH": ".",
        },
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]


def _parent_exposition() -> str:
    import importlib

    import src.analytics.subprocess_metrics as sm

    importlib.reload(sm)
    return sm.multiproc_exposition().decode()


def test_gauge_mirrored_under_exact_name_with_last_value(multiproc_dir):
    """Child gauge -> mmap file -> parent aggregation, exact name, last value."""
    _run_child(multiproc_dir)
    exposition = _parent_exposition()

    # Exact metric name — no _total suffix, no renaming.
    assert "grace_f0049_probe_strength" in exposition
    assert "grace_f0049_probe_strength_total" not in exposition

    strength_lines = [
        line
        for line in exposition.splitlines()
        if line.startswith("grace_f0049_probe_strength{") and 'signal="A"' in line
    ]
    assert strength_lines, exposition
    # mostrecent semantics: the second set() (0.7) wins, values are not summed
    # (livesum would show 0.95) and the first write (0.25) does not linger.
    assert any(line.rstrip().endswith("0.7") for line in strength_lines), strength_lines


def test_zero_gauge_value_is_not_dropped(multiproc_dir):
    """0.0 is a legitimate last value — the mirror must not zero-skip gauges."""
    _run_child(multiproc_dir)
    exposition = _parent_exposition()

    zero_lines = [
        line
        for line in exposition.splitlines()
        if line.startswith("grace_f0049_probe_zero") and not line.startswith("#")
    ]
    assert zero_lines, exposition
    assert any(line.rstrip().endswith("0.0") for line in zero_lines), zero_lines


def test_counter_path_still_mirrors_alongside_gauges(multiproc_dir):
    """F-15 counter mirroring must survive the F-0049 gauge extension."""
    script = _CHILD_SCRIPT + (
        "\ncounter = meter.create_counter("
        '"grace_f0049_coexist_total", description="coexistence probe")\n'
        'counter.add(2, {"outcome": "ok"})\n'
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "PROMETHEUS_MULTIPROC_DIR": str(multiproc_dir),
            "PYTHONPATH": ".",
        },
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]

    exposition = _parent_exposition()
    assert "grace_f0049_coexist" in exposition
    assert "grace_f0049_probe_strength" in exposition
    counter_lines = [
        line
        for line in exposition.splitlines()
        if line.startswith("grace_f0049_coexist") and "ok" in line
    ]
    assert any(line.rstrip().endswith("2.0") for line in counter_lines), counter_lines
