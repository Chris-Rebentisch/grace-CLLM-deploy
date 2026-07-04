#!/usr/bin/env python3
"""Intent harness — WRITE tool. Takes a human-confirmed decision bundle and writes it.

Productionizes the manual example writes: validates via the Pydantic source-of-truth models,
embeds principles over statement+applies_when (D-int-5), surfaces near-duplicate principles for
reuse (D-int-6), and writes everything through the deterministic ``src/extraction/intent_writer``
(epistemic fence, provenance-not-decay, edge-dedup — all guaranteed in code). Idempotent.

The facilitator (Claude) composes the bundle AFTER the human confirms the structure (skill step
4). This tool has zero reasoning latitude.

Bundle JSON shape:
  {
    "session_id": "...", "reviewer": "...",
    "principles": [{"name","statement","applies_when","certainty_band"?}],
    "rationale": {"name","summary","constraint"?,"stakes"?,"leverage"?,"negotiation"?,
                  "certainty_band"?,"resolver"?,
                  "applies_principles": ["name",...], "justifies_facts": ["grace_id",...]},
    "principle_explains": {"principle_name": ["fact_grace_id",...]},
    "counterfactuals": [{"name","description","demanded","why_rejected",
                         "rejected_alternative_to": "fact_grace_id"}],
    "traded_for": [{"a":"fact_id","b":"fact_id","certainty_band"?,"documented"?,"note"?}]
  }

  python3 intent_apply.py --bundle decision.json [--ollama http://localhost:11434] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import add_grace_to_path, route_logs_to_stderr  # noqa: E402


async def _run(bundle: dict, ollama: str, dry_run: bool) -> None:
    add_grace_to_path()
    from src.graph.arcade_client import get_arcade_client
    from src.shared.embeddings import embed_texts
    from src.extraction.intent_writer import (
        write_principle, write_rationale, write_counterfactual, write_mandatory_provision,
        link_intent, find_similar_principles, DEFAULT_PRINCIPLE_SIMILARITY,
    )
    from src.ontology.intent_models import (
        DecisionPrinciple, DecisionRationale, Counterfactual, MandatoryProvision,
    )

    reviewer = bundle.get("reviewer", "human (facilitated by claude)")
    sid = bundle.get("session_id")
    c = get_arcade_client()

    # --- principles (embed statement+applies_when; surface dupes) -------------------
    pid: dict[str, str] = {}
    p_models = [DecisionPrinciple.model_validate(p) for p in bundle.get("principles", [])]
    embs = []
    if p_models:
        embs = await embed_texts([f"{p.statement} Applies when: {p.applies_when}" for p in p_models],
                                 base_url=ollama)
    for p, e in zip(p_models, embs):
        dupes = await find_similar_principles(c, e, threshold=DEFAULT_PRINCIPLE_SIMILARITY)
        dupes = [d for d in dupes if d["name"] != p.name]
        if dupes:
            print(f"  [canonicalize] '{p.name}' is similar to existing "
                  f"{[(d['name'], d['similarity']) for d in dupes]} — confirm reuse if intended.",
                  file=sys.stderr)
        if dry_run:
            print(f"  [dry-run] would write principle {p.name}")
            pid[p.name] = f"dry::{p.name}"
            continue
        out = await write_principle(c, p, reviewer=reviewer, session_id=sid, embedding=e)
        pid[p.name] = out["grace_id"]
        print(f"  [principle] {'reused' if out['reused'] else 'new   '} {p.name} -> {out['grace_id']}")

    # --- rationale ------------------------------------------------------------------
    r = bundle.get("rationale")
    rid = None
    if r:
        rm = DecisionRationale.model_validate({k: v for k, v in r.items()
                                               if k not in ("applies_principles", "justifies_facts")})
        if dry_run:
            rid = "dry::rationale"; print(f"  [dry-run] would write rationale {rm.name}")
        else:
            out = await write_rationale(c, rm, reviewer=reviewer, session_id=sid)
            rid = out["grace_id"]; print(f"  [rationale] {rm.name} -> {rid}")

    # --- counterfactuals ------------------------------------------------------------
    cf_targets: list[tuple[str, str]] = []  # (cf_grace_id, fact_grace_id)
    for cf in bundle.get("counterfactuals", []):
        cm = Counterfactual.model_validate({k: v for k, v in cf.items() if k != "rejected_alternative_to"})
        if dry_run:
            print(f"  [dry-run] would write counterfactual {cm.name} (fenced)"); continue
        out = await write_counterfactual(c, cm, reviewer=reviewer, session_id=sid)
        cf_targets.append((out["grace_id"], cf["rejected_alternative_to"]))
        print(f"  [counterfactual] {cm.name} -> {out['grace_id']} (fenced)")

    # --- mandatory provisions (v#10 P1 — compelled facts, no fabricated rationale) ---
    mp_targets: list[tuple[str, str]] = []  # (provision_grace_id, fact_grace_id)
    for mp in bundle.get("mandatory_provisions", []):
        mm = MandatoryProvision.model_validate({k: v for k, v in mp.items() if k != "compels"})
        if dry_run:
            print(f"  [dry-run] would write mandatory_provision {mm.name} (compelled, source={mm.source_of_compulsion})"); continue
        out = await write_mandatory_provision(c, mm, reviewer=reviewer, session_id=sid)
        mp_targets.append((out["grace_id"], mp["compels"]))
        print(f"  [mandatory]      {mm.name} -> {out['grace_id']} (compelled by {mm.source_of_compulsion})")

    if dry_run:
        await c.aclose(); print("[dry-run] no edges written."); return

    # --- edges (all dedup-guarded) --------------------------------------------------
    edges = 0
    async def L(s, t, ty, props=None):
        nonlocal edges
        if await link_intent(c, source_grace_id=s, edge_type=ty, target_grace_id=t,
                             reviewer=reviewer, session_id=sid, properties=props):
            edges += 1

    if rid and r:
        for f in r.get("justifies_facts", []): await L(rid, f, "justifies")
        for pn in r.get("applies_principles", []):
            if pn in pid: await L(rid, pid[pn], "applies_principle")
    for pn, facts in bundle.get("principle_explains", {}).items():
        if pn in pid:
            for f in facts: await L(pid[pn], f, "explains")
    for cf_gid, fact in cf_targets:
        await L(cf_gid, fact, "rejected_alternative_to")
    for mp_gid, fact in mp_targets:
        await L(mp_gid, fact, "compels")
    for t in bundle.get("traded_for", []):
        props = {"certainty_band": t.get("certainty_band", "medium"),
                 "documented": t.get("documented", False), "note": t.get("note", "")}
        await L(t["a"], t["b"], "traded_for", props)
        await L(t["b"], t["a"], "traded_for", props)
    # v#10 P2 — load-bearing dependency between two real facts
    for d in bundle.get("depends_on", []):
        await L(d["source"], d["target"], "depends_on", {"note": d.get("note", "")})

    # v#11 P4 — principle hierarchy: child specializes parent (parent may pre-exist)
    from src.graph.cypher_utils import escape_cypher_string
    async def _principle_gid(name):
        if name in pid:
            return pid[name]
        rows = (await c.execute_cypher(
            f"MATCH (p:Decision_Principle {{name:'{escape_cypher_string(name)}'}}) RETURN p.grace_id AS g"))["result"]
        return rows[0]["g"] if rows else None
    for s in bundle.get("specializes", []):
        child, parent = await _principle_gid(s["child"]), await _principle_gid(s["parent"])
        if child and parent:
            await L(child, parent, "specializes")
        else:
            print(f"  [specializes] SKIP — unresolved principle: child={s['child']} parent={s['parent']}", file=sys.stderr)

    await c.aclose()
    print(f"\nDONE. principles={len(pid)} rationale={'1' if rid else '0'} "
          f"counterfactuals={len(cf_targets)} mandatory={len(mp_targets)} edges_written={edges}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bundle", required=True, help="path to the confirmed decision bundle JSON")
    ap.add_argument("--ollama", default="http://localhost:11434", help="Ollama base url for embeddings")
    ap.add_argument("--dry-run", action="store_true", help="validate + surface dupes, write nothing")
    args = ap.parse_args()
    route_logs_to_stderr()
    bundle = json.loads(Path(args.bundle).read_text(encoding="utf-8"))
    # P5/F2 — session-bundle: a top-level `decisions: [...]` writes N decisions in one run.
    # Each decision is a standalone bundle inheriting top-level reviewer/session_id.
    if isinstance(bundle.get("decisions"), list):
        top = {k: bundle[k] for k in ("reviewer", "session_id") if k in bundle}
        decisions = bundle["decisions"]
        for i, dec in enumerate(decisions):
            print(f"=== decision {i + 1}/{len(decisions)} ===")
            asyncio.run(_run({**top, **dec}, args.ollama, args.dry_run))
        print(f"\nALL DONE — {len(decisions)} decisions in the session bundle.")
    else:
        asyncio.run(_run(bundle, args.ollama, args.dry_run))


if __name__ == "__main__":
    main()
