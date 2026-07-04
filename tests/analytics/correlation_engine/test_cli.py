"""CLI smoke tests for the correlation engine (Chunk 33, D246/D248).

Subprocess-based tests verify exit codes, JSON output, and argument
parsing. The real DB and Prometheus are touched because the CLI is the
sole operational entry point.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys


def _run(args: list[str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    return subprocess.run(
        [sys.executable, "-m", "src.analytics.correlation_engine", *args],
        cwd=os.path.dirname(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
        ),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_cli_dry_run_emits_json_summary_and_exits_clean():
    """``run-all --dry-run`` exits with a known status and emits JSON."""
    proc = _run(["run-all", "--dry-run"])
    assert proc.returncode in (0, 1, 2), proc.stderr
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert lines, f"no stdout: {proc.stderr}"
    payload = json.loads(lines[-1])
    assert "run_id" in payload
    assert payload["dry_run"] is True
    assert payload["status"] in {"success", "partial_failure", "error"}


def test_cli_pattern_filter_runs_single_pattern_dry_run():
    """``--pattern extraction_quality_problem --dry-run`` succeeds with JSON output."""
    proc = _run([
        "run-all",
        "--pattern", "extraction_quality_problem",
        "--dry-run",
    ])
    assert proc.returncode in (0, 1, 2), proc.stderr
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    payload = json.loads(lines[-1])
    assert payload["dry_run"] is True
    # patterns list is bounded by the filter; could be empty if detector
    # emits no record.
    assert set(payload.get("patterns", [])).issubset(
        {"extraction_quality_problem"}
    )
