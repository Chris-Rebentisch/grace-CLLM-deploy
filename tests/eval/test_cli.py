"""Eval CLI tests (Chunk 34, D257)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.eval import cli as cli_module


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_validate_golden_exit_codes(capsys, tmp_path):
    """``validate-golden`` exits 0 on the canonical dataset and 1 on an
    empty/invalid directory.
    """
    rc = cli_module.main(["validate-golden"])
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip().splitlines()[-1])
    assert payload["status"] == "valid"

    bad_dir = tmp_path / "empty"
    bad_dir.mkdir()
    rc_bad = cli_module.main(["validate-golden", "--golden-dir", str(bad_dir)])
    assert rc_bad == 1


def test_run_suite_dry_run_writes_nothing(capsys, monkeypatch):
    """``run-suite --dry-run`` exits 0, prints status, and never touches the DB.

    Asserted by patching ``write_run`` to raise if called.
    """

    def boom(*args, **kwargs):
        raise AssertionError("dry-run must not call write_run")

    monkeypatch.setattr("src.eval.results_writer.write_run", boom, raising=True)
    rc = cli_module.main(["run-suite", "--dry-run"])
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip().splitlines()[-1])
    assert payload["status"] == "dry_run"
    assert payload["total_cases"] >= 50


def test_subcommand_dispatch(capsys):
    """``show-config`` exits 0 and emits a JSON object with the threshold map."""
    rc = cli_module.main(["show-config"])
    assert rc == 0
    captured = capsys.readouterr()
    blob = "\n".join(captured.out.strip().splitlines())
    payload = json.loads(blob)
    assert "thresholds" in payload
    assert "faithfulness" in payload["thresholds"]
    assert payload["thresholds"]["hallucination"]["higher_is_better"] is False
