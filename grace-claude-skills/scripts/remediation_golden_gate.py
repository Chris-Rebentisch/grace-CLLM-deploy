#!/usr/bin/env python3
"""grace-gap-remediation-harness — GOLDEN GATE. Domain-agnostic invariants that
MUST hold on healthy current code: the four-co-signal rubric accepts a grounded +
well-formed proposal, and REJECTS each failure mode via a DIFFERENT co-signal
(malformed-but-grounded -> well-formedness; well-formed-but-ungrounded ->
groundedness), credits abstention, and keeps the gated co-signals (closure,
non-regression) explicitly deferred. A heat breach, a scorer regression, or a
KGCL-grammar drift fails a gate.

HEAT-FREE: Claude proposes at the edge (in-loop); `change_executor parse` is pure;
the faithfulness scorer is pure string analysis. No get_provider(), no local model.

  python3 remediation_golden_gate.py
  python3 remediation_golden_gate.py --json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import remediation_score as rs  # noqa: E402

_GEN = ("gpt-oss", "llama", "mixtral", "mistral", "gemma")  # qwen allowed (4.7GB)

# A small, self-contained evidence fixture (mirrors a real Signal-B orphan pair +
# source text). Domain-agnostic shape; nothing hardcoded into the scorer.
EVIDENCE = (
    "SIGNAL B orphan pair: Acme Holdings [Party] <-> Beacon Trust [Party], chunk c1.\n"
    "SOURCE TEXT: 'Acme Holdings (the \"Company\") and Beacon Trust (the \"Trustee\") are "
    "parties to this Agreement; the Company and the Trustee shall act jointly.'\n"
    "ACTIVE ONTOLOGY: types Party, Agreement. No relationship connects two Party entities."
)
GROUNDED_RATIONALE = (
    "Acme Holdings (the Company) and Beacon Trust (the Trustee) are named as the two "
    "parties to the same Agreement and act jointly, so the ontology needs a relationship "
    "type connecting these affiliated Party entities."
)
UNGROUNDED_RATIONALE = (
    "Acme Holdings and Beacon Trust are fierce competitors fighting over the Frankfurt "
    "derivatives market, so they need a competes_with relationship."
)
ABSTAIN_RATIONALE = (
    "The evidence shows only that these two parties co-occur; there is insufficient "
    "evidence to name the specific relationship type between them."
)


def _ollama_clean() -> tuple[bool, str]:
    try:
        out = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=10).stdout
    except Exception as e:  # noqa: BLE001
        return True, f"ollama ps unavailable ({e})"
    loaded = [ln for ln in out.splitlines()[1:] if ln.strip()]
    bad = [ln.split()[0] for ln in loaded if any(g in ln.lower() for g in _GEN)]
    return (not bad), (f"generation model loaded: {bad}" if bad else "heat 0")


def run_gates() -> dict:
    gates: list[dict] = []
    audit: list[str] = []

    def g(name, ok, detail):
        gates.append({"gate": name, "pass": bool(ok), "detail": detail})

    clean, msg = _ollama_clean()
    g("GATE-1 heat 0 (initial)", clean, msg)

    # GATE-2 grounded + well-formed -> ACCEPT
    a = rs.score("create relationship 'affiliated_with'", GROUNDED_RATIONALE, EVIDENCE,
                 "what relationship connects the parties?")
    g("GATE-2 grounded+well-formed accepts",
      a["core_verdict"] == "accept" and a["groundedness"]["verdict"] == "faithful"
      and a["well_formedness"]["well_formed"],
      f"verdict={a['core_verdict']} grounded={a['groundedness']['verdict']} "
      f"wf={a['well_formedness']['well_formed']}")

    # GATE-3 malformed-but-grounded -> REJECT on WELL-FORMEDNESS (the signal_mapping baseline)
    b = rs.score("create edge related_to between Acme Holdings and RelatedType",
                 GROUNDED_RATIONALE, EVIDENCE)
    g("GATE-3 malformed baseline rejected on well-formedness",
      b["core_verdict"] == "reject" and not b["well_formedness"]["well_formed"]
      and b["groundedness"]["verdict"] == "faithful",
      f"verdict={b['core_verdict']} wf={b['well_formedness']['well_formed']} "
      f"(grounded={b['groundedness']['verdict']} — still passes groundedness)")

    # GATE-4 well-formed-but-ungrounded -> REJECT on GROUNDEDNESS
    c = rs.score("create relationship 'competes_with'", UNGROUNDED_RATIONALE, EVIDENCE)
    g("GATE-4 ungrounded proposal rejected on groundedness",
      c["core_verdict"] == "reject" and c["well_formedness"]["well_formed"]
      and c["groundedness"]["verdict"] == "unfaithful"
      and c["groundedness"]["hallucinated_tokens"],
      f"verdict={c['core_verdict']} wf={c['well_formedness']['well_formed']} "
      f"grounded={c['groundedness']['verdict']} flagged={c['groundedness']['hallucinated_tokens']}")

    # GATE-5 "neither co-signal alone suffices": the two rejects fail on DIFFERENT axes
    g("GATE-5 failure modes caught by different co-signals",
      (not b["well_formedness"]["well_formed"] and b["groundedness"]["verdict"] == "faithful")
      and (c["well_formedness"]["well_formed"] and c["groundedness"]["verdict"] == "unfaithful"),
      "malformed-baseline fails ONLY well-formedness; ungrounded fails ONLY groundedness "
      "-> a single headline score would miss one of them")

    # GATE-6 abstention credited (thin evidence -> faithful refusal, not invention)
    d = rs.score("create relationship 'affiliated_with'", ABSTAIN_RATIONALE, EVIDENCE,
                 expect_abstain=True)
    g("GATE-6 abstention credited as grounded",
      d["core_verdict"] == "accept" and d["groundedness"]["abstained"],
      f"verdict={d['core_verdict']} abstained={d['groundedness']['abstained']}")

    # GATE-7 placeholder mechanical-default name flagged
    e = rs.score("create relationship 'RelatedType'", GROUNDED_RATIONALE, EVIDENCE)
    g("GATE-7 mechanical placeholder name flagged",
      e["well_formedness"].get("placeholder_name") and e["core_verdict"] == "reject",
      f"placeholder={e['well_formedness'].get('placeholder_name')} verdict={e['core_verdict']}")

    # GATE-8 co-signals 3 & 4 are kept OUT of the heat-free scorer (built as apply_probe.py),
    # never silently green inside this scorer.
    g("GATE-8 closure + non-regression delegated to apply_probe (not silently green)",
      "apply_probe" in a["gap_closure"]["status"] and "apply_probe" in a["non_regression"]["status"],
      "gap_closure + non_regression point to the built qwen-gated apply_probe.py — the "
      "heat-free scorer never reports them as passed")

    # GATE-9 groundedness is RELATIVE to provided evidence (the A2 bounded-by-upstream lesson):
    # the SAME proposal+rationale is grounded against rich evidence, ungrounded against empty.
    g_rich = rs.score("create relationship 'affiliated_with'", GROUNDED_RATIONALE, EVIDENCE)
    g_empty = rs.score("create relationship 'affiliated_with'", GROUNDED_RATIONALE,
                       "ACTIVE ONTOLOGY: types Party, Agreement.")
    g("GATE-9 groundedness is context-relative (bounded by upstream evidence)",
      g_rich["groundedness"]["verdict"] == "faithful"
      and g_empty["groundedness"]["verdict"] == "unfaithful",
      f"same rationale: vs-evidence={g_rich['groundedness']['verdict']} "
      f"vs-empty={g_empty['groundedness']['verdict']}")

    # GATE-11 (F-RM1 regression, swarm 2026-06-22): a grounded rationale that NAMES the
    # coined relationship as a token must still ACCEPT — the proposed element is new (not in
    # evidence) and must not self-penalize as a hallucination. Pre-fix this scored REJECT
    # (the 'affiliated_with' token was flagged ungrounded).
    named = rs.score("create relationship 'affiliated_with'",
                     "Acme Holdings and Beacon Trust act jointly as the parties to this "
                     "Agreement, so an affiliated_with relationship should connect them.",
                     EVIDENCE)
    g("GATE-11 proposed name in rationale not self-penalized (F-RM1)",
      named["core_verdict"] == "accept" and named["groundedness"]["verdict"] == "faithful"
      and "id:affiliated_with" not in named["groundedness"]["hallucinated_tokens"],
      f"verdict={named['core_verdict']} grounded={named['groundedness']['verdict']} "
      f"(coined name 'affiliated_with' grounded-by-construction)")

    clean2, msg2 = _ollama_clean()
    g("GATE-10 heat 0 (final)", clean2, msg2)

    # ---- AUDIT ----
    audit.append(
        "FIXED (D534, 2026-06-22): signal_mapping.py emitted non-well-formed KGCL for 5 of 8 "
        "branches (B/F-rel 'create edge … between …', C 'change property … on …', E 'change "
        "domain of … from …', F-prop 'create property … on …') — change_executor.parse "
        "rejected them, so those proposals could never apply. Now matches the canonical "
        "kgcl_generator.py forms with quoted names; guarded by "
        "tests/ontology/test_signal_mapping.py::test_all_templates_parse. The well-formedness "
        "co-signal (this harness) surfaced it; GATE-3 still proves the scorer catches a "
        "malformed proposal regardless of source.")
    audit.append(
        "Co-signals 3 (gap-closure) & 4 (non-regression) are DEFERRED: both need "
        "change_executor apply, which does graph DDL and runs the CQ non-regression gate "
        "via get_provider() -> run on qwen2.5:7b (NEVER the 70B). Per-detector gap-closure "
        "is multi-step (e.g. Signal B's orphan pair lives in extraction_claims; adding a "
        "relationship TYPE does not retroactively remove the orphan — closure needs "
        "re-extraction). Build as a sandbox follow-on with its own heat check.")
    audit.append(
        "Groundedness is RELATIVE to the provided evidence and BOUNDED by the upstream signal "
        "(GATE-9): a proposal is only as good as the signal evidence + source chunks the "
        "harness feeds Claude. Thin evidence -> the faithful output is abstention, not a "
        "confident invention (GATE-6). This mirrors the A2 retrieval-bounds-regeneration lesson.")

    npass = sum(1 for x in gates if x["pass"])
    return {"gates": gates, "passed": npass, "total": len(gates), "audit": audit}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    res = run_gates()
    if args.json:
        print(json.dumps(res, indent=2, default=str))
        sys.exit(0 if res["passed"] == res["total"] else 1)
    print(f"REMEDIATION GOLDEN GATE — {res['passed']}/{res['total']} PASS\n")
    for x in res["gates"]:
        print(f"  [{'✓PASS' if x['pass'] else '✗FAIL'}] {x['gate']}")
        print(f"          {x['detail']}")
    print("\n--- AUDIT (in-tree candidates + deferred co-signals, informational) ---")
    for a in res["audit"]:
        print(f"  • {a}")
    sys.exit(0 if res["passed"] == res["total"] else 1)


if __name__ == "__main__":
    main()
