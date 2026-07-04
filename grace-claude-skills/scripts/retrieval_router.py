#!/usr/bin/env python3
"""Claude-wrapped retrieval ROUTER — the harness that scores high structural recall
by wrapping Claude-as-the-LLM with the right schema prompt + deterministic Cypher
execution. CF3-safe: a CLIENT. It NEVER edits src/retrieval/*; it picks the right
retrieval mechanism per query and uses Claude for text-to-Cypher.

Routing (rule-based classifier; cheap, transparent):
  • STRUCTURAL / relational / aggregation / negation  -> text-to-Cypher path:
      schema-aware prompt -> Claude generates Cypher -> lint + EXPLAIN + execute
      (deterministic, via cypher_exec). One repair round on EXPLAIN failure.
      This is the fix for the 1/6 structural-recall failure (pilot: ~88% e2e).
  • TOPICAL / semantic / "why" / intent -> the existing POST /api/retrieval/query
      (already strong there: clause recall + intent "why" = 6/6). The router is a
      client of it; no pipeline change.

Heat: the Cypher generator uses AnthropicProvider DIRECTLY (Claude API) — never
get_provider() (which would load the configured ollama/llama3.3:70b = heat). The
semantic path is template-serialized (no LLM). nomic-embed-text is the only local
model that loads (via the API for the semantic branch).

  python3 retrieval_router.py --query "Which agreements are governed by Delaware law?"
  python3 retrieval_router.py --query "<q>" --expect "Armstrong"   # FOUND/MISS scoring
  python3 retrieval_router.py --query "<q>" --force cypher|semantic # override the router
  python3 retrieval_router.py --query "<q>" --no-llm               # print prompt, no API call
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import add_grace_to_path, route_logs_to_stderr  # noqa: E402
import cypher_exec  # noqa: E402

API_DEFAULT = "http://127.0.0.1:8000"
CLAUDE_MODEL = "claude-haiku-4-5-20251001"  # heat-free (cloud); never the local llama

# --- query classification ------------------------------------------------------
_STRUCTURAL_CUES = (
    "which", "who", "what", "list", "how many", "count", "number of", "total",
    "average", "most", "fewest", "each", "all ", "governed", "party", "parties",
    "owns", "owned", "covers", "territory", "grants", "license", "licensed",
    "jurisdiction", "obligor", "obligee", "owed to", "pays", "payable",
    "share", "shared", "between", "no ", "without", "missing", "every",
)
_INTENT_CUES = ("why", "rationale", "reason", "because", "tradeoff", "trade-off",
                "rejected", "alternative", "principle", "intent")


_VAGUE_CUES = ("tell me about", "about the", "overview", "summary", "summarize",
               "describe", "what is the", "give me", "everything about", "details on")


def classify(query: str) -> tuple[str, list[str]]:
    """Return (route, cues_hit). route in {structural, intent, vague, semantic}.

    S4-1: aggregation/negation cues HARD-route to structural — the semantic path
    cannot answer those and reports false success. S4-2: vague entity-anchored
    asks route to the anchor+summary path.
    """
    q = query.lower()
    agg, neg = cypher_exec.analytical_cues(query)
    if agg or neg:
        return "structural", (["agg:" + a.strip() for a in agg] + ["neg:" + n.strip() for n in neg])
    intent_hits = [c for c in _INTENT_CUES if c in q]
    if intent_hits and not any(c in q for c in ("which agreements", "list all")):
        return "intent", intent_hits
    vague_hits = [c for c in _VAGUE_CUES if c in q]
    if vague_hits:
        return "vague", vague_hits
    struct_hits = [c for c in _STRUCTURAL_CUES if c in q]
    if struct_hits:
        return "structural", struct_hits
    return "semantic", []


# --- text-to-Cypher generation (Claude) ---------------------------------------
_SYSTEM_TMPL = """You translate a natural-language question into ONE OpenCypher query for ArcadeDB.

{schema}

