#!/usr/bin/env python3
"""Regeneration COMPOSE client — assemble the exact prompt regeneration would feed
its LLM, HEAT-FREE. CF3/D193-safe: it imports the REAL ``src/regeneration``
PromptAssembler and ``RegenSettings`` and *calls* them (read-only use — it never
modifies regeneration internals). It NEVER calls ``get_provider()`` /
``ResponseSynthesizer`` (those would load the configured ollama/llama3.3:70b = HEAT).

The decompression LLM is Claude (in-loop or AnthropicProvider), supplied SEPARATELY
to ``regen_decompress.py``. This tool only produces the grounded prompt.

Pipeline:
  1. grounded context source (one of):
       • default     — POST /api/retrieval/query (the native semantic context
                        regeneration gets out of the box: nomic + bm25 + cross-encoder)
       • --context-file F  — a pre-built grounded subgraph (e.g. A1's Cypher router
                        output), so the two harnesses compose
       • --context-stdin   — read the context block from stdin
  2. reconstruct a real RetrievalResponse and run the REAL PromptAssembler
     (deterministic, pure string formatting — no LLM).
  3. emit the assembled system/context/query blocks + a machine-readable JSON
     (consumed by regen_decompress.py / faithfulness_score.py).

Heat: retrieval API loads nomic-embed-text + CPU cross-encoder only. This tool
itself loads nothing. Verify with ``ollama ps`` before/after.

  python3 regen_compose.py --query "Which agreements are governed by Delaware law?"
  python3 regen_compose.py --query "<q>" --phase-state structure --json
  python3 regen_compose.py --query "<q>" --context-file deal.txt   # composed context
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import add_grace_to_path, route_logs_to_stderr  # noqa: E402

API_DEFAULT = "http://127.0.0.1:8000"


async def _retrieval_response(api: str, query: str, top_k: int, mode: str | None) -> dict:
    """POST the live retrieval API and return the raw JSON (heat-free)."""
    import httpx

    payload: dict = {"query_text": query, "top_k": top_k}
    if mode:
        payload["iterative_mode"] = mode
    async with httpx.AsyncClient(timeout=120.0) as cx:
        r = await cx.post(
            f"{api}/api/retrieval/query",
            json=payload,
            headers={"X-Graph-Scope": "all"},
        )
        r.raise_for_status()
        return r.json()


def _build_retrieval_response(query: str, context: str, raw: dict | None):
    """Reconstruct a real RetrievalResponse object PromptAssembler can consume.

    Only ``serialized_context`` is load-bearing for assembly (PromptAssembler reads
    just that field), so ``results=[]`` is sufficient and avoids coupling to the
    RankedResult schema. Grounded ids/names are read from the raw dict separately.
    """
    from src.retrieval.retrieval_models import RetrievalResponse

    return RetrievalResponse(
        query=query,
        results=[],
        serialized_context=context,
        serialization_format=(raw or {}).get("serialization_format", "template"),
        total_candidates=(raw or {}).get("total_candidates", 0),
        strategy_contributions=(raw or {}).get("strategy_contributions", {}),
        latency_ms=(raw or {}).get("latency_ms", {}),
    )


async def _run(args) -> None:
    add_grace_to_path()
    from src.regeneration.prompt_assembly import PromptAssembler
    from src.regeneration.regeneration_config import get_regen_settings
    from src.regeneration.regeneration_models import RegenerationQuery

    raw: dict | None = None
    if args.context_file:
        context = Path(args.context_file).read_text()
        source = f"file:{args.context_file}"
    elif args.context_stdin:
        context = sys.stdin.read()
        source = "stdin"
    else:
        raw = await _retrieval_response(args.api, args.query, args.top_k, args.mode)
        context = raw.get("serialized_context", "") or ""
        source = "live-retrieval-api"

    settings = get_regen_settings()
    assembler = PromptAssembler(settings)
    rq = RegenerationQuery(query_text=args.query, phase_state=args.phase_state)
    rr = _build_retrieval_response(args.query, context, raw)
    assembled = assembler.assemble(rq, rr)

    grounded_ids = []
    result_names = []
    if raw:
        for r in raw.get("results", []):
            grounded_ids.append(r.get("grace_id"))
            if r.get("name"):
                result_names.append(r["name"])

    # F6 (SS-4): fact-level coverage — token math understates the loss an operator cares
    # about. A 509-edge subgraph (~42k tok) truncates to ~30 surviving facts (~6%).
    def _fact_count(text: str) -> int:
        return len([ln for ln in (text or "").splitlines() if ln.strip()])
    orig_facts = _fact_count(context)
    surviving_facts = _fact_count(assembled.context)
    coverage = {
        "original_fact_count": orig_facts,
        "surviving_fact_count": surviving_facts,
        "dropped_fact_count": max(0, orig_facts - surviving_facts),
        "coverage_fraction": round(surviving_facts / orig_facts, 3) if orig_facts else 1.0,
    }

    # F6: --assert-truncation / --assert-no-truncation (golden-gate / regression use).
    if args.assert_truncation and not assembled.context_truncated:
        print("ASSERT FAILED: expected truncation but context was NOT truncated", file=sys.stderr)
        sys.exit(3)
    if args.assert_no_truncation and assembled.context_truncated:
        print("ASSERT FAILED: expected NO truncation but context WAS truncated", file=sys.stderr)
        sys.exit(3)

    if args.json:
        print(json.dumps({
            "query": args.query,
            "phase_state": args.phase_state,
            "context_source": source,
            "system_prompt": assembled.system_prompt,
            "context": assembled.context,
            "user_query": assembled.user_query,
            "phase_style_applied": assembled.phase_style_applied,
            "context_truncated": assembled.context_truncated,
            "truncation_details": assembled.truncation_details,
            "tokens": {
                "system": assembled.system_token_estimate,
                "context": assembled.context_token_estimate,
                "query": assembled.query_token_estimate,
                "total": assembled.total_token_estimate,
                "budget": settings.total_input_budget_tokens,
            },
            "coverage": coverage,
            "grounded_ids": grounded_ids,
            "result_names": result_names,
            "strategy_contributions": (raw or {}).get("strategy_contributions", {}),
        }, indent=2, default=str))
        return

    print(f'QUERY: "{args.query}"   phase_state={args.phase_state}   context_source={source}')
    print(f"  tokens: system={assembled.system_token_estimate} "
          f"context={assembled.context_token_estimate} query={assembled.query_token_estimate} "
          f"total={assembled.total_token_estimate} / budget={settings.total_input_budget_tokens}"
          + ("  ⚠ TRUNCATED" if assembled.context_truncated else ""))
    if assembled.context_truncated:
        print(f"  truncation: {assembled.truncation_details}")
        print(f"  fact coverage: {coverage['surviving_fact_count']}/{coverage['original_fact_count']} "
              f"facts survive ({coverage['coverage_fraction']:.0%}); "
              f"{coverage['dropped_fact_count']} dropped from the tail")
    print("\n=== SYSTEM PROMPT ===")
    print(assembled.system_prompt)
    print("\n=== CONTEXT (the grounded subgraph regeneration consumes) ===")
    print(assembled.context or "(empty)")
    print("\n=== USER QUERY ===")
    print(assembled.user_query)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--query", required=True)
    ap.add_argument("--phase-state", default="none",
                    choices=["prepare", "open", "structure", "clarify", "close", "none"])
    ap.add_argument("--top-k", type=int, default=8)
    ap.add_argument("--mode", choices=["auto", "on", "off"], help="iterative_mode override")
    ap.add_argument("--context-file", help="use a pre-built grounded context (composes with A1 router)")
    ap.add_argument("--context-stdin", action="store_true", help="read context block from stdin")
    ap.add_argument("--json", action="store_true", help="machine-readable output for the scorer")
    ap.add_argument("--assert-truncation", action="store_true", dest="assert_truncation",
                    help="exit non-zero unless the context was truncated (regression use)")
    ap.add_argument("--assert-no-truncation", action="store_true", dest="assert_no_truncation",
                    help="exit non-zero if the context was truncated (regression use)")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="restore full INFO logs (default: quiet — WARNING only)")
    ap.add_argument("--api", default=API_DEFAULT)
    args = ap.parse_args()
    route_logs_to_stderr(quiet=not args.verbose)  # R6: quiet by default
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
