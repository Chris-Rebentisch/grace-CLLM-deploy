#!/usr/bin/env python3
"""grace-correlation-probe — DIAGNOSTIC GROUNDEDNESS scorer for the
Claude-as-correlation-reasoner harness (A4 goal 2/3). The faithfulness analogue,
one layer up: a Claude diagnosis is only as good as the fired signals + evidence it
is grounded in. A single headline number is unsafe (the A2/A3 lesson), so this
emits a CO-SIGNAL panel — never one score:

  • groundedness  — every diagnosis cites signals/modules present in the input
    bundle, names a valid root-cause module, and its rationale tokens resolve in
    the context (reuses faithfulness_score). Abstention is grounded by construction.
  • pattern-validity — root_cause ∈ {extraction, retrieval, graph, ontology,
    discovery}; cited signals exist for the module.
  • consistency   — per-module agreement with the deterministic engine on
    fire-vs-silence and root cause.
  • richness      — modules Claude diagnoses that the engine MISSES (Claude finds
    more) — validated as grounded, credited not penalized (the D535-class gap).
  • no-cry-wolf   — modules where both Claude and the engine stay silent.

A MISS (engine fired, Claude abstained) or a DISAGREE (both fire, different root
cause) fails the verdict. A grounded richer-than-engine diagnosis does not.

Inputs:
  --diagnosis  claude.json   {"diagnoses":[{module,root_cause,band,cited_signals,
                              rationale}], "abstentions":[module,...]}
  --compose-json bundle.json  output of correlation_compose.py --json --include-engine

  python3 correlation_score.py --diagnosis claude.json --compose-json bundle.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import faithfulness_score as fs  # noqa: E402

VALID_ROOT_CAUSES = {"extraction", "retrieval", "graph", "ontology", "discovery"}
VALID_BANDS = {"low", "medium", "high"}


def score(diagnosis: dict, compose: dict) -> dict:
    bundle = compose.get("bundle", {}).get("modules", {})
    context = compose.get("context", "")
    engine = {d["module"]: d for d in compose.get("engine_diagnoses", [])}

    diagnoses = diagnosis.get("diagnoses", [])
    abstentions = set(diagnosis.get("abstentions", []))

    # module -> set of signal letters present in the bundle
    bundle_signals = {m: {s["signal"] for s in sigs} for m, sigs in bundle.items()}

    per_diag = []
    grounded_n = 0
    for d in diagnoses:
        module = d.get("module")
        root = d.get("root_cause")
        band = (d.get("band") or "").lower()
        cited = set(d.get("cited_signals", []))
        rationale = d.get("rationale", "") or ""

        module_exists = module in bundle
        signals_grounded = bool(cited) and cited.issubset(bundle_signals.get(module, set()))
        root_valid = root in VALID_ROOT_CAUSES
        band_valid = band in VALID_BANDS
        rep = fs.score(rationale, context, query="")
        rationale_ok = rep["overall_verdict"] != "unfaithful"

        grounded = module_exists and signals_grounded and root_valid and rationale_ok
        if grounded:
            grounded_n += 1
        per_diag.append({
            "module": module, "root_cause": root, "band": band,
            "module_exists": module_exists, "signals_grounded": signals_grounded,
            "root_cause_valid": root_valid, "band_valid": band_valid,
            "rationale_grounding": rep["token_grounding"],
            "rationale_verdict": rep["overall_verdict"],
            "grounded": grounded,
            "ungrounded_tokens": rep["hallucinated_tokens"],
        })

    groundedness = round(grounded_n / len(diagnoses), 3) if diagnoses else 1.0

    # ---- consistency vs the deterministic engine ----
    claude_diag = {d["module"]: d for d in diagnoses}
    claude_fire = {m: d.get("root_cause") for m, d in claude_diag.items()}
    all_modules = set(bundle) | set(engine) | set(claude_fire) | abstentions
    agree, disagree, missed, richer, cry_wolf, single_fire = [], [], [], [], [], []
    for m in sorted(all_modules):
        e_fires = m in engine
        c_fires = m in claude_fire
        if c_fires and e_fires:
            # Fix 3a: credit agreement with ANY engine candidate root cause. A
            # boundary pattern (D535) emits candidate_root_causes; a reasoner who
            # picks a defensible candidate agrees, not disagrees.
            e_candidates = set(engine[m].get("candidate_root_causes")
                               or [engine[m]["root_cause"]])
            if claude_fire[m] in e_candidates:
                agree.append(m)
            else:
                disagree.append({"module": m, "claude": claude_fire[m],
                                 "engine_candidates": sorted(e_candidates)})
        elif c_fires and not e_fires:
            # Claude diagnoses where the engine is silent. Fix 2: "richer" credit
            # requires >=2 cited signals AND grounded (a genuine cross-signal
            # correlation). A grounded SINGLE-signal fire is an aggressiveness call,
            # not richness — flagged separately (single_signal_fire), not credited as
            # richer and not auto-failed. An ungrounded fire is a real disagreement.
            g = next((p for p in per_diag if p["module"] == m), None)
            n_cited = len(set(claude_diag[m].get("cited_signals", [])))
            if not (g and g["grounded"]):
                disagree.append({"module": m, "claude": claude_fire[m], "grounded": False})
            elif n_cited >= 2:
                richer.append({"module": m, "claude": claude_fire[m], "grounded": True,
                               "cited_signals": n_cited})
            else:
                single_fire.append({"module": m, "claude": claude_fire[m],
                                    "cited_signals": n_cited})
        elif e_fires and not c_fires:
            missed.append({"module": m, "engine": engine[m]["root_cause"]})
        else:
            cry_wolf.append(m)  # both silent -> correct abstention (no cry-wolf)

    denom = len(agree) + len(disagree) + len(missed)
    consistency = round(len(agree) / denom, 3) if denom else 1.0

    verdict_reasons = []
    if groundedness < 1.0:
        verdict_reasons.append("ungrounded diagnosis")
    if missed:
        verdict_reasons.append(f"missed engine fires: {[m['module'] for m in missed]}")
    if disagree:
        verdict_reasons.append(f"root-cause disagreement: {disagree}")
    overall = "fail" if verdict_reasons else "pass"

    return {
        "overall_verdict": overall,
        "verdict_reasons": verdict_reasons,
        "groundedness": groundedness,
        "consistency_vs_engine": consistency,
        "n_diagnoses": len(diagnoses),
        "n_abstentions": len(abstentions),
        "agree": agree,
        "disagree": disagree,
        "missed_engine_fires": missed,
        "claude_richer_than_engine": richer,
        "single_signal_fires": single_fire,
        "both_silent_no_cry_wolf": cry_wolf,
        "per_diagnosis": per_diag,
    }


def _print(rep: dict) -> None:
    v = {"pass": "✓", "fail": "✗"}.get(rep["overall_verdict"], "?")
    print(f"OVERALL {v} {rep['overall_verdict'].upper()}"
          + (f"  ({'; '.join(rep['verdict_reasons'])})" if rep["verdict_reasons"] else ""))
    print(f"GROUNDEDNESS        {rep['groundedness']:.0%}  "
          f"({rep['n_diagnoses']} diagnoses, {rep['n_abstentions']} abstentions)")
    print(f"CONSISTENCY vs engine {rep['consistency_vs_engine']:.0%}  "
          f"(agree={len(rep['agree'])} disagree={len(rep['disagree'])} "
          f"missed={len(rep['missed_engine_fires'])})")
    if rep["claude_richer_than_engine"]:
        print(f"  + CLAUDE RICHER (grounded multi-signal diagnoses the engine misses): "
              f"{rep['claude_richer_than_engine']}")
    if rep.get("single_signal_fires"):
        print(f"  ~ SINGLE-SIGNAL FIRES (grounded but <2 signals — aggressiveness, "
              f"not richness): {rep['single_signal_fires']}")
    if rep["both_silent_no_cry_wolf"]:
        print(f"  ✓ NO CRY-WOLF (both silent): {rep['both_silent_no_cry_wolf']}")
    if rep["missed_engine_fires"]:
        print(f"  ✗ MISSED engine fires: {rep['missed_engine_fires']}")
    if rep["disagree"]:
        print(f"  ✗ DISAGREEMENT: {rep['disagree']}")
    for p in rep["per_diagnosis"]:
        flag = "✓" if p["grounded"] else "✗"
        print(f"    [{flag}] {p['module']} -> {p['root_cause']} ({p['band']}) "
              f"grounding={p['rationale_grounding']:.0%}"
              + (f" UNGROUNDED={p['ungrounded_tokens']}" if p["ungrounded_tokens"] else ""))


def score_panel(diagnosis_list: list[dict], compose: dict) -> dict:
    """Fix 3b: aggregate N independent reasoners. When a strict majority diverge
    from the engine the SAME way, that is signal about the ENGINE's (contestable)
    choice, not N reasoner failures. Flags three things for human/architect review:
      • contested root cause — engine fires M, >=majority pick the same alternative;
      • contested fire policy — engine fires M, >=majority abstain (e.g. lone-signal fire);
      • convergent richer    — >=majority fire a grounded multi-signal M the engine
                               misses, agreeing on root cause (a candidate NEW pattern).
    """
    from collections import Counter

    n = len(diagnosis_list)
    maj = n // 2 + 1
    engine = {d["module"]: d for d in compose.get("engine_diagnoses", [])}
    per_reasoner = [score(d, compose) for d in diagnosis_list]
    fires = [{x["module"]: x for x in d.get("diagnoses", [])} for d in diagnosis_list]

    contested_root, contested_fire, convergent_richer = [], [], []

    for m, ed in engine.items():
        ecands = set(ed.get("candidate_root_causes") or [ed["root_cause"]])
        diverge = Counter()
        miss = 0
        for f in fires:
            if m in f:
                rc = f[m].get("root_cause")
                if rc not in ecands:
                    diverge[rc] += 1
            else:
                miss += 1
        if diverge and max(diverge.values()) >= maj:
            top, cnt = diverge.most_common(1)[0]
            contested_root.append({
                "module": m, "engine": sorted(ecands),
                "majority_say": top, "votes": f"{cnt}/{n}",
                "note": "engine root cause contested by a majority — review attribution / candidates"})
        if miss >= maj:
            contested_fire.append({
                "module": m, "engine": ed["root_cause"], "abstain_votes": f"{miss}/{n}",
                "note": "engine fires where a majority of reasoners see no correlation — review fire policy"})

    # modules reasoners systematically fire that the engine is silent on
    engine_silent_fires = Counter()
    fire_rc = {}
    for f in fires:
        for m, d in f.items():
            if m not in engine and len(set(d.get("cited_signals", []))) >= 2:
                engine_silent_fires[m] += 1
                fire_rc.setdefault(m, Counter())[d.get("root_cause")] += 1
    for m, cnt in engine_silent_fires.items():
        if cnt >= maj:
            top_rc, rc_cnt = fire_rc[m].most_common(1)[0]
            convergent_richer.append({
                "module": m, "fire_votes": f"{cnt}/{n}",
                "majority_root_cause": top_rc, "rc_agreement": f"{rc_cnt}/{cnt}",
                "note": "majority of reasoners diagnose a multi-signal correlation the "
                        "engine has no pattern for — candidate NEW pattern (D535-class)"})

    return {
        "n_reasoners": n,
        "majority_threshold": maj,
        "per_reasoner_verdicts": [r["overall_verdict"] for r in per_reasoner],
        "per_reasoner_groundedness": [r["groundedness"] for r in per_reasoner],
        "contested_root_cause": contested_root,
        "contested_fire_policy": contested_fire,
        "convergent_richer_candidate_patterns": convergent_richer,
    }


def _print_panel(rep: dict) -> None:
    print(f"REASONER PANEL — {rep['n_reasoners']} independent reasoners "
          f"(majority = {rep['majority_threshold']})")
    print(f"  groundedness: {rep['per_reasoner_groundedness']}   "
          f"verdicts: {rep['per_reasoner_verdicts']}")
    print("  (per-reasoner FAIL is expected where the engine makes a contestable "
          "choice — read the panel flags, not the individual verdicts)")
    for key, label in [("contested_root_cause", "CONTESTED ROOT CAUSE"),
                       ("contested_fire_policy", "CONTESTED FIRE POLICY"),
                       ("convergent_richer_candidate_patterns", "CANDIDATE NEW PATTERN")]:
        for item in rep[key]:
            print(f"  ⚑ {label}: {item}")
    if not any(rep[k] for k in ("contested_root_cause", "contested_fire_policy",
                                "convergent_richer_candidate_patterns")):
        print("  ✓ no systematic divergence — reasoners and engine concur")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--diagnosis", help="single Claude diagnosis JSON file")
    ap.add_argument("--panel", nargs="+",
                    help="2+ diagnosis JSON files -> reasoner-panel divergence analysis")
    ap.add_argument("--compose-json", required=True,
                    help="correlation_compose.py --json --include-engine output")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    compose = json.loads(Path(args.compose_json).read_text())
    if args.panel:
        diags = [json.loads(Path(p).read_text()) for p in args.panel]
        rep = score_panel(diags, compose)
        print(json.dumps(rep, indent=2, default=str)) if args.json else _print_panel(rep)
        sys.exit(0)
    if not args.diagnosis:
        ap.error("provide --diagnosis <file> or --panel <file> <file> ...")
    rep = score(json.loads(Path(args.diagnosis).read_text()), compose)
    if args.json:
        print(json.dumps(rep, indent=2, default=str))
    else:
        _print(rep)
    sys.exit(0 if rep["overall_verdict"] == "pass" else 1)


if __name__ == "__main__":
    main()
