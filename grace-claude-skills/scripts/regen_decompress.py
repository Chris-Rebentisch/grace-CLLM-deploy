#!/usr/bin/env python3
"""End-to-end REGENERATION decompression driver — compose the grounded prompt,
decompress it with CLAUDE, and score FAITHFULNESS. The A2 one-command harness.

Pipeline (all heat-free):
  retrieval/composed context → real PromptAssembler (regen_compose) → Claude → faithfulness_score

CF3/D193: a pure CLIENT. It imports + calls the REAL regeneration PromptAssembler
(read-only) but NEVER ResponseSynthesizer/get_provider() (those load the configured
ollama/llama3.3:70b = HEAT). The decompression LLM is CLAUDE, two ways:
  • --answer-file F   Claude-in-the-loop: this session's Claude reads the printed
                      prompt, writes the answer to F, and the tool scores it. This
                      is the default operating mode (and the only one that works
                      when no valid LLM_API_KEY is configured).
  • --autonomous      AnthropicProvider DIRECT (heat-free cloud). Degrades to
                      printing the prompt if the key 401s (the A1 pattern).

  # 1. print the grounded prompt for the in-loop LLM (no answer yet):
  python3 regen_decompress.py --query "Why was the ValueAct standstill structured this way?" --phase-state clarify
  # 2. score the answer you wrote:
  python3 regen_decompress.py --query "..." --phase-state clarify --answer-file ans.txt --expect "board seat,proxy"
  # composed context (A1 router output) instead of native semantic:
  python3 regen_decompress.py --query "..." --context-file deal.txt --answer-file ans.txt
  # autonomous (valid key):
  python3 regen_decompress.py --query "..." --autonomous --expect "Delaware"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import add_grace_to_path, route_logs_to_stderr  # noqa: E402
import faithfulness_score as fs  # noqa: E402

API_DEFAULT = "http://127.0.0.1:8000"
CLAUDE_MODEL = "claude-haiku-4-5-20251001"  # heat-free cloud; NEVER the local llama


async def _retrieval_context(api: str, query: str, top_k: int, mode: str | None):
    import httpx

    payload: dict = {"query_text": query, "top_k": top_k}
    if mode:
        payload["iterative_mode"] = mode
    async with httpx.AsyncClient(timeout=120.0) as cx:
        r = await cx.post(f"{api}/api/retrieval/query", json=payload,
                          headers={"X-Graph-Scope": "all"})
        r.raise_for_status()
        return r.json()


def _assemble(query: str, phase_state: str, context: str, raw: dict | None):
    """Run the REAL PromptAssembler (heat-free, pure string)."""
    from src.regeneration.prompt_assembly import PromptAssembler
    from src.regeneration.regeneration_config import get_regen_settings
    from src.regeneration.regeneration_models import RegenerationQuery
    from src.retrieval.retrieval_models import RetrievalResponse

    settings = get_regen_settings()
    rr = RetrievalResponse(
        query=query, results=[], serialized_context=context,
        serialization_format=(raw or {}).get("serialization_format", "template"),
        total_candidates=(raw or {}).get("total_candidates", 0),
        strategy_contributions=(raw or {}).get("strategy_contributions", {}),
        latency_ms=(raw or {}).get("latency_ms", {}),
    )
    rq = RegenerationQuery(query_text=query, phase_state=phase_state)
    return PromptAssembler(settings).assemble(rq, rr), settings


_REJECT_STOP = {"rejected", "alternative", "rejected_alternative", "the", "and", "for", "with"}

# F2 (SS-3): auto Layer-2 — verify the answer's RELATIONAL claims against the graph.
# Layer-1 token grounding is blind to distractor capture / relational fabrication (a
# wrong-deal "Delaware" is still "grounded"); only a graph EDGE check catches it. The
# robust path is --verify-claims (the in-loop LLM declares the triples it asserted, the
# tool verifies each — the harness's "reason in-loop, execute deterministically" pattern);
# a best-effort auto-extractor covers the dominant governing-law / parties shapes.
import re as _re2


async def _resolve_node(client, token: str, labels=None) -> dict | None:
    """Find one graph node whose name CONTAINS token (case-insensitive)."""
    t = (token or "").replace("'", "").strip().lower()
    if not t:
        return None
    label_filter = ""
    if labels:
        label_filter = " AND labels(n)[0] IN [" + ", ".join(f"'{l}'" for l in labels) + "]"
    cy = (f"MATCH (n) WHERE n.name IS NOT NULL AND toLower(n.name) CONTAINS '{t}'{label_filter} "
          f"RETURN n.name AS name, labels(n)[0] AS label ORDER BY size(n.name) LIMIT 1")
    try:
        rows = (await client.execute_cypher(cy))["result"]
    except Exception:
        return None
    return rows[0] if rows else None


def _clean_entity(token: str) -> str:
    """Strip leading articles + surrounding punctuation from an extracted entity span."""
    t = (token or "").strip().strip(".,;:'\"()").strip()
    t = _re2.sub(r"^(?:the|a|an)\s+", "", t, flags=_re2.I)
    return t.strip()


async def _verify_edge(client, subj: str, edge_type: str, obj: str, edge_meta: dict) -> dict:
    """Verify (subj)-[:edge_type]->(obj) exists. Resolve each endpoint to the edge's
    correct src/dst label (so "Arconic Trademark" resolves to the Agreement, not the
    similarly-named IP_Asset). CONFIRMED / REFUTED / UNRESOLVED."""
    subj, obj = _clean_entity(subj), _clean_entity(obj)
    claim = f"{subj} --[{edge_type}]--> {obj}"
    meta = edge_meta.get(edge_type)
    if not meta:
        return {"claim": claim, "status": "UNRESOLVED", "detail": f"unknown edge type '{edge_type}'"}
    sn = await _resolve_node(client, subj, labels=[meta["src"]] if meta.get("src") else None)
    on = await _resolve_node(client, obj, labels=[meta["dst"]] if meta.get("dst") else None)
    if not sn or not on:
        miss = "subject" if not sn else "object"
        return {"claim": claim, "status": "UNRESOLVED", "detail": f"{miss} not found in graph"}
    s = sn["name"].replace("'", "")
    o = on["name"].replace("'", "")
    cy = (f"MATCH (a)-[r:{edge_type}]->(b) WHERE a.name='{s}' AND b.name='{o}' "
          f"RETURN count(r) AS c")
    try:
        c = (await client.execute_cypher(cy))["result"][0]["c"]
    except Exception as e:
        return {"claim": claim, "status": "UNRESOLVED", "detail": f"query error: {str(e)[:60]}"}
    if c > 0:
        return {"claim": f"{sn['name']} --[{edge_type}]--> {on['name']}", "status": "CONFIRMED", "detail": ""}
    return {"claim": f"{sn['name']} --[{edge_type}]--> {on['name']}", "status": "REFUTED",
            "detail": "both nodes exist but no such edge (relational hallucination / distractor capture)"}


def _auto_extract_claims(answer: str) -> list[tuple]:
    """Best-effort (subject, edge_type, object) extraction. Scoped — declares only what it
    can map; pass anything else explicitly via --verify-claims.

    R4 (session-4): the original extractor only knew the governing-law shape, so it pulled
    ZERO claims from intent-attribution prose ("X is justified by the watson_clawback
    rationale") and a fully cross-wired answer slipped through with faithfulness 1.0. We
    now also recognize the explicit arrow form and the dominant intent-attribution verbs
    (justifies / explains / rejected_alternative_to), which is where cross-wiring lives."""
    claims = []
    # explicit arrow form: "Subj --[edge]--> Obj" (the serializer's own notation)
    for m in _re2.finditer(r"([\w .,&':\-]+?)\s*--\[(\w+)\]-->\s*([\w .,&':\-]+)", answer):
        claims.append((_clean_entity(m.group(1)), m.group(2).strip(), _clean_entity(m.group(3))))
    # governing law: "<Subject> ... governed by <Object>"
    for m in _re2.finditer(r"([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){1,8}?)\s+(?:is|are|shall be)?\s*governed by(?:\s+the(?:\s+laws? of)?)?\s+([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})", answer):
        subj = _clean_entity(m.group(1))
        obj = _re2.sub(r"\s+laws?$", "", _clean_entity(m.group(2)), flags=_re2.I).strip()
        claims.append((subj, "governed_by", obj))
    # intent attribution (passive): "<obj> is justified/explained by [the] <snake_id> [rationale]"
    _verb2edge = {"justified": "justifies", "explained": "explains"}
    for m in _re2.finditer(
            r"([A-Z\"][\w .,:&'\-]{3,80}?)\s+(?:is|are|was|were)\s+(justified|explained)\s+by\s+"
            r"(?:the\s+)?([a-z][a-z0-9]*(?:_[a-z0-9]+)+)", answer):
        claims.append((m.group(3).strip(), _verb2edge[m.group(2).lower()], _clean_entity(m.group(1))))
    # intent attribution (active): "<snake_id> justifies/explains <obj>"
    for m in _re2.finditer(
            r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\s+(justifies|explains)\s+([A-Z\"][\w .,:&'\-]{3,80})", answer):
        claims.append((m.group(1), m.group(2).lower(), _clean_entity(m.group(3))))
    # rejected alternative: "<snake_id> ... (is|was) [the] rejected alternative to <obj>"
    for m in _re2.finditer(
            r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b[\w .,:&'\-]*?rejected\s+alternative\s+to\s+([A-Z\"][\w .,:&'\-]{3,80})", answer):
        claims.append((m.group(1), "rejected_alternative_to", _clean_entity(m.group(2))))
    # dedupe (case-insensitive on the whole triple), preserve order
    seen, out = set(), []
    for c in claims:
        k = tuple(x.lower().strip() for x in c)
        if all(k) and k not in seen:
            seen.add(k)
            out.append(c)
    return out


def _parse_verify_claims(spec: str) -> list[tuple]:
    out = []
    for seg in (spec or "").split(";"):
        parts = [p.strip() for p in seg.split("|")]
        if len(parts) == 3 and all(parts):
            out.append((parts[0], parts[1], parts[2]))
    return out


async def _fetch_rejected_terms(client) -> list[dict]:
    """H-2 Layer-3: graph nodes that are REJECTED alternatives (is_term=false /
    epistemic_status=rejected_alternative / Counterfactual). Returns
    [{name, tokens}] — tokens = the name's distinctive content words, for paraphrase
    matching. Heat-free (ArcadeDB only)."""
    cy = ("MATCH (n) WHERE n.is_term = false OR n.epistemic_status = 'rejected_alternative' "
          "RETURN n.name AS name")
    try:
        rows = (await client.execute_cypher(cy))["result"]
    except Exception:
        return []
    out = []
    for r in rows:
        name = r.get("name") or ""
        toks = [t for t in name.lower().split("_") if t and t not in _REJECT_STOP]
        out.append({"name": name, "tokens": toks})
    return out


# F5 (SS-5): heat-safe local decompressor. Lets an operator exercise PRODUCTION-class
# small-model behaviour (positional bias, refusal) that the Claude/in-loop path can't
# show — via a DIRECT OllamaProvider (never get_provider(), which would load the
# configured 70B llama). A hard size guard makes loading a large model IMPOSSIBLE.
_BIG_MODEL_RE = __import__("re").compile(
    r"(?:6[05]|70|72|110|120|180|235|405)\s*b|llama3\.[13]|mixtral|:\d{2,}b", __import__("re").I)


def _decompressor_size_guard(model: str) -> tuple[bool, str]:
    """Block models too large to be heat-safe on an 18 GB box (esp. the configured
    70B llama). Returns (allowed, reason)."""
    m = (model or "").lower()
    if _BIG_MODEL_RE.search(m):
        return False, (f"'{model}' looks like a large (>=~14 GB) model — BLOCKED to protect the "
                       f"heat budget. Use a small model (e.g. qwen2.5:7b, llama3.2:3b, phi3).")
    try:
        import yaml
        cfg = yaml.safe_load(open("config/discovery.yaml"))
        configured = str(cfg.get("llm", {}).get("model", "")).lower()
        if configured and configured == m:
            return False, f"'{model}' is the CONFIGURED provider model — refusing (heat). Pick a smaller one."
    except Exception:
        pass
    return True, ""


async def _local_model_answer(model: str, system: str, context: str, query: str) -> str | None:
    ok, reason = _decompressor_size_guard(model)
    if not ok:
        print(f"  [decompressor-model BLOCKED] {reason}", file=sys.stderr)
        return None
    from src.discovery.ollama_client import OllamaProvider
    provider = OllamaProvider(model=model)
    print(f"  ⚠ loading LOCAL model '{model}' (heat: bounded, opt-in) — NOT get_provider(); "
          f"the configured 70B llama is never touched.", file=sys.stderr)
    resp = await provider.generate(system_prompt=system, user_prompt=f"{context}\n\n{query}",
                                   temperature=0.2, max_tokens=800, json_mode=False)
    return resp.text


async def _autonomous_answer(system: str, context: str, query: str) -> str | None:
    """Decompress via AnthropicProvider DIRECT (never get_provider()). Mirrors the
    real ResponseSynthesizer's call shape: system + (context\\n\\nquery)."""
    import os
    key = os.environ.get("LLM_API_KEY")
    if not key:
        try:
            from src.shared.config import get_settings
            key = get_settings().llm_api_key
        except Exception:
            key = None
    if not key:
        print("  LLM_API_KEY not set — use --answer-file (Claude-in-the-loop).", file=sys.stderr)
        return None
    from src.shared.anthropic_provider import AnthropicProvider
    provider = AnthropicProvider(api_key=key, model=CLAUDE_MODEL)
    user = f"{context}\n\n{query}"
    try:
        resp = await provider.generate(system_prompt=system, user_prompt=user,
                                       temperature=0.2, max_tokens=800, json_mode=False)
        return resp.text
    except Exception as e:  # 401/expired key etc. — degrade like A1
        print(f"  Claude API unavailable ({str(e)[:80]}). Use --answer-file (in-loop).",
              file=sys.stderr)
        return None


async def _run(args) -> None:
    add_grace_to_path()
    from dotenv import load_dotenv
    load_dotenv()

    # 1. context source
    raw = None
    if args.compose_json:
        d = json.loads(Path(args.compose_json).read_text())
        context = d.get("context", "")
        source = d.get("context_source", "compose-json")
    elif args.context_file:
        context = Path(args.context_file).read_text()
        source = f"file:{args.context_file}"
    else:
        raw = await _retrieval_context(args.api, args.query, args.top_k, args.mode)
        context = raw.get("serialized_context", "") or ""
        source = "live-retrieval-api"

    assembled, settings = _assemble(args.query, args.phase_state, context, raw)

    if not args.json:
        print(f'QUERY: "{args.query}"   phase_state={args.phase_state}   context_source={source}')
        print(f"  tokens: total={assembled.total_token_estimate}/{settings.total_input_budget_tokens}"
              + ("  ⚠TRUNCATED" if assembled.context_truncated else ""))

    # 2. obtain the answer
    answer = None
    mode = "none"
    if args.decompressor_model:
        answer = await _local_model_answer(args.decompressor_model, assembled.system_prompt,
                                           assembled.context, assembled.user_query)
        mode = f"local-model({args.decompressor_model})"
    elif args.autonomous:
        answer = await _autonomous_answer(assembled.system_prompt, assembled.context, assembled.user_query)
        mode = "autonomous(AnthropicProvider)"
    elif args.answer_file:
        answer = Path(args.answer_file).read_text()
        mode = "in-loop(answer-file)"

    if answer is None:
        # No answer yet: emit the grounded prompt for the in-loop LLM.
        if args.json:
            print(json.dumps({"context_source": source, "system_prompt": assembled.system_prompt,
                              "context": assembled.context, "user_query": assembled.user_query,
                              "context_truncated": assembled.context_truncated}, indent=2))
        else:
            print("\n=== SYSTEM PROMPT ===\n" + assembled.system_prompt)
            print("\n=== CONTEXT (the grounded subgraph) ===\n" + (assembled.context or "(empty)"))
            print("\n=== USER QUERY ===\n" + assembled.user_query)
            print("\n[in-loop] Write the answer using ONLY the context above, then re-run with "
                  "--answer-file <path> to score faithfulness.")
        return

    # 3. score
    expect = [e.strip() for e in args.expect.split(",") if e.strip()] if args.expect else None
    anti_expect = [e.strip() for e in args.anti_expect.split(",") if e.strip()] if args.anti_expect else None
    rep = fs.score(answer, assembled.context, assembled.user_query, expect,
                   expect_abstain=args.expect_abstain, anti_expect=anti_expect)
    if not args.json:
        print(f"  decompression: {mode}")
        print("\n=== ANSWER ===\n" + answer.strip())
        print("\n=== FAITHFULNESS SCORE ===")
        fs._print_report(rep, args.verbose)

    # Layer-2 (F2) + Layer-3 (H-2) graph-backed checks — share one client when needed.
    epistemic = None
    layer2 = None
    auto_extracted = None  # R4: count of auto-extracted claims (None unless --auto-layer2)
    need_graph = args.epistemic or args.verify_claims or args.auto_layer2
    if need_graph:
        from src.graph.arcade_client import get_arcade_client
        import cypher_exec
        client = get_arcade_client()

        if args.verify_claims or args.auto_layer2:
            schema = await cypher_exec.introspect_schema(client)
            edge_meta = {e["name"]: e for e in schema["edges"]}
            triples = _parse_verify_claims(args.verify_claims) if args.verify_claims else []
            if args.auto_layer2:
                auto_claims = _auto_extract_claims(answer)
                auto_extracted = len(auto_claims)
                triples += auto_claims
            layer2 = []
            for subj, edge, obj in triples:
                layer2.append(await _verify_edge(client, subj, edge, obj, edge_meta))

        if args.epistemic:
            rejected = await _fetch_rejected_terms(client)
            epistemic = fs.epistemic_violations(answer, rejected)
        await client.aclose()

    if not args.json:
        if layer2 is not None:
            print("\n=== LAYER-2 RELATION VERIFY (graph edge check) ===")
            if args.auto_layer2:
                print(f"  auto-extracted {auto_extracted} relational claim(s) from the answer")
                if auto_extracted == 0:
                    print("  ⚠ the auto-extractor mapped NO claims — this is NOT 'all clean'. "
                          "Cross-wiring/relational errors are unverified. Pass claims explicitly "
                          "with --verify-claims \"Subj|edge|Obj; ...\".")
            if not layer2:
                print("  (no relational claims extracted/supplied — Layer-2 did not run)")
            for v in layer2:
                mark = {"CONFIRMED": "✓", "REFUTED": "✗", "UNRESOLVED": "·"}.get(v["status"], "?")
                print(f"  {mark} [{v['status']}] {v['claim']}" + (f"  — {v['detail']}" if v["detail"] else ""))
        if epistemic is not None:
            print("\n=== LAYER-3 EPISTEMIC CHECK ===")
            if epistemic:
                print(f"  ✗ {len(epistemic)} EPISTEMIC VIOLATION(S) — a REJECTED alternative asserted "
                      f"as a real term (token-grounded but polarity-inverted):")
                for v in epistemic:
                    print(f"    rejected_term={v['rejected_term']} (matched on {v['matched_on']})")
                    print(f"      “{v['sentence'][:110]}”")
            else:
                print("  ✓ no rejected alternative is presented as a real term")

    # R5 (session-4): recompute the folded verdict to ALSO fold Layer-2 (edge REFUTED)
    # and Layer-3 (epistemic) — the full-stack verdict the headline `faithfulness` field
    # cannot give on its own. A REFUTED edge or an epistemic violation makes the answer
    # unfaithful even when L1 token grounding is 1.0.
    reasons = list(rep.get("verdict_reasons") or [])
    if layer2 and any(v["status"] == "REFUTED" for v in layer2):
        reasons.append("Layer-2 edge REFUTED")
    if epistemic:
        reasons.append("Layer-3 epistemic violation")
    overall = ("unfaithful" if reasons
               else "abstained" if rep.get("abstains_overall") else "faithful")

    if not args.json:
        vflag = "✗" if overall == "unfaithful" else "✓"
        print(f"\nOVERALL VERDICT {vflag} {overall.upper()}"
              + (f"  ({', '.join(reasons)})" if reasons else "  (all layers clean)"))

    if args.json:
        out = {k: v for k, v in rep.items() if k != "sentences"}
        out["overall_verdict"] = overall
        out["verdict_reasons"] = reasons
        out["context_source"] = source
        out["decompression_mode"] = mode
        out["answer"] = answer.strip()
        if layer2 is not None:
            out["layer2_relation_checks"] = layer2
            if auto_extracted is not None:
                out["auto_layer2_claims_extracted"] = auto_extracted
        if epistemic is not None:
            out["epistemic_violations"] = epistemic
        print(json.dumps(out, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--query", required=True)
    ap.add_argument("--phase-state", default="none",
                    choices=["prepare", "open", "structure", "clarify", "close", "none"])
    ap.add_argument("--top-k", type=int, default=8)
    ap.add_argument("--mode", choices=["auto", "on", "off"])
    ap.add_argument("--context-file", help="composed grounded context (A1 router output)")
    ap.add_argument("--compose-json", help="reuse a frozen regen_compose --json context")
    ap.add_argument("--answer-file", help="Claude-in-the-loop: the answer to score")
    ap.add_argument("--autonomous", action="store_true", help="AnthropicProvider direct (heat-free cloud)")
    ap.add_argument("--decompressor-model", dest="decompressor_model",
                    help="run decompression via a heat-safe LOCAL Ollama model (size-guarded; never the 70B)")
    ap.add_argument("--expect", help="comma-separated tokens a COMPLETE answer should contain")
    ap.add_argument("--expect-abstain", action="store_true", dest="expect_abstain",
                    help="negative-rejection gate: PASS only if the answer refuses (no positive assertion)")
    ap.add_argument("--anti-expect", dest="anti_expect",
                    help="comma-separated tokens that must NOT appear (distractor-capture catch)")
    ap.add_argument("--epistemic", action="store_true",
                    help="Layer-3: flag a REJECTED alternative asserted as a real term (graph-backed, heat-free)")
    ap.add_argument("--verify-claims", dest="verify_claims",
                    help="Layer-2: 'Subject|edge_type|Object; ...' relational claims to verify against the graph")
    ap.add_argument("--auto-layer2", action="store_true", dest="auto_layer2",
                    help="Layer-2: best-effort auto-extract governing-law/parties claims from the answer + verify")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--verbose", action="store_true",
                    help="per-sentence detail AND full INFO logs (default: quiet — WARNING only)")
    ap.add_argument("--api", default=API_DEFAULT)
    args = ap.parse_args()
    route_logs_to_stderr(quiet=not args.verbose)  # R6: quiet by default
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
