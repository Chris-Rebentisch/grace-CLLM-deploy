#!/usr/bin/env python3
"""grace-correlation-probe — GOLDEN GATE. Domain-agnostic invariants that MUST hold
on healthy current code: the deterministic correlation patterns fire on seeded
signal COMBINATIONS (RECALL), stay quiet when a conjunction is unsatisfied
(PRECISION), abstain on thin/uncorrelated signal sets (NO CRY-WOLF), and never
false-fire on empty `analytics_signals` (SUBSTRATE HONESTY). Prometheus-gated
patterns no-op cleanly when telemetry is absent (reported, not failed). A heat
breach, a pattern regression, or a broken seeder/probe fails a gate.

HEAT-FREE: the correlation engine is LLM-free (grep get_provider
src/analytics/correlation_engine/ -> empty; PromQL/SQL + Mann-Kendall only). The
gate seeds the `grace_test` SANDBOX only (never the live `grace` GOLD corpus) and
tears its fixtures down at the end. GOLD `diagnostic_records` is asserted unchanged.

  python3 correlation_golden_gate.py
  python3 correlation_golden_gate.py --json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import add_grace_to_path, route_logs_to_stderr  # noqa: E402
import correlation_probe as cp  # noqa: E402

HERE = Path(__file__).resolve().parent
_GEN = ("gpt-oss", "llama", "mixtral", "mistral", "gemma")  # qwen allowed; none should load
_PROMETHEUS_GATED = {
    "extraction_quality_problem",
    "graph_or_index_problem",
    "relationship_gap_propagation",
}


def _ollama_clean() -> tuple[bool, str]:
    try:
        out = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=10).stdout
    except Exception as e:  # noqa: BLE001
        return True, f"ollama ps unavailable ({e})"
    loaded = [ln for ln in out.splitlines()[1:] if ln.strip()]
    bad = [ln.split()[0] for ln in loaded if any(g in ln.lower() for g in _GEN)]
    return (not bad), (f"generation model loaded: {bad}" if bad else "heat 0")


def _seed(args: list[str]) -> dict:
    p = subprocess.run([sys.executable, str(HERE / "seed_correlations.py"), *args, "--json"],
                       capture_output=True, text=True, timeout=120)
    try:
        return json.loads(p.stdout)
    except Exception:  # noqa: BLE001
        return {"error": p.stdout[-300:] + p.stderr[-300:]}


def _llm_free() -> tuple[bool, str]:
    root = add_grace_to_path()
    p = subprocess.run(
        ["grep", "-rn", "get_provider", str(root / "src/analytics/correlation_engine")],
        capture_output=True, text=True)
    hits = [ln for ln in p.stdout.splitlines() if ln.strip()]
    return (not hits), ("LLM-free (no get_provider)" if not hits else f"get_provider found: {hits[:3]}")


def _gold_url(test_url: str) -> str:
    """Resolve the GOLD reference. Explicit GRACE_GOLD_URL wins; else derive the
    sibling by stripping the trailing `_test` (the canonical grace/grace_test pair)."""
    import os
    explicit = os.environ.get("GRACE_GOLD_URL")
    if explicit:
        return explicit
    head, _, tail = test_url.rpartition("/")
    return f"{head}/{tail[:-5]}" if tail.endswith("_test") else test_url


def _gold_count(test_url: str) -> int | None:
    """diagnostic_records count on the LIVE GOLD db. Returns None (skip GATE-12,
    not crash) when the GOLD pair is absent — e.g. running on an isolated swarm
    `_test` DB whose `<name>` sibling does not exist (F-A4-2 fix). Honors
    GRACE_GOLD_URL for an explicit GOLD reference."""
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError
    gold = _gold_url(test_url)
    if gold == test_url:
        return None  # not a derivable pair; nothing to compare
    try:
        with cp._engine(gold).begin() as conn:
            return int(conn.execute(text("SELECT count(*) FROM diagnostic_records")).scalar() or 0)
    except OperationalError:
        return None  # GOLD sibling does not exist -> swarm-portable skip


def run_gates() -> dict:
    db_url = cp._resolve_db_url(None)
    if not db_url.rsplit("/", 1)[-1].split("?")[0].endswith("_test"):
        return {"gates": [{"gate": "GATE-0 sandbox-only", "pass": False,
                           "detail": f"refusing to run against {db_url}"}],
                "passed": 0, "total": 1, "audit": []}

    gates: list[dict] = []
    audit: list[str] = []

    def g(name, ok, detail):
        gates.append({"gate": name, "pass": bool(ok), "detail": detail})

    def approx(a, b, tol=0.011):
        return a is not None and abs(float(a) - float(b)) <= tol

    gold_before = _gold_count(db_url)

    # GATE-1 heat 0 (initial)
    g("GATE-1 heat 0 (initial)", *_ollama_clean())

    # GATE-2 LLM-free (the A3 lesson: verify, don't trust)
    g("GATE-2 engine is LLM-free", *_llm_free())

    # ---- SEEDED run: recall + precision + abstention ----
    _seed(["--clean"])
    _seed(["--combination", "all"])
    res = cp.probe(db_url)
    by_module = {d["module"]: d for d in res["diagnostics"]}
    fired_patterns = {d["pattern"] for d in res["diagnostics"]}

    drift = by_module.get("a4probe_drift")
    g("GATE-3 schema_drift recall (C+D -> ontology)",
      drift and drift["pattern"] == "schema_drift_per_module"
      and drift["root_cause"] == "ontology" and approx(drift["strength"], 0.75),
      f"a4probe_drift={drift}")

    g("GATE-4 schema_drift precision (C only -> quiet)",
      "a4probe_healthy" not in by_module,
      f"a4probe_healthy present={('a4probe_healthy' in by_module)} (C without D must not fire)")

    cqreg = by_module.get("a4probe_cqreg")
    g("GATE-5 cq_regression recall (F -> discovery)",
      cqreg and cqreg["pattern"] == "cq_regression_pre_extraction"
      and cqreg["root_cause"] == "discovery" and approx(cqreg["strength"], 0.90),
      f"a4probe_cqreg={cqreg}")

    g("GATE-6 cq_regression precision (F<0.5 -> quiet)",
      "a4probe_lowf" not in by_module,
      f"a4probe_lowf present={('a4probe_lowf' in by_module)} (F=0.3 must not fire)")

    econ = by_module.get("a4probe_econflict")
    g("GATE-7 D535 ontology_constraint_conflict recall (E+B -> ontology)",
      econ and econ["pattern"] == "ontology_constraint_conflict"
      and econ["root_cause"] == "ontology" and approx(econ["strength"], 0.70),
      f"a4probe_econflict={econ}")

    g("GATE-8 D535 precision (E only -> quiet)",
      "a4probe_eonly" not in by_module,
      f"a4probe_eonly present={('a4probe_eonly' in by_module)} (E without B must not fire)")

    g("GATE-9 no cry-wolf (single weak A -> quiet)",
      "a4probe_thin" not in by_module,
      f"a4probe_thin present={('a4probe_thin' in by_module)} (lone weak signal must not fire)")

    # GATE-10 Prometheus-gated honesty: A (thin) + B (econflict) DB signals ARE
    # seeded, but the 3 telemetry-gated patterns must NOT fire without a Prometheus
    # spike (no pushgateway on this box). Substrate-honest no-op, not a false fire.
    leaked = _PROMETHEUS_GATED & fired_patterns
    g("GATE-10 Prometheus-gated patterns no-op (telemetry absent)",
      not leaked,
      f"prometheus-gated patterns that fired without telemetry: {sorted(leaked) or 'none'}")

    # ---- CLEAN run: substrate honesty (empty analytics_signals -> 0 diagnoses) ----
    _seed(["--clean"])
    res2 = cp.probe(db_url)
    g("GATE-11 substrate honesty (empty signals -> 0 diagnoses)",
      res2["n_diagnostics"] == 0,
      f"clean grace_test produced {res2['n_diagnostics']} diagnoses (must be 0)")

    # GATE-12 GOLD untouched across the whole gate. Skips cleanly (pass) when the
    # GOLD pair is absent — e.g. an isolated swarm `_test` DB (F-A4-2 fix): the
    # seed/probe slices only ever touch the sandbox, so there is no GOLD to harm.
    gold_after = _gold_count(db_url)
    if gold_before is None or gold_after is None:
        g("GATE-12 GOLD diagnostic_records untouched",
          True,
          "GOLD pair not present (isolated _test DB) — untouched check skipped; "
          "set GRACE_GOLD_URL to assert against an explicit GOLD db")
    else:
        g("GATE-12 GOLD diagnostic_records untouched",
          gold_after == gold_before,
          f"GOLD diagnostic_records {gold_before} -> {gold_after} (must be unchanged)")

    # GATE-13 heat 0 (final)
    g("GATE-13 heat 0 (final)", *_ollama_clean())

    # ---- AUDIT (informational) ----
    audit.append(
        "Prometheus-gated patterns (extraction_quality_problem, graph_or_index_problem, "
        "relationship_gap_propagation) need current-vs-14d-baseline TSDB telemetry to FIRE. "
        "No pushgateway/remote-write on this box, so a true fire needs timestamp-controlled "
        "backfill (a follow-on harness). The gate proves they correctly NO-OP on absent "
        "substrate (GATE-10), not that they cannot fire.")
    audit.append(
        "On the live `grace` GOLD corpus analytics_signals is EMPTY (all six signal "
        "detectors no-op on GOLD — see grace-signal-probe), so the engine emits 0 diagnoses "
        "there. Correct substrate behaviour: detection fidelity is proven against the "
        "grace_test sandbox seeded with signal combinations.")
    audit.append(
        "D535 (this session): ontology_constraint_conflict (Signal E domain/range violation + "
        "Signal B missing-edge, same module -> ontology) is the 6th pattern, surfaced by running "
        "Claude AS the correlation reasoner over the same D252 inputs — a cross-signal root cause "
        "the static 5-pattern library missed. Wired deterministically, amends D250.")

    npass = sum(1 for x in gates if x["pass"])
    return {"gates": gates, "passed": npass, "total": len(gates), "audit": audit}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    add_grace_to_path()
    route_logs_to_stderr(quiet=True)
    res = run_gates()
    if args.json:
        print(json.dumps(res, indent=2, default=str))
        sys.exit(0 if res["passed"] == res["total"] else 1)
    print(f"CORRELATION GOLDEN GATE — {res['passed']}/{res['total']} PASS\n")
    for x in res["gates"]:
        print(f"  [{'✓PASS' if x['pass'] else '✗FAIL'}] {x['gate']}")
        print(f"          {x['detail']}")
    print("\n--- AUDIT (known substrate limits + cross-findings, informational) ---")
    for a in res["audit"]:
        print(f"  • {a}")
    sys.exit(0 if res["passed"] == res["total"] else 1)


if __name__ == "__main__":
    main()
