#!/usr/bin/env python3
"""grace-gap-remediation-harness — REMEDIATION QUALITY scorer (roadmap A3, goal 2).

Given a fired signal + its evidence, Claude (at the EDGE, in-loop) proposes ONE
KGCL schema change with a one-paragraph RATIONALE grounded in the evidence. This
scorer judges that proposal on FOUR co-signals — never one headline number (the
A2 lesson: a single "looks plausible" score is unsafe).

  1. GROUNDEDNESS   — is the change supported by the evidence, or invented from
                      priors? Computed as faithfulness(rationale, evidence): every
                      salient entity/number the rationale asserts must resolve in
                      the evidence. ABSTENTION ("insufficient evidence to name a
                      specific change") is credited as faithful (thin evidence ->
                      a faithful proposal is a refusal, not a confident invention).
                      Reuses the A2 faithfulness_score wholesale.
  2. WELL-FORMEDNESS — does the KGCL parse? `change_executor parse` (deterministic,
                      heat-free). Also flags mechanical PLACEHOLDER names
                      (RelatedType/UnknownType/...) the deterministic baseline emits.
  3. GAP-CLOSURE    — [DEFERRED] does applying it (sandbox) make the signal stop
                      firing? Needs change_executor apply (graph DDL) -> qwen-gated
                      follow-on. Per-detector closure is multi-step (e.g. B's orphan
                      lives in extraction_claims, not the schema).
  4. NON-REGRESSION — [DEFERRED] does the CQ non-regression gate pass? The CQ-gate
                      calls get_provider() -> runs on qwen2.5:7b (NEVER the 70B) in
                      the gated follow-on.

WHY two co-signals are not enough, made concrete (proven live on real GOLD data):
  • A grounded-but-MALFORMED proposal (the signal_mapping baseline 'create edge ...')
    passes groundedness, fails well-formedness.
  • A well-formed-but-UNGROUNDED proposal ('create relationship competes_with' when
    the source says the parties are affiliates) passes well-formedness, fails
    groundedness.
  Neither co-signal alone catches both. You need both (and, gated, closure+regression).

HEAT-FREE: Claude is the only LLM and it runs in-loop at the edge (no get_provider,
no local model for proposing). `change_executor parse` is pure (no DB/graph/LLM).

Usage:
  python3 remediation_score.py --kgcl "create relationship 'affiliated_with'" \
      --rationale-file r.txt --evidence-file ev.txt --query "..."
  echo "<rationale>" | python3 remediation_score.py --kgcl "..." --evidence-file ev.txt
  python3 remediation_score.py --proposal-json p.json   # {kgcl, rationale, evidence, query}
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import faithfulness_score as fs  # noqa: E402

GRACE_ROOT = os.path.expanduser("~/grace")
# Placeholder names the deterministic signal_mapping.py emits when evidence is thin
# (RelatedType/UnknownType/unknown_property/new_property). A proposal whose target is
# a literal placeholder is a mechanical default, not a grounded change.
_PLACEHOLDERS = {"relatedtype", "unknowntype", "unknown_property", "new_property",
                 "string", "relatedtype'", "newtype"}


def parse_kgcl(kgcl: str) -> dict:
    """Wrap `change_executor parse` (heat-free). Returns well-formedness verdict."""
    proc = subprocess.run(
        [sys.executable, "-m", "src.ontology.change_executor", "parse", kgcl],
        capture_output=True, text=True, cwd=GRACE_ROOT,
        env=dict(os.environ, PYTHONPATH=GRACE_ROOT), timeout=60,
    )
    out = proc.stdout + proc.stderr
    err = re.search(r"Parse error:\s*(.+)", out)
    if err:
        return {"well_formed": False, "command_kind": None, "target_name": None,
                "error": err.group(1).strip()}
    parsed = {}
    try:
        # the CLI prints the ProposedSchemaChange JSON on success
        start = out.find("{")
        if start >= 0:
            parsed = json.loads(out[start: out.rfind("}") + 1])
    except Exception:  # noqa: BLE001
        parsed = {}
    ck = parsed.get("command_kind")
    tname = parsed.get("target_name") or parsed.get("entity_name") or parsed.get("property_name")
    placeholder = bool(tname) and tname.strip().strip("'\"").lower() in _PLACEHOLDERS
    return {"well_formed": bool(ck), "command_kind": ck, "target_name": tname,
            "placeholder_name": placeholder, "error": None}


def score(kgcl: str, rationale: str, evidence: str, query: str = "",
          expect_abstain: bool = False) -> dict:
    # Co-signal 2: well-formedness (deterministic)
    wf = parse_kgcl(kgcl)

    # Co-signal 1: groundedness = faithfulness(rationale, evidence). Credits abstention.
    # F-RM1 (swarm 2026-06-22): the PROPOSED element name is by definition NEW (not yet in
    # the evidence — it is what we are proposing), so naming it in the rationale must NOT
    # self-penalize as a hallucination. Honor D-RM2 ("score the rationale, not the name") by
    # grounding the proposed target_name by construction: append it to the fact set. This does
    # NOT weaken ungrounded-detection — any OTHER invented entity/number in the rationale is
    # still flagged.
    proposed = wf.get("target_name") or ""
    grounding_ctx = f"{evidence}\nProposed new schema element: {proposed}" if proposed else evidence
    gr = fs.score(rationale, grounding_ctx, query, expect_abstain=expect_abstain)
    grounded_verdict = gr["overall_verdict"]  # faithful | abstained | unfaithful

    # Headline (folded, but the per-co-signal fields are the real read):
    # acceptable for the HEAT-FREE CORE iff well-formed AND (grounded or credited abstention)
    # AND the target is not a bare mechanical placeholder.
    grounded_ok = grounded_verdict in ("faithful", "abstained")
    core_pass = bool(wf["well_formed"]) and grounded_ok and not wf.get("placeholder_name")
    reasons = []
    if not wf["well_formed"]:
        reasons.append(f"malformed KGCL ({wf['error']})")
    if wf.get("placeholder_name"):
        reasons.append(f"placeholder target name '{wf['target_name']}' (mechanical default, not grounded)")
    if not grounded_ok:
        reasons.append(f"rationale not grounded ({', '.join(gr['verdict_reasons']) or grounded_verdict})")

    return {
        "kgcl": kgcl,
        "core_verdict": "accept" if core_pass else "reject",
        "core_reasons": reasons,
        # --- co-signal 1: groundedness ---
        "groundedness": {
            "verdict": grounded_verdict,
            "faithfulness": gr["faithfulness"],
            "token_grounding": gr["token_grounding"],
            "hallucinated_tokens": gr["hallucinated_tokens"],
            "abstained": gr["abstains_overall"],
        },
        # --- co-signal 2: well-formedness ---
        "well_formedness": wf,
        # --- co-signals 3 & 4: built as the qwen-gated apply follow-on (apply_probe.py) ---
        # Kept OUT of this heat-free scorer so co-signals 1 & 2 never touch the DB/qwen.
        # Run `apply_probe.py --kgcl "<...>"` for the full 4-co-signal report (loads qwen2.5:7b).
        "gap_closure": {"status": "see apply_probe.py (built)",
                        "note": "co-signal 3 = closure-readiness via apply_probe._closure_readiness "
                                "(HEAT-FREE; schema mutate + element-present assertion). Full "
                                "re-detect loop deferred (orphan lives in extraction_claims)."},
        "non_regression": {"status": "see apply_probe.py (built)",
                           "note": "co-signal 4 = CQ non-regression gate via apply_probe._non_regression "
                                   "(QWEN HEAT — qwen2.5:7b, NEVER the 70B; heat-guarded)."},
    }


def _print(rep: dict) -> None:
    v = rep["core_verdict"]
    flag = "✓ ACCEPT" if v == "accept" else "✗ REJECT"
    print(f"REMEDIATION (heat-free core) {flag}   kgcl: {rep['kgcl']}")
    if rep["core_reasons"]:
        for r in rep["core_reasons"]:
            print(f"    reason: {r}")
    g = rep["groundedness"]
    gf = {"faithful": "✓", "abstained": "✓(abstain)", "unfaithful": "✗"}.get(g["verdict"], "?")
    print(f"  [1] GROUNDEDNESS {gf} {g['verdict']}  faithfulness={g['faithfulness']:.0%} "
          f"token_grounding={g['token_grounding']:.0%}")
    if g["hallucinated_tokens"]:
        print(f"        ungrounded in evidence: {g['hallucinated_tokens']}")
    w = rep["well_formedness"]
    wf = "✓" if w["well_formed"] else "✗"
    ph = "  ⚠ PLACEHOLDER NAME" if w.get("placeholder_name") else ""
    print(f"  [2] WELL-FORMEDNESS {wf} {'parses' if w['well_formed'] else 'REJECTED: ' + (w['error'] or '')}"
          f"  ({w['command_kind']} {w['target_name'] or ''}){ph}")
    print(f"  [3] GAP-CLOSURE      · see apply_probe.py (closure-readiness, heat-free)")
    print(f"  [4] NON-REGRESSION   · see apply_probe.py (CQ-gate on qwen2.5:7b)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kgcl", help="the proposed KGCL command")
    ap.add_argument("--rationale-file", help="Claude's grounding rationale (default stdin)")
    ap.add_argument("--evidence-file", help="evidence context (signal evidence + source chunks + ontology)")
    ap.add_argument("--query", default="", help="the signal/gap framed as a question")
    ap.add_argument("--expect-abstain", action="store_true",
                    help="thin-evidence case: PASS only if the rationale abstains")
    ap.add_argument("--proposal-json", help="{kgcl, rationale, evidence, query} bundle")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.proposal_json:
        d = json.loads(Path(args.proposal_json).read_text())
        kgcl = d["kgcl"]; rationale = d.get("rationale", "")
        evidence = d.get("evidence", ""); query = d.get("query", "")
    else:
        kgcl = args.kgcl
        rationale = (Path(args.rationale_file).read_text() if args.rationale_file
                     else sys.stdin.read())
        evidence = Path(args.evidence_file).read_text() if args.evidence_file else ""
        query = args.query
    if not kgcl:
        raise SystemExit("--kgcl (or --proposal-json) required")

    rep = score(kgcl, rationale, evidence, query, expect_abstain=args.expect_abstain)
    if args.json:
        print(json.dumps(rep, indent=2, default=str))
    else:
        _print(rep)


if __name__ == "__main__":
    main()
