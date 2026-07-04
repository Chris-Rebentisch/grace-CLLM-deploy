#!/usr/bin/env python3
"""Intent harness — READ/EXTRACT tool. Three modes; all read-only, heat-free.

  --facts "<agreement substring>"     prepare-phase queue: the intent-rich facts to elicit
  --similar "<statement>" --applies-when "<scope>"   canonicalization: near-duplicate principles
  --ask "<a novel decision>"          extraction: top-K principles + precedent + rejected paths
                                      to compose a human-inspired resolution

Embodies the extraction rule (D-int-14): retrieve top-K, select using ``applies_when``, never
trust rank #1. Embeds queries over statement+applies_when (D-int-5). Only nomic-embed-text loads.

  python3 intent_query.py --ask "We're licensing exclusively to a much larger partner who runs
                                 all the regulatory work — who should bear the compliance cost?"
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import add_grace_to_path, route_logs_to_stderr  # noqa: E402

# high-stakes obligation types — a wrong/unexamined why is expensive on these (prepare queue)
_HIGH_STAKES = ("payment", "governance", "indemnification", "non_compete", "exclusivity",
                "restriction", "warranty", "change_control")


async def _facts(agreement: str, ollama: str, full: bool = False) -> None:
    add_grace_to_path()
    from src.graph.arcade_client import get_arcade_client
    from src.graph.cypher_utils import escape_cypher_string
    c = get_arcade_client()
    a = escape_cypher_string(agreement)
    rows = (await c.execute_cypher(
        f"MATCH (ag:Agreement)-[:has_obligation]->(o:Obligation) WHERE ag.name CONTAINS '{a}' "
        "OPTIONAL MATCH (o)<-[:justifies]-(r:Decision_Rationale) "
        "RETURN o.grace_id AS gid, o.obligation_type AS t, o.summary AS s, "
        "count(r) AS has_intent ORDER BY o.obligation_type"))["result"]
    law = (await c.execute_cypher(
        f"MATCH (ag:Agreement)-[:governed_by]->(j:Jurisdiction) WHERE ag.name CONTAINS '{a}' "
        "RETURN j.name AS law"))["result"]
    await c.aclose()
    queue = sorted(rows, key=lambda r: (r["has_intent"] > 0, r["t"] not in _HIGH_STAKES))
    print(f"Governing law: {', '.join(x['law'] for x in law) or '(none recorded)'}")
    print("Intent-rich facts to elicit (★ high-stakes, ✓ already has captured why):\n")
    for r in queue:
        star = "★" if r["t"] in _HIGH_STAKES else " "
        seen = "✓" if r["has_intent"] else " "
        # F1: --full prints the verbatim clause (needed to present a fact at elicitation time)
        text = r["s"] if full else r["s"][:72]
        gid = r["gid"] if full else r["gid"][:8]
        print(f"  {star}{seen} [{r['t']:<16}] {gid}  {text}")


async def _fact(gid: str, ollama: str) -> None:
    """F1: one fact's verbatim text + its neighborhood + any captured intent — for elicitation."""
    add_grace_to_path()
    from src.graph.arcade_client import get_arcade_client
    from src.graph.cypher_utils import escape_cypher_string
    c = get_arcade_client()
    g = escape_cypher_string(gid)
    o = (await c.execute_cypher(
        f"MATCH (o:Obligation) WHERE o.grace_id STARTS WITH '{g}' "
        "OPTIONAL MATCH (a:Agreement)-[:has_obligation]->(o) "
        "RETURN o.grace_id AS gid, o.obligation_type AS t, o.summary AS s, a.name AS agreement"))["result"]
    if not o:
        print(f"No fact found for {gid}"); await c.aclose(); return
    o = o[0]
    intent = (await c.execute_cypher(
        f"MATCH (o:Obligation {{grace_id:'{escape_cypher_string(o['gid'])}'}}) "
        "OPTIONAL MATCH (r:Decision_Rationale)-[:justifies]->(o) "
        "OPTIONAL MATCH (p:Decision_Principle)-[:explains]->(o) "
        "OPTIONAL MATCH (cf:Counterfactual)-[:rejected_alternative_to]->(o) "
        "OPTIONAL MATCH (mp:Mandatory_Provision)-[:compels]->(o) "
        "RETURN collect(DISTINCT r.name) AS rationales, collect(DISTINCT p.name) AS principles, "
        "collect(DISTINCT cf.name) AS counterfactuals, collect(DISTINCT mp.name) AS compelled"))["result"][0]
    await c.aclose()
    print(f"AGREEMENT: {o['agreement']}")
    print(f"TYPE: {o['t']}   GRACE_ID: {o['gid']}\n")
    print(f"VERBATIM:\n  {o['s']}\n")
    has = lambda xs: [x for x in xs if x]
    print("Already-captured intent on this fact:")
    print(f"  rationales:      {has(intent['rationales']) or '(none — elicit a why)'}")
    print(f"  principles:      {has(intent['principles']) or '(none)'}")
    print(f"  counterfactuals: {has(intent['counterfactuals']) or '(none)'}")
    print(f"  compelled-by:    {has(intent['compelled']) or '(none — check if mandatory architecture)'}")


