#!/usr/bin/env python3
"""Retrieval harness — CLIENT/PROBE tool. Queries the REAL grace retrieval API
and evaluates what it returns. CF3-safe: it never imports or mutates
``src/retrieval/*`` — it is a consumer that POSTs to ``/api/retrieval/query``
and scores the response against graph ground-truth.

  --query "<natural language>"   run one query through the full pipeline
      [--top-k N]                final result count (default 8)
      [--mode auto|on|off]       iterative_mode override (Path: round-2 retrieval)
      [--types T1,T2]            restrict entity_types
      [--context]                print the serialized_context block
      [--raw]                    dump the full JSON response

What it adds on top of the raw API (the evaluation layer):
  • GROUNDING CHECK — every returned grace_id is looked up in ArcadeDB. A result
    whose id does not resolve is a PHANTOM (stale index / corpus-keying bug). The
    real entity's type + name/summary is shown so you can judge display fidelity.
  • STRATEGY / LATENCY / MODE summary — which of the 5 strategies fired, RRF
    candidate count, per-stage timing, single_round vs iterative_round2.
  • PLANE TAG — marks each result fact-plane vs intent-plane (Decision_Principle/
    Rationale/Counterfactual/Mandatory_Provision) vs system (Query_Event/…), so
    you can see whether retrieval reaches the intent layer.

Heat: the default retrieval config serializes with ``template`` (no LLM). Only
nomic-embed-text loads (semantic + chunk_semantic strategies); the reranker is a
CPU cross-encoder. Never routes through regeneration/grace_answer.

  python3 retrieval_probe.py --query "Which agreements are governed by Delaware law?"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import add_grace_to_path, route_logs_to_stderr  # noqa: E402
import cypher_exec  # noqa: E402  (shared analytical-cue detector, S4-1)

API_DEFAULT = "http://127.0.0.1:8000"

# Intent-plane vertex types (the human-reasoning meta-layer, ontology v#9–v#11).
_INTENT_TYPES = {
    "Decision_Principle", "Decision_Rationale", "Counterfactual",
    "Mandatory_Provision",
}
# Graph plumbing — should never surface as a domain answer.
_SYSTEM_TYPES = {
    "Query_Event", "Response_Event", "Extraction_Event", "Correction_Event",
    "Migration_Event", "GovernanceDecision_Event",
}


def _plane(etype: str | None) -> str:
    if etype in _INTENT_TYPES:
        return "INTENT"
    if etype in _SYSTEM_TYPES:
        return "SYSTEM"
    return "fact"


async def _post_query(api: str, payload: dict) -> dict:
    import httpx

    async with httpx.AsyncClient(timeout=120.0) as cx:
        r = await cx.post(
            f"{api}/api/retrieval/query",
            json=payload,
            headers={"X-Graph-Scope": "all"},
        )
        r.raise_for_status()
        return r.json()


async def _ground(client, grace_ids: list[str]) -> dict[str, dict]:
    """Look up each grace_id in ArcadeDB. Returns {gid: {type,name}} for those
    that resolve; missing ids are absent from the dict (= phantom)."""
    if not grace_ids:
        return {}
    id_list = ", ".join(f"'{g}'" for g in grace_ids)
    rows = (await client.execute_cypher(
        f"MATCH (n) WHERE n.grace_id IN [{id_list}] "
        "RETURN n.grace_id AS gid, labels(n)[0] AS t, "
        "coalesce(n.name, n.summary, n.statement, '(unnamed)') AS nm"
    ))["result"]
    return {r["gid"]: {"type": r["t"], "name": r["nm"]} for r in rows}


async def _run(args) -> None:
    add_grace_to_path()
    from src.graph.arcade_client import get_arcade_client

    payload: dict = {"query_text": args.query, "top_k": args.top_k}
    if args.mode:
        payload["iterative_mode"] = args.mode
    if args.types:
        payload["entity_types"] = [t.strip() for t in args.types.split(",") if t.strip()]
    if args.seed:
        payload["seed_entity_ids"] = [s.strip() for s in args.seed.split(",") if s.strip()]

    resp = await _post_query(args.api, payload)
    if args.raw:
        print(json.dumps(resp, indent=2, default=str))
        return

    results = resp.get("results", [])
    gids = [r["grace_id"] for r in results]

    client = get_arcade_client()
    truth = await _ground(client, gids)
    await client.aclose()

    lat = resp.get("latency_ms", {})
    print(f'QUERY: "{args.query}"')
    print(f"  mode={resp.get('retrieval_mode')}  format={resp.get('serialization_format')}  "
          f"candidates={resp.get('total_candidates')}  intents={resp.get('query_intents')}")
    print(f"  strategies_fired={resp.get('strategy_contributions')}")
    print(f"  latency_ms: total={lat.get('total')}  "
          f"semantic={lat.get('semantic')}  bm25={lat.get('bm25')}  graph={lat.get('graph')}  "
          f"chunk_semantic={lat.get('chunk_semantic')}  rerank={lat.get('rerank')}")
    print(f"  multi_hop_proxy={resp.get('multi_hop_proxy_score')}  query_event_id={resp.get('query_event_id')}")

    # S4-1: the semantic path cannot honestly answer aggregation/absence questions —
    # it returns lexical neighbours and a grounded-looking false success. Warn loudly.
    _agg, _neg = cypher_exec.analytical_cues(args.query)
    if _agg or _neg:
        print(f"  ⚠ ANALYTICAL/ABSENCE intent detected (cues {_agg + _neg}). The semantic "
              f"path CANNOT answer this — results below are lexical neighbours; 'grounded' ≠ "
              f"'relevant'. Use the Cypher path (retrieval_router.py --force structural --cypher).")

    phantom = 0
    print(f"\nRESULTS ({len(results)}) — [display name/type] vs [graph ground-truth]:")
    for i, r in enumerate(results):
        gid = r["grace_id"]
        disp_name = (r.get("name") or "")[:42]
        disp_type = r.get("entity_type")
        g = truth.get(gid)
        if g is None:
            phantom += 1
            print(f"  #{i+1:>2} ✗PHANTOM  display={disp_name!r}/{disp_type}  id={gid[:8]}  "
                  f"strat={r.get('contributing_strategies')}")
            continue
        plane = _plane(g["type"])
        flag = "★" if plane == "INTENT" else ("⚠" if plane == "SYSTEM" else " ")
        truth_name = (g["name"] or "")[:46]
        print(f"  #{i+1:>2} {flag}[{plane:<6}] {g['type']:<18} {truth_name!r}")
        print(f"         id={gid[:8]} strat={r.get('contributing_strategies')} hop={r.get('hop_distance')}")

    resolved = len(results) - phantom
    print(f"\nGROUNDING: {resolved}/{len(results)} resolve in graph"
          + (f"   ⚠ {phantom} PHANTOM (stale index?)" if phantom else "   ✓ all grounded"))

    # F-H1: optional expected-substring recall scoring. Scans the result names,
    # ground-truth names, AND the serialized context (structural answers often
    # live only in context edges) -> FOUND / CONTEXT-ONLY / MISS.
    if args.expect:
        exp = args.expect.lower()
        in_results = any(exp in (r.get("name") or "").lower() for r in results) or \
            any(exp in (g.get("name") or "").lower() for g in truth.values())
        in_context = exp in (resp.get("serialized_context", "") or "").lower()
        verdict = "FOUND" if in_results else ("CONTEXT-ONLY" if in_context else "MISS")
        if (_agg or _neg) and verdict != "MISS":
            verdict += "  (⚠ likely a lexical match on an analytical/absence query — not a real answer)"
        print(f"\nEXPECT '{args.expect}': {verdict}")

    if args.context:
        print("\n--- serialized_context ---")
        print(resp.get("serialized_context", "(empty)"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--query", required=True, help="natural-language query")
    ap.add_argument("--top-k", type=int, default=8)
    ap.add_argument("--mode", choices=["auto", "on", "off"], help="iterative_mode override")
    ap.add_argument("--seed", help="comma-separated seed grace_ids for graph traversal")
    ap.add_argument("--types", help="comma-separated entity_types filter")
    ap.add_argument("--expect", help="substring expected in the answer -> FOUND/CONTEXT-ONLY/MISS")
    ap.add_argument("--context", action="store_true", help="print serialized_context")
    ap.add_argument("--raw", action="store_true", help="dump full JSON response")
    ap.add_argument("--api", default=API_DEFAULT)
    args = ap.parse_args()
    route_logs_to_stderr()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
