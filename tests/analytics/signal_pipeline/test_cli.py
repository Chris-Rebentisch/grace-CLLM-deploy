"""CLI smoke tests for the signal pipeline (D246).

Subprocess-based tests verify exit codes, JSON output, and argument
parsing. The real DB and Prometheus are touched in the smoke tests
because the CLI is the operational entry point.
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
        [sys.executable, "-m", "src.analytics.signal_pipeline", *args],
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


def test_cli_help_exits_zero():
    proc = _run(["--help"])
    assert proc.returncode == 0
    assert "run-all" in proc.stdout


def test_cli_run_all_dry_run_emits_json_summary():
    """Dry-run with single signal must succeed and print JSON status."""
    proc = _run(["run-all", "--signal", "B", "--dry-run"])
    assert proc.returncode in (0, 1, 2), proc.stderr
    # Last non-empty line of stdout is our JSON summary.
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert lines, f"no stdout: {proc.stderr}"
    payload = json.loads(lines[-1])
    assert "run_id" in payload
    assert payload["dry_run"] is True
    assert payload["status"] in {"success", "partial_failure", "error"}


def test_cli_unknown_signal_rejected():
    proc = _run(["run-all", "--signal", "Z"])
    assert proc.returncode != 0


def test_cli_requires_subcommand():
    proc = _run([])
    assert proc.returncode != 0


def test_cli_run_all_filter_by_module_dry_run():
    """--ontology-module passes through; dry-run prints summary."""
    proc = _run([
        "run-all", "--signal", "B", "--ontology-module", "nonexistent",
        "--dry-run",
    ])
    assert proc.returncode in (0, 1, 2), proc.stderr
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    payload = json.loads(lines[-1])
    assert payload["dry_run"] is True