RULES:
1. Use ONLY vertex/edge types and properties listed above. Edge DIRECTION is exact — write edges in the direction shown.
2. Prefer a typed property (e.g. obligation_type='payment') over text matching when one exists.
3. For text matching use toLower(field) CONTAINS toLower('stem') and pick a STEM ('amend', not 'amendments'). Match entity names with CONTAINS on a distinctive token (e.g. 'Xencor').
4. Always RETURN named, human-readable columns (… AS name); never RETURN a whole node or _embedding.
5. For "how many / count / total / average" use count()/avg() with a clear alias.
6. For "no / without / missing" use WHERE NOT (n)-[:EDGE]->().
7. Output ONLY the Cypher query. No markdown fences, no prose, no explanation."""

_REPAIR_TMPL = """That query failed validation with:
{error}
Return a corrected single OpenCypher query (ONLY the query, no prose)."""


def _extract_cypher(text: str) -> str:
    t = text.strip()
    t = re.sub(r"^```(?:cypher|sql)?\s*", "", t, flags=re.I)
    t = re.sub(r"\s*```$", "", t)
    # If the model still added prose, keep from the first MATCH/CALL/WITH.
    m = re.search(r"(MATCH|CALL|WITH|UNWIND)\b", t, re.I)
    return (t[m.start():] if m else t).strip()


async def _generate_cypher(provider, schema_text: str, question: str,
                           error: str | None = None, prior: str | None = None) -> str:
    system = _SYSTEM_TMPL.format(schema=schema_text)
    if error and prior:
        user = f"Question: {question}\n\nYour previous query:\n{prior}\n\n" + _REPAIR_TMPL.format(error=error)
    else:
        user = f"Question: {question}"
    resp = await provider.generate(system_prompt=system, user_prompt=user,
                                   temperature=0.0, max_tokens=600, json_mode=False)
    return _extract_cypher(resp.text)


# --- semantic delegation (existing pipeline) ----------------------------------
async def _semantic(query: str, top_k: int, api: str) -> dict:
    import httpx
    async with httpx.AsyncClient(timeout=120.0) as cx:
        r = await cx.post(f"{api}/api/retrieval/query",
                          json={"query_text": query, "top_k": top_k},
                          headers={"X-Graph-Scope": "all"})
        r.raise_for_status()
        return r.json()


def _expect_verdict(expect: str, blob: str) -> str:
    return "FOUND" if expect.lower() in blob.lower() else "MISS"


def _print_rows(out: dict, cap: int = 60, expect_count: int | None = None) -> None:
    """Print a cypher_exec result: S4-3 (no truncation), S4-4 (empty-set note),
    S6-3 (reconciliation footer + --expect-count assertion)."""
    print(f"\n--- result ({out['row_count']} rows, ok={out['ok']}) ---")
    if not out["ok"]:
        print(f"  ERROR: {out['error']}")
        return
    rows = out["rows"]
    for r in rows[:cap]:
        print(f"  {json.dumps(r, default=str)}")
    if len(rows) > cap:
        print(f"  … showing {cap} of {len(rows)} (full set via cypher_exec --cypher)")
    if out.get("empty_set_note"):
        print(f"  {out['empty_set_note']}")
    if out.get("reconciliation"):
        print(f"  reconcile: {out['reconciliation']}")
    if expect_count is not None:
        got = out["row_count"]
        verdict = "PASS" if got == expect_count else f"FAIL (got {got})"
        print(f"  EXPECT-COUNT {expect_count}: {verdict}")


async def _run(args) -> None:
    add_grace_to_path()
    from dotenv import load_dotenv
    load_dotenv()
    import os
    from src.graph.arcade_client import get_arcade_client

    route, cues = classify(args.query)
    if args.force:
        route, cues = args.force, cues
    if args.anchor:  # --anchor forces the entity-anchored deal-summary path
        route = "vague"
    print(f'QUERY: "{args.query}"')
    print(f"  route: {route}" + (f"  (cues: {cues})" if cues else ""))

    if route == "structural":
        client = get_arcade_client()
        schema = await cypher_exec.introspect_schema(client)
        schema_text = cypher_exec.render_schema_text(schema)

        if args.no_llm:
            print("\n--- generation prompt (feed to Claude) ---")
            print(_SYSTEM_TMPL.format(schema=schema_text))
            print(f"\nQuestion: {args.query}")
            await client.aclose()
            return

        key = os.environ.get("LLM_API_KEY")
        if not key:
            try:
                from src.shared.config import get_settings
                key = get_settings().llm_api_key
            except Exception:
                key = None
        if not key:
            print("  LLM_API_KEY not set — falling back to semantic path.")
            await client.aclose()
            route = "semantic"
        elif args.cypher:
            # Claude-in-the-loop path: caller (Claude) supplies the generated Cypher;
            # the deterministic tool validates + executes it. Faithful to the
            # harness pattern (reason in-loop, execute deterministically) and the
            # path used when no valid API key is configured.
            cypher = args.cypher
            out = await cypher_exec.validate_and_run(client, cypher, schema)
            await client.aclose()
            print("\n--- supplied cypher (Claude-in-the-loop) ---")
            print(f"  {cypher}")
            if out["lint"]:
                print(f"  lint: {out['lint']}")
            _print_rows(out, expect_count=args.expect_count)
            if args.expect:
                v = _expect_verdict(args.expect, json.dumps(out["rows"], default=str))
                print(f"\nEXPECT '{args.expect}': {v}")
            print("\nGROUNDING: cypher path is grounded by construction (rows ARE graph rows).")
            return
        else:
            from src.shared.anthropic_provider import AnthropicProvider
            provider = AnthropicProvider(api_key=key, model=CLAUDE_MODEL)
            try:
                cypher = await _generate_cypher(provider, schema_text, args.query)
            except ValueError as e:  # invalid/expired key (401) — degrade cleanly
                print(f"  Claude API unavailable ({str(e)[:60]}). "
                      f"Use --cypher '<claude-generated>' (in-loop) or --no-llm (print prompt).")
                await client.aclose()
                return
            out = await cypher_exec.validate_and_run(client, cypher, schema)
            if not out["ok"]:  # one repair round
                print(f"  (repairing: {out['error'][:80]})")
                cypher2 = await _generate_cypher(provider, schema_text, args.query,
                                                 error=out["error"], prior=cypher)
                out2 = await cypher_exec.validate_and_run(client, cypher2, schema)
                if out2["ok"]:
                    cypher, out = cypher2, out2
            await client.aclose()
            print("\n--- generated cypher ---")
            print(f"  {cypher}")
            if out["lint"]:
                print(f"  lint: {out['lint']}")
            _print_rows(out, expect_count=args.expect_count)
            if args.expect:
                v = _expect_verdict(args.expect, json.dumps(out["rows"], default=str))
                print(f"\nEXPECT '{args.expect}': {v}")
            print("\nGROUNDING: cypher path is grounded by construction (rows ARE graph rows).")
            return

    if route == "vague":
        # S4-2: entity-anchored open query. Resolve mentioned entities -> the
        # agreement(s) -> a grounded 1-hop deal profile. No corpus-wide noise.
        client = get_arcade_client()
        text = args.anchor or args.query
        anchors = await cypher_exec.resolve_anchors(client, text)
        if not anchors:
            print("  no graph entity recognized in the query — falling back to semantic.")
            await client.aclose()
            route = "semantic"
        else:
            print(f"  anchored on {len(anchors)} agreement(s): {[a['name'][:40] for a in anchors]}")
            for a in anchors[:3]:
                s = await cypher_exec.node_summary(client, a["grace_id"])
                cap = s.get("per_edge_cap", 8)
                print(f"\n=== {s['label']}: {s['name']} ===")
                for k, v in (s.get("props") or {}).items():
                    if k not in ("name", "title") and not isinstance(v, (list, dict)):
                        print(f"  {k}: {v}")
                for rel, names in {**s.get("incoming", {}), **s.get("outgoing", {})}.items():
                    extra = f" (+{len(names)-cap} more)" if len(names) > cap else ""
                    print(f"  {rel}: {', '.join(names[:cap])}{extra}")
            await client.aclose()
            if args.expect:
                blob = json.dumps(anchors, default=str)
                # also scan summaries already printed via re-resolve is overkill;
                # FOUND if the expect token is in any anchor name (deal scoping check)
                print(f"\nEXPECT '{args.expect}': {_expect_verdict(args.expect, blob)}")
            print("\nGROUNDING: anchored deal profile — every line is a 1-hop graph neighbour.")
            return

    if route == "intent" or route == "semantic":
        # S4-1: warn when the semantic path is asked an analytical/absence question
        # it cannot honestly answer (it returns lexical neighbours + false success).
        agg, neg = cypher_exec.analytical_cues(args.query)
        if agg or neg:
            print(f"  ⚠ semantic path cannot satisfy "
                  f"{'aggregation' if agg else ''}{'/' if agg and neg else ''}{'absence' if neg else ''} "
                  f"intent (cues {agg + neg}). 'grounded' ≠ 'relevant' — use the Cypher path "
                  f"(--force structural --cypher).")
        resp = await _semantic(args.query, args.top_k, args.api)
        results = resp.get("results", [])
        print(f"  strategies={resp.get('strategy_contributions')}  candidates={resp.get('total_candidates')}")
        print(f"\n--- result ({len(results)} ranked) ---")
        for i, r in enumerate(results[:15]):
            print(f"  #{i+1} [{r.get('entity_type')}] {(r.get('name') or '')[:60]!r}")

        # S6-1: for a "why" answer, append the FULL grounded intent chain so the
        # principle-layer edges (applies_principle / specializes) are guaranteed,
        # not just the rationale's justifies-consequences.
        if route == "intent":
            client = get_arcade_client()
            chain = await cypher_exec.intent_chain(
                client, [r.get("grace_id") for r in results])
            # S6-2: the FROZEN semantic reranker can leak cross-deal rows on an
            # entity-named query; steer to the deal-scoped path (in-tree fix logged).
            anchors = await cypher_exec.resolve_anchors(client, args.query)
            await client.aclose()
            if chain:
                print("\n--- grounded intent chain (principle-layer guaranteed) ---")
                for e in chain[:20]:
                    print(f"  {e['src_type']} '{e['src'][:32]}' --[{e['rel']}]--> "
                          f"'{(e['dst'] or '')[:46]}'")
            if anchors:
                print(f"\n  tip (S6-2): semantic results may include cross-deal rows; for a "
                      f"deal-scoped profile use --anchor '{anchors[0]['name'][:30]}'.")

        if args.expect:
            blob = json.dumps(results, default=str) + resp.get("serialized_context", "")
            verdict = _expect_verdict(args.expect, blob)
            if (agg or neg) and verdict == "FOUND":
                verdict += "  (⚠ likely a lexical match, not a real answer — see warning above)"
            print(f"\nEXPECT '{args.expect}': {verdict}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--query", required=True)
    ap.add_argument("--top-k", type=int, default=8)
    ap.add_argument("--expect", help="substring expected in the answer -> FOUND/MISS")
    ap.add_argument("--expect-count", type=int, dest="expect_count",
                    help="assert exact row count on the structural/Cypher path -> PASS/FAIL (S6-3)")
    ap.add_argument("--force", choices=["structural", "semantic", "intent", "vague"], help="override the router")
    ap.add_argument("--anchor", help="force the entity-anchored deal-summary path on this entity name")
    ap.add_argument("--cypher", help="Claude-in-the-loop: supply the generated Cypher; tool validates+executes it")
    ap.add_argument("--no-llm", action="store_true", help="print the generation prompt, do not call Claude")
    ap.add_argument("--api", default=API_DEFAULT)
    args = ap.parse_args()
    route_logs_to_stderr()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
