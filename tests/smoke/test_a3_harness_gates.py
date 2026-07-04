"""A3 harness golden gates as pytest smoke tests (2026-06-22).

Wires the grace-signal-probe + grace-gap-remediation-harness golden gates
(under ``grace-claude-skills/scripts/``, committed into this repo) into the test
suite so the deterministic gap-detection + remediation-scoring contracts — and the
D534 signal_mapping fix they guard — are regression-protected.

Layering (three-tier test model):
  • signal + remediation gates  -> ``@pytest.mark.smoke`` (heat-free; opt-in via -m smoke).
  • apply gate                  -> ``@pytest.mark.smoke`` + ``@pytest.mark.requires_ollama``
                                   (deliberately loads qwen2.5:7b; auto-skips when Ollama absent,
                                   e.g. in CI).

The gates seed the ``grace_test`` SANDBOX only (conftest isolation redirects
``DATABASE_URL`` to the ``_test`` sibling, which the subprocess inherits) and clean
their own markers. They never touch the live ``grace`` GOLD corpus.

Run locally:   python -m pytest tests/smoke/test_a3_harness_gates.py -m smoke -v
Excluded from the default suite by ``addopts = -m "not perf and not smoke"``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_DIR = REPO_ROOT / "grace-claude-skills" / "scripts"


def _run_gate(script: str, timeout: int) -> subprocess.CompletedProcess:
    """Run a golden-gate script as a subprocess; inherit the conftest-isolated
    DATABASE_URL (-> grace_test) and pin GRACE_ROOT for harness portability."""
    path = GATE_DIR / script
    assert path.exists(), f"gate script missing: {path}"
    env = dict(os.environ, GRACE_ROOT=str(REPO_ROOT))
    return subprocess.run(
        [sys.executable, str(path)],
        capture_output=True, text=True, cwd=str(REPO_ROOT), env=env, timeout=timeout,
    )


def _assert_pass(result: subprocess.CompletedProcess) -> None:
    assert result.returncode == 0, (
        f"gate failed (exit={result.returncode})\n"
        f"stdout:\n{result.stdout[-2000:]}\nstderr:\n{result.stderr[-1000:]}"
    )
    assert "PASS" in result.stdout, f"no PASS line in gate output:\n{result.stdout[-1000:]}"


@pytest.mark.smoke
def test_signal_golden_gate():
    """grace-signal-probe: detection fidelity (recall/precision/substrate-honesty). Heat-free."""
    _assert_pass(_run_gate("signal_golden_gate.py", timeout=120))


@pytest.mark.smoke
def test_remediation_golden_gate():
    """grace-gap-remediation-harness: co-signals 1&2 (groundedness + well-formedness). Heat-free."""
    _assert_pass(_run_gate("remediation_golden_gate.py", timeout=60))


@pytest.mark.smoke
@pytest.mark.requires_ollama
def test_apply_golden_gate():
    """Apply follow-on: co-signals 3&4 (closure-readiness + CQ-gate). Loads qwen2.5:7b
    (never the 70B) — auto-skips when Ollama is unavailable."""
    _assert_pass(_run_gate("apply_golden_gate.py", timeout=300))
