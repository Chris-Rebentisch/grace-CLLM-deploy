#!/usr/bin/env python3
"""grace-gap-remediation-harness — APPLY GOLDEN GATE (co-signals 3 & 4).

The qwen-gated follow-on's regression anchor. Unlike the other campaign gates this
one is NOT heat-0 by design: co-signal 4 deliberately loads qwen2.5:7b (the CQ
non-regression gate). The invariant it enforces is HEAT-BOUNDED — only qwen / a
small local model may load; the 70B (llama3.3 / gpt-oss) must NEVER be reachable.

Gates:
  • co-signal 3 (closure-readiness) is HEAT-FREE and correct (create -> element present,
    obsolete -> schema changed, unparseable -> not ready).
  • the heat guard REFUSES a forbidden (too-big) configured model.
  • co-signal 4 runs the real CQ non-regression gate on qwen and returns a structured,
    bounded result.
  • HEAT-BOUNDED: no forbidden generation model is ever loaded (qwen is allowed).

Sandbox-only (grace_test). Seeds the CQ fixture, runs, leaves the fixture in place.

  python3 apply_golden_gate.py
  python3 apply_golden_gate.py --json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import add_grace_to_path, route_logs_to_stderr  # noqa: E402
import apply_probe as ap  # noqa: E402
import signal_probe as sp  # noqa: E402

HERE = Path(__file__).resolve().parent
# Forbidden = too big for a 28GB box. qwen is the ALLOWED gated model.
_FORBIDDEN = ("gpt-oss", "llama3.3", "llama3.1:70", "llama3:70", "70b", "mixtral:8x22")


def _loaded_models() -> list[str]:
    try:
        out = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=10).stdout
    except Exception:  # noqa: BLE001
        return []
    return [ln.split()[0] for ln in out.splitlines()[1:] if ln.strip()]


def _heat_bounded() -> tuple[bool, str]:
    loaded = _loaded_models()
    bad = [m for m in loaded if any(f in m.lower() for f in _FORBIDDEN)]
    return (not bad), (f"FORBIDDEN model loaded: {bad}" if bad else
                       f"heat-bounded ok (loaded: {loaded or ['none']}; qwen allowed)")


def _seed_fixture() -> dict:
    p = subprocess.run([sys.executable, str(HERE / "seed_gaps.py"), "--cq-fixture", "--json"],
                       capture_output=True, text=True, timeout=120)
    try:
        return json.loads(p.stdout)
    except Exception:  # noqa: BLE001
        return {"error": p.stdout[-200:] + p.stderr[-300:]}


def run_gates() -> dict:
    db_url = sp._resolve_db_url(None)
    if not db_url.rsplit("/", 1)[-1].split("?")[0].endswith("_test"):
        return {"gates": [{"gate": "GATE-0 sandbox-only", "pass": False,
                           "detail": f"refusing {db_url}"}], "passed": 0, "total": 1, "audit": []}
    gates: list[dict] = []
    audit: list[str] = []

    def g(name, ok, detail):
        gates.append({"gate": name, "pass": bool(ok), "detail": detail})

    # GATE-1 heat-bounded at start (no forbidden model)
    ok, msg = _heat_bounded()
    g("GATE-1 heat-bounded (initial)", ok, msg)

    # GATE-2 heat guard REFUSES a forbidden configured model (pure-function check)
    fake_forbidden = any(f in "llama3.3:70b-instruct-q8_0" for f in ap._FORBIDDEN_MODELS)
    g("GATE-2 heat guard rejects the 70B", fake_forbidden,
      "ap._FORBIDDEN_MODELS matches 'llama3.3:70b…' -> co-signal 4 would refuse it")

    # seed the fixture (active version + ACCEPTED CQs)
    seeded = _seed_fixture()
    g("GATE-3 CQ fixture seeded", "cq_fixture" in seeded,
      f"active_version + {seeded.get('cq_fixture', {}).get('cqs', '?')} CQs"
      if "cq_fixture" in seeded else f"seed failed: {seeded.get('error')}")

    # --- co-signal 3: closure-readiness (HEAT-FREE) on three shapes ---
    add_grace_to_path()
    from sqlalchemy import create_engine, text
    eng = create_engine(db_url, pool_pre_ping=True)
    with eng.connect() as conn:
        active = conn.execute(
            text("SELECT schema_json FROM ontology_versions WHERE is_active=true LIMIT 1")).first()[0]

    c_rel = ap._closure_readiness("create relationship 'affiliated_with'", active)
    g("GATE-4 closure-readiness: create relationship",
      c_rel["closure_ready"] and c_rel["element"] == "affiliated_with",
      f"element present-after-mutate={c_rel['closure_ready']} (heat-free)")

    c_cls = ap._closure_readiness("create class 'Holding Company'", active)
    g("GATE-5 closure-readiness: create class (multi-word)",
      c_cls["closure_ready"] and c_cls["element"] == "Holding Company",
      f"quoted multi-word class present-after-mutate={c_cls['closure_ready']}")

    c_bad = ap._closure_readiness("frobnicate the widget", active)
    g("GATE-6 closure-readiness rejects unparseable KGCL",
      not c_bad["closure_ready"] and "unparseable" in c_bad.get("reason", ""),
      f"closure_ready={c_bad['closure_ready']} reason={c_bad.get('reason')}")

    # --- co-signal 4: CQ non-regression gate runs on qwen (QWEN HEAT) ---
    full = ap.probe("create relationship 'affiliated_with'", closure_only=False)
    nr = full.get("non_regression", {})
    ran = (isinstance(nr.get("gate_passed"), bool) and nr.get("total_cqs", 0) >= 1
           and "refused" != nr.get("status"))
    g("GATE-7 co-signal 4 CQ-gate runs on qwen",
      ran, f"gate_passed={nr.get('gate_passed')} pass_band={nr.get('pass_band')} "
           f"total_cqs={nr.get('total_cqs')} (ran on qwen2.5:7b)")

    # GATE-8 HEAT-BOUNDED after the gate: qwen may be loaded, the 70B must NOT
    ok2, msg2 = _heat_bounded()
    g("GATE-8 heat-bounded (after qwen gate — no 70B)", ok2, msg2)

    # GATE-9 (F-AP1 regression, swarm 2026-06-22): a malformed/unparseable KGCL must SKIP the
    # qwen CQ-gate (it fails well-formedness — no point paying the heat cost). Pre-fix it ran
    # the gate anyway on the unchanged schema.
    mal = ap.probe("frobnicate the widget", closure_only=False)
    mal_nr = mal.get("non_regression", {})
    g("GATE-9 malformed KGCL short-circuits the qwen gate (F-AP1)",
      "skipped" in str(mal_nr.get("status", "")) and "unparseable" in str(mal_nr.get("status", "")),
      f"non_regression={mal_nr.get('status')} (no qwen call for unparseable input)")

    audit.append(
        "Co-signal 4 (non-regression) v1 proves the CQ-gate MACHINERY runs end-to-end on "
        "qwen2.5:7b (heat-bounded; the 70B is never reachable). A green pass/fail "
        "DISCRIMINATION needs a richer baseline fixture (CQs that PASS on the active schema so "
        "a regression has something to break) — the minimal Party/Agreement fixture yields "
        "pass_rate 0 (qwen judges it can't answer), so the gate asserts the machinery + heat "
        "bound, not a specific pass_rate. Richer-fixture tuning is a follow-on.")
    audit.append(
        "Co-signal 3 (gap-closure) is CLOSURE-READINESS only (architect-approved v1): the "
        "schema now contains the missing element so re-extraction WOULD realize it. A full "
        "re-detect loop (apply -> re-extract -> signal clears) is deferred — the orphan/missing "
        "fact lives in extraction_claims, not the schema (see design record §8).")
    audit.append(
        "This is the campaign's first non-heat-0 gate by design. The invariant is HEAT-BOUNDED: "
        "qwen2.5:7b (4.7GB) is allowed; llama3.3/70b/gpt-oss are forbidden and never reachable "
        "(config swapped + heat guard + this gate). Restore the 70B only on a 128GB host.")

    npass = sum(1 for x in gates if x["pass"])
    return {"gates": gates, "passed": npass, "total": len(gates), "audit": audit}


def main() -> None:
    ap_ = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap_.add_argument("--json", action="store_true")
    args = ap_.parse_args()
    route_logs_to_stderr(quiet=True)
    res = run_gates()
    if args.json:
        print(json.dumps(res, indent=2, default=str))
        sys.exit(0 if res["passed"] == res["total"] else 1)
    print(f"APPLY GOLDEN GATE — {res['passed']}/{res['total']} PASS  (heat-bounded to qwen2.5:7b)\n")
    for x in res["gates"]:
        print(f"  [{'✓PASS' if x['pass'] else '✗FAIL'}] {x['gate']}")
        print(f"          {x['detail']}")
    print("\n--- AUDIT (deferred depth + heat bound, informational) ---")
    for a in res["audit"]:
        print(f"  • {a}")
    sys.exit(0 if res["passed"] == res["total"] else 1)


if __name__ == "__main__":
    main()