async def _similar(statement: str, applies_when: str, ollama: str) -> None:
    add_grace_to_path()
    from src.graph.arcade_client import get_arcade_client
    from src.shared.embeddings import embed_texts
    from src.extraction.intent_writer import find_similar_principles
    c = get_arcade_client()
    qv = (await embed_texts([f"{statement} Applies when: {applies_when}"], base_url=ollama))[0]
    # P4: surface near-PARENTS too, not just duplicates — tier the output (low floor 0.62).
    hits = await find_similar_principles(c, qv, top_k=8, threshold=0.62)
    await c.aclose()
    if not hits:
        print("No existing principle is related — this is a NEW principle."); return
    dup = [h for h in hits if h["similarity"] >= 0.93]
    strong = [h for h in hits if 0.80 <= h["similarity"] < 0.93]
    related = [h for h in hits if 0.62 <= h["similarity"] < 0.80]
    if dup:
        print("LIKELY DUPLICATE — reuse this principle (do not re-author):")
        for h in dup: print(f"  [{h['similarity']:.3f}] {h['name']}")
    if strong:
        print("STRONG OVERLAP — confirm reuse with the human before authoring new:")
        for h in strong: print(f"  [{h['similarity']:.3f}] {h['name']}")
    if related:
        print("RELATED — consider `specializes` (is your principle a CHILD of one of these?). Human judges:")
        for h in related: print(f"  [{h['similarity']:.3f}] {h['name']}")
    print("\nNote: at this range similarity is a weak parent signal — the human decides reuse vs specialize vs new.")


async def _ask(question: str, top_k: int, ollama: str) -> None:
    add_grace_to_path()
    from src.graph.arcade_client import get_arcade_client
    from src.shared.embeddings import embed_texts
    from src.graph.cypher_utils import escape_cypher_string
    import numpy as np
    c = get_arcade_client()
    prows = (await c.execute_cypher(
        "MATCH (p:Decision_Principle) RETURN p.name AS name, p.statement AS s, "
        "p.applies_when AS w, p.certainty_band AS band"))["result"]
    pemb = await embed_texts([f"{r['s']} Applies when: {r['w']}" for r in prows], base_url=ollama)
    qv = np.array((await embed_texts([question], base_url=ollama))[0])
    cos = lambda e: float(qv @ np.array(e) / (np.linalg.norm(qv) * np.linalg.norm(e)))
    ranked = sorted(((cos(e), r) for e, r in zip(pemb, prows)), key=lambda x: -x[0])[:top_k]

    print(f'QUESTION: "{question}"\n')
    print(f"Top {top_k} captured principles (select by applies_when — do NOT trust rank #1):\n")
    for score, p in ranked:
        esc = escape_cypher_string(p["name"])
        rat = (await c.execute_cypher(
            f"MATCH (r:Decision_Rationale)-[:applies_principle]->(:Decision_Principle {{name:'{esc}'}}) "
            "RETURN r.name AS name, r.summary AS summary, r.leverage AS leverage LIMIT 1"))["result"]
        contracts = (await c.execute_cypher(
            f"MATCH (:Decision_Principle {{name:'{esc}'}})-[:explains]->(o:Obligation)"
            "<-[:has_obligation]-(a:Agreement) RETURN DISTINCT a.name AS name"))["result"]
        cfs = (await c.execute_cypher(
            f"MATCH (:Decision_Principle {{name:'{esc}'}})-[:explains]->(f)"
            "<-[:rejected_alternative_to]-(cf:Counterfactual) "
            "RETURN cf.demanded AS demanded, cf.why_rejected AS why LIMIT 1"))["result"]
        print(f"  [{score:.3f}] {p['name']}  (applies_when scope below; certainty {p['band']})")
        print(f"     \"{p['s']}\"")
        if rat:
            print(f"     precedent: {rat[0]['name']} — {rat[0]['summary'][:120]}")
            if rat[0]["leverage"]: print(f"     leverage : {rat[0]['leverage']}")
        if contracts:
            print(f"     drawn from: {', '.join(x['name'][:38] for x in contracts[:3])}")
        if cfs:
            print(f"     already-rejected: {cfs[0]['demanded'][:90]} ({cfs[0]['why'][:60]})")
        print()
    await c.aclose()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--facts", help="list intent-rich facts for an agreement (substring match)")
    ap.add_argument("--full", action="store_true", help="with --facts: print verbatim clauses + full grace_ids")
    ap.add_argument("--fact", help="one fact's verbatim text + neighborhood + captured intent (grace_id prefix)")
    ap.add_argument("--similar", help="statement of a proposed principle to canonicalize")
    ap.add_argument("--applies-when", default="", help="scope of the proposed principle (with --similar)")
    ap.add_argument("--ask", help="a novel decision to resolve from captured intent")
    ap.add_argument("--top-k", type=int, default=4)
    ap.add_argument("--ollama", default="http://localhost:11434")
    args = ap.parse_args()
    route_logs_to_stderr()
    if args.facts:
        asyncio.run(_facts(args.facts, args.ollama, full=args.full))
    elif args.fact:
        asyncio.run(_fact(args.fact, args.ollama))
    elif args.similar:
        asyncio.run(_similar(args.similar, args.applies_when, args.ollama))
    elif args.ask:
        asyncio.run(_ask(args.ask, args.top_k, args.ollama))
    else:
        ap.error("one of --facts / --fact / --similar / --ask is required")


if __name__ == "__main__":
    main()
