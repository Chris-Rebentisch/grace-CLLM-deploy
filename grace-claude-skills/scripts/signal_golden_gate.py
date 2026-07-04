#!/usr/bin/env python3
"""grace-signal-probe — GOLDEN GATE. Domain-agnostic invariants that MUST hold on
healthy current code: the deterministic detectors fire on a seeded gap (RECALL),
stay quiet on a healthy module (PRECISION), and NEVER false-fire on empty
substrate (SUBSTRATE HONESTY). A heat breach, a detector regression, or a broken
seeder fails a gate. Known/accepted substrate limits (A/C/E need telemetry
history) are reported in an informational AUDIT block, not failed.

HEAT-FREE: the signal pipeline is LLM-free (PromQL/SQL + Mann-Kendall). The gate
seeds the `grace_test` SANDBOX only (never the live `grace` GOLD corpus) and
tears its fixtures down at the end.

  python3 signal_golden_gate.py
  python3 signal_golden_gate.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import add_grace_to_path, route_logs_to_stderr  # noqa: E402
import signal_probe as sp  # noqa: E402

HERE = Path(__file__).resolve().parent
_GEN = ("gpt-oss", "llama", "mixtral", "mistral", "gemma")  # NB: qwen allowed (4.7GB CQ-gate)


def _ollama_clean() -> tuple[bool, str]:
    try:
        out = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=10).stdout
    except Exception as e:  # noqa: BLE001
        return True, f"ollama ps unavailable ({e})"
    loaded = [ln for ln in out.splitlines()[1:] if ln.strip()]
    bad = [ln.split()[0] for ln in loaded if any(g in ln.lower() for g in _GEN)]
    return (not bad), (f"generation model loaded: {bad}" if bad else "heat 0")


def _seed(args: list[str]) -> dict:
    p = subprocess.run([sys.executable, str(HERE / "seed_gaps.py"), *args, "--json"],
                       capture_output=True, text=True, timeout=120)
    try:
        return json.loads(p.stdout)
    except Exception:  # noqa: BLE001
        return {"error": p.stdout[-300:] + p.stderr[-300:]}


def run_gates() -> dict:
    db_url = sp._resolve_db_url(None)
    # F-SP1 (swarm 2026-06-22): guard the documented contract — db name ENDS in
    # `_test` (matches seed_gaps.py / signal_probe.py). The prior startswith("grace_test")
    # wrongly refused valid `_test` siblings (e.g. an isolated grace_swarma_test).
    if not db_url.rsplit("/", 1)[-1].split("?")[0].endswith("_test"):
        return {"gates": [{"gate": "GATE-0 sandbox-only", "pass": False,
                           "detail": f"refusing to run against {db_url} (db name must end in _test)"}],
                "passed": 0, "total": 1, "audit": []}
    gates: list[dict] = []
    audit: list[str] = []

    def g(name, ok, detail):
        gates.append({"gate": name, "pass": bool(ok), "detail": detail})

    # GATE-1 heat 0 (initial)
    clean, msg = _ollama_clean()
    g("GATE-1 heat 0 (initial)", clean, msg)

    # ---- SEEDED run: recall + precision ----
    _seed(["--detector", "all"])
    seeded = sp.probe(db_url, None, None, None)
    by = {(s["signal"], s["module"]): s for s in seeded.get("signals", [])}

    orphan = by.get(("B", "a3probe_orphan"))
    g("GATE-2 B recall (orphan fires 1.0)",
      orphan and orphan["fired"] and orphan["strength"] == 1.0,
      f"a3probe_orphan strength={orphan['strength'] if orphan else None} "
      f"orphans={orphan['evidence'].get('orphan_pairs') if orphan else None}")

    healthy = by.get(("B", "a3probe_healthy"))
    g("GATE-3 B precision (healthy quiet 0.0)",
      healthy is not None and not healthy["fired"] and healthy["strength"] == 0.0,
      f"a3probe_healthy strength={healthy['strength'] if healthy else 'MISSING'}")

    dep = by.get(("D", "a3probe_deprecate"))
    g("GATE-4 D recall (decreasing trend fires)",
      dep and dep["fired"] and dep["trend"] == "decreasing",
      f"a3probe_deprecate strength={dep['strength'] if dep else None} "
      f"trend={dep['trend'] if dep else None}")

    stable_present = ("D", "a3probe_stable") in by
    g("GATE-5 D precision (flat module no record)", not stable_present,
      f"a3probe_stable produced a record={stable_present} (flat series must NOT fire)")

    fsig = by.get(("F", "__global__"))
    g("GATE-6 F recall (increasing failure rate fires)",
      fsig and fsig["fired"] and fsig["trend"] == "increasing",
      f"__global__ strength={fsig['strength'] if fsig else None} "
      f"trend={fsig['trend'] if fsig else None}")

    # GATE-7 emit_threshold is vestigial -> a strength-0 record IS persisted.
    # (The A2 'don't trust config fields' lesson: 'fired' must be read off STRENGTH,
    #  not row-existence — the healthy 0.0 record proves strength-0 still writes.)
    g("GATE-7 strength is the signal (emit_threshold vestigial)",
      healthy is not None and healthy["strength"] == 0.0,
      "healthy module persisted a strength-0.0 record (emit_threshold never filters)")

    # ---- CLEAN run: substrate honesty (no false-fire on empty substrate) ----
    _seed(["--clean"])
    cleaned = sp.probe(db_url, None, None, None)
    n_signals = len(cleaned.get("signals", []))
    g("GATE-8 substrate honesty (clean db -> 0 signals)", n_signals == 0,
      f"clean grace_test produced {n_signals} signals (must be 0 — no false-fire)")

    # GATE-9 A/C/E correctly no-op against live Prometheus (run, don't false-fire)
    ace_noop = {n["signal"] for n in cleaned.get("no_op_substrate", [])} >= {"A", "C", "E"}
    g("GATE-9 A/C/E run + no-op (Prometheus substrate absent)", ace_noop,
      "A/C/E produced no record and no error (correct no-op, not a false fire)")

    # GATE-10 heat 0 (final)
    clean2, msg2 = _ollama_clean()
    g("GATE-10 heat 0 (final)", clean2, msg2)

    # ---- AUDIT (known/accepted substrate limits + cross-findings; informational) ----
    pr = _seed(["--prometheus"]) if False else None  # avoid double subprocess; describe inline
    audit.append(
        "A/C/E firing-validation deferred: these read Prometheus (current vs 14-day "
        "baseline, sigma/rate). No pushgateway/remote-write ingress on this box, and a "
        "single synthetic sample cannot create the sigma separation — a true fire needs "
        "timestamp-controlled TSDB backfill (a follow-on harness). The probe proves they "
        "RUN and correctly NO-OP on absent substrate (GATE-9).")
    audit.append(
        "Mann-Kendall validity: D/F need >= mann_kendall_min_points (10) ordered points "
        "(days for D, completed runs for F); shorter series correctly skip emission (R4/R5). "
        "The seeder lays down 12 points to clear the floor.")
    audit.append(
        "Cross-finding (FIXED D534, 2026-06-22): signal_mapping.py emitted non-well-formed "
        "KGCL for 5 of 8 branches (B/C/E/F-rel/F-prop) — change_executor.parse rejected them. "
        "Now matches canonical kgcl_generator.py forms with quoted names; guarded by "
        "tests/ontology/test_signal_mapping.py::test_all_templates_parse. Surfaced by the "
        "grace-gap-remediation-harness well-formedness co-signal.")
    audit.append(
        "On the live `grace` GOLD corpus ALL SIX detectors no-op (extraction_claims "
        "single-day + ontology_module NULL; cq_test_runs empty; Prometheus grace_* empty). "
        "This is correct substrate behaviour, not a detector defect — detection fidelity is "
        "proven here against the grace_test sandbox.")

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
    print(f"SIGNAL GOLDEN GATE — {res['passed']}/{res['total']} PASS\n")
    for x in res["gates"]:
        print(f"  [{'✓PASS' if x['pass'] else '✗FAIL'}] {x['gate']}")
        print(f"          {x['detail']}")
    print("\n--- AUDIT (known substrate limits + cross-findings, informational) ---")
    for a in res["audit"]:
        print(f"  • {a}")
    sys.exit(0 if res["passed"] == res["total"] else 1)


if __name__ == "__main__":
    main()
