"""F-15 regression tests: CLI-subprocess counters survive into /metrics.

Transport: prometheus_client multiprocess mmap files (no collector container,
no pushgateway). A real child process records an OTel counter through the
write-through; the parent aggregates the directory the way /metrics does.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

_CHILD_SCRIPT = """
import os
# PROMETHEUS_MULTIPROC_DIR is inherited from the parent env.
from src.analytics.subprocess_metrics import init_subprocess_metrics
assert init_subprocess_metrics() is True

from opentelemetry import metrics as otel_metrics
meter = otel_metrics.get_meter("grace.analytics")
counter = meter.create_counter(
    "grace_f15_probe_total", description="F-15 transport probe"
)
counter.add(3, {"outcome": "ok"})
# Process exit -> MeterProvider atexit shutdown -> exporter flush -> mmap file.
"""


@pytest.fixture()
def multiproc_dir(tmp_path, monkeypatch):
    d = tmp_path / "prom-multiproc"
    d.mkdir()
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(d))
    return d


def test_disabled_without_env(monkeypatch):
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    import importlib

    import src.analytics.subprocess_metrics as sm

    importlib.reload(sm)
    assert sm.init_subprocess_metrics() is False
    assert sm.multiproc_exposition() == b""


def test_child_process_counter_reaches_parent_aggregation(multiproc_dir):
    """End-to-end: child records -> mmap file -> parent /metrics aggregation."""
    result = subprocess.run(
        [sys.executable, "-c", _CHILD_SCRIPT],
        capture_output=True,
        text=True,
        cwd="/Users/glennys/grace-CLLM-deploy",
        env={
            **__import__("os").environ,
            "PROMETHEUS_MULTIPROC_DIR": str(multiproc_dir),
            "PYTHONPATH": ".",
        },
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]

    # Parent side: aggregate exactly like the /metrics route does.
    import importlib

    import src.analytics.subprocess_metrics as sm

    importlib.reload(sm)
    exposition = sm.multiproc_exposition().decode()
    assert "grace_f15_probe" in exposition
    assert 'outcome="ok"' in exposition
    # The value 3.0 recorded in the child must be visible in the parent.
    probe_lines = [
        line
        for line in exposition.splitlines()
        if line.startswith("grace_f15_probe") and "ok" in line
    ]
    assert any(line.rstrip().endswith("3.0") for line in probe_lines), probe_lines


def test_empty_dir_yields_empty_exposition(multiproc_dir):
    import importlib

    import src.analytics.subprocess_metrics as sm

    importlib.reload(sm)
    assert sm.multiproc_exposition() == b""
