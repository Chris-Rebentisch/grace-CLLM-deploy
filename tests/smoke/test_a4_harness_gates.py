"""A4 correlation harness golden gates as pytest smoke tests (2026-06-22).

Wires the grace-correlation-probe golden gate + the Claude-as-correlation-reasoner
scorer contract (under ``grace-claude-skills/scripts/``, committed into this repo)
into the test suite so the deterministic CORRELATION-pattern detection contract —
and the D535 ``ontology_constraint_conflict`` pattern it guards — are
regression-protected.

Layering (three-tier test model):
  • correlation golden gate -> ``@pytest.mark.smoke`` (heat-free; opt-in via -m smoke;
    needs Postgres grace_test + the engine CLI).
  • reasoner scorer contract -> plain unit test (Tier 1, no DB/services): proves the
    co-signal rubric grounds a faithful diagnosis and FAILS a hallucinated / wrong /
    incomplete one — the diagnostic-groundedness analogue of A2 faithfulness.

The gate seeds the ``grace_test`` SANDBOX only (conftest isolation redirects
``DATABASE_URL`` to the ``_test`` sibling, which the subprocess inherits) and cleans
its own ``a4probe`` markers. It never touches the live ``grace`` GOLD corpus, and
asserts GOLD ``diagnostic_records`` is unchanged (GATE-12).

Run locally:   python -m pytest tests/smoke/test_a4_harness_gates.py -m smoke -v
Excluded from the default suite by ``addopts = -m "not perf and not smoke"`` (the
scorer unit test below has no smoke marker, so it runs in the default suite).
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
    path = GATE_DIR / script
    assert path.exists(), f"gate script missing: {path}"
    env = dict(os.environ, GRACE_ROOT=str(REPO_ROOT))
    return subprocess.run(
        [sys.executable, str(path)],
        capture_output=True, text=True, cwd=str(REPO_ROOT), env=env, timeout=timeout,
    )


@pytest.mark.smoke
def test_correlation_golden_gate():
    """grace-correlation-probe: detection fidelity (recall/precision/substrate-honesty),
    the D535 6th pattern, Prometheus-gated no-op honesty, GOLD-untouched. Heat-free."""
    result = _run_gate("correlation_golden_gate.py", timeout=180)
    assert result.returncode == 0, (
        f"gate failed (exit={result.returncode})\n"
        f"stdout:\n{result.stdout[-2000:]}\nstderr:\n{result.stderr[-1000:]}"
    )
    assert "PASS" in result.stdout, f"no PASS line:\n{result.stdout[-1000:]}"


# ---- hermetic reasoner-scorer contract (no DB / services) ----

def _load_scorer():
    if str(GATE_DIR) not in sys.path:
        sys.path.insert(0, str(GATE_DIR))
    import correlation_score as cs  # noqa: E402
    return cs


_COMPOSE = {
    "context": (
        "module 'mod_x': Signal C strength 0.80, Signal D strength 0.70. "
        "module 'mod_y': Signal A strength 0.55. "
        "module 'mod_unc': Signal A strength 0.70, Signal C strength 0.65."
    ),
    "bundle": {
        "modules": {
            "mod_x": [
                {"signal": "C", "strength": 0.80, "evidence": {}},
                {"signal": "D", "strength": 0.70, "evidence": {}},
            ],
            "mod_y": [{"signal": "A", "strength": 0.55, "evidence": {}}],
            "mod_unc": [{"signal": "A", "strength": 0.70, "evidence": {}},
                        {"signal": "C", "strength": 0.65, "evidence": {}}],
        }
    },
    "engine_diagnoses": [
        {"pattern": "schema_drift_per_module", "module": "mod_x",
         "root_cause": "ontology", "candidate_root_causes": ["ontology"],
         "strength": 0.75},
    ],
}


def test_scorer_passes_grounded_consistent_diagnosis():
    cs = _load_scorer()
    good = {
        "diagnoses": [
            {"module": "mod_x", "root_cause": "ontology", "band": "high",
             "cited_signals": ["C", "D"],
             "rationale": "module mod_x shows Signal C strength 0.80 and Signal D strength 0.70."},
        ],
        "abstentions": ["mod_y"],
    }
    rep = cs.score(good, _COMPOSE)
    assert rep["overall_verdict"] == "pass", rep["verdict_reasons"]
    assert rep["groundedness"] == 1.0
    assert rep["consistency_vs_engine"] == 1.0
    assert "mod_y" in rep["both_silent_no_cry_wolf"]


def test_scorer_fails_hallucinated_and_missed_diagnosis():
    cs = _load_scorer()
    bad = {
        "diagnoses": [
            {"module": "mod_ghost", "root_cause": "graph", "band": "high",
             "cited_signals": ["Z"], "rationale": "Phantom drift at 0.99."},
        ],
        "abstentions": ["mod_x"],  # engine fires mod_x -> a MISS
    }
    rep = cs.score(bad, _COMPOSE)
    assert rep["overall_verdict"] == "fail"
    assert rep["groundedness"] < 1.0
    assert any(m["module"] == "mod_x" for m in rep["missed_engine_fires"])


def test_scorer_credits_grounded_multisignal_richer_than_engine():
    """A grounded MULTI-signal diagnosis the engine misses is RICHER, not a failure."""
    cs = _load_scorer()
    richer = {
        "diagnoses": [
            {"module": "mod_x", "root_cause": "ontology", "band": "high",
             "cited_signals": ["C", "D"],
             "rationale": "module mod_x shows Signal C strength 0.80 and Signal D strength 0.70."},
            {"module": "mod_unc", "root_cause": "ontology", "band": "medium",
             "cited_signals": ["A", "C"],
             "rationale": "module mod_unc shows Signal A strength 0.70 and Signal C strength 0.65."},
        ],
        "abstentions": ["mod_y"],
    }
    rep = cs.score(richer, _COMPOSE)
    assert rep["overall_verdict"] == "pass", rep["verdict_reasons"]
    assert any(r["module"] == "mod_unc" for r in rep["claude_richer_than_engine"])


def test_scorer_single_signal_fire_is_not_richer(_=None):
    """Fix 2 (blind spot B): a grounded SINGLE-signal fire where the engine is silent
    is an aggressiveness flag, NOT richness — must not be credited as richer."""
    cs = _load_scorer()
    over = {
        "diagnoses": [
            {"module": "mod_x", "root_cause": "ontology", "band": "high",
             "cited_signals": ["C", "D"],
             "rationale": "module mod_x shows Signal C strength 0.80 and Signal D strength 0.70."},
            {"module": "mod_y", "root_cause": "extraction", "band": "high",
             "cited_signals": ["A"],
             "rationale": "module mod_y shows Signal A strength 0.55."},
        ],
        "abstentions": ["mod_unc"],
    }
    rep = cs.score(over, _COMPOSE)
    assert not any(r["module"] == "mod_y" for r in rep["claude_richer_than_engine"])
    assert any(r["module"] == "mod_y" for r in rep["single_signal_fires"])


def test_scorer_credits_agreement_with_engine_candidate_root_cause():
    """Fix 3a: a boundary pattern emits candidate_root_causes; a reasoner picking any
    candidate AGREES, not disagrees."""
    cs = _load_scorer()
    compose = {**_COMPOSE, "engine_diagnoses": [
        {"pattern": "ontology_constraint_conflict", "module": "mod_x",
         "root_cause": "ontology", "candidate_root_causes": ["ontology", "extraction"],
         "strength": 0.7}]}
    diag = {"diagnoses": [
        {"module": "mod_x", "root_cause": "extraction", "band": "medium",
         "cited_signals": ["C", "D"],
         "rationale": "module mod_x shows Signal C strength 0.80 and Signal D strength 0.70."}],
        "abstentions": ["mod_y", "mod_unc"]}
    rep = cs.score(diag, compose)
    assert rep["consistency_vs_engine"] == 1.0, rep["disagree"]
    assert rep["overall_verdict"] == "pass"
