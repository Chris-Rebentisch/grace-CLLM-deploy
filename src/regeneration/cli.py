"""Regeneration CLI — canonical entrypoint.

python -m src.regeneration.cli --query "..."

§10 of chunk-23-spec.md. --dry-run prints the assembled prompt and
does NOT call the LLM. scripts/regeneration_query.py wraps this.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import cast

from src.regeneration.prompt_assembly import PromptAssembler
from src.regeneration.regeneration_config import get_regen_settings
from src.regeneration.regeneration_models import (
    PhaseState,
    RegenerationQuery,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="regeneration",
        description="Run the GrACE regeneration pipeline end-to-end.",
    )
    parser.add_argument("--query", required=True, help="Natural language query")
    parser.add_argument(
        "--phase-state",
        default="none",
        choices=["prepare", "open", "structure", "clarify", "close", "none"],
    )
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the assembled prompt without calling the LLM",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit raw RegenerationResponse as JSON",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Pretty-print claim spans, latency, tokens",
    )
    return parser


async def _dry_run(args: argparse.Namespace) -> int:
    from src.retrieval.retrieval_models import RetrievalResponse

    settings = get_regen_settings()
    asm = PromptAssembler(settings)
    query = RegenerationQuery(
        query_text=args.query,
        phase_state=cast(PhaseState, args.phase_state),
    )
    empty = RetrievalResponse(
        query=args.query,
        results=[],
        serialized_context="",
        serialization_format="template",
        total_candidates=0,
        strategy_contributions={},
        latency_ms={},
    )
    assembled = asm.assemble(query, empty)
    print("=== SYSTEM PROMPT ===")
    print(assembled.system_prompt)
    print()
    print("=== CONTEXT (empty in --dry-run) ===")
    print(assembled.context)
    print()
    print("=== USER QUERY ===")
    print(assembled.user_query)
    print()
    print(
        f"[tokens] system={assembled.system_token_estimate} "
        f"context={assembled.context_token_estimate} "
        f"query={assembled.query_token_estimate} "
        f"total={assembled.total_token_estimate}"
    )
    return 0


async def _run_pipeline(args: argparse.Namespace) -> int:
    # Lazy imports so --dry-run does not require retrieval infrastructure.
    from src.graph.arcade_client import ArcadeClient
    from src.graph.config import ArcadeConfig
    from src.regeneration.regeneration_pipeline import (
        RegenerationPipeline,
        RegenerationStageError,
    )
    from src.retrieval.bm25_strategy import BM25SearchIndex
    from src.retrieval.pipeline import RetrievalPipeline
    from src.retrieval.reranker import CrossEncoderReranker
    from src.retrieval.retrieval_config import RetrievalConfig
    from src.retrieval.retrieval_models import RetrievalQuery
    from src.retrieval.semantic_strategy import SemanticSearchIndex

    settings = get_regen_settings()
    retrieval_config = RetrievalConfig()
    retrieval_pipeline = RetrievalPipeline(
        client=ArcadeClient(config=ArcadeConfig()),
        config=retrieval_config,
        semantic_index=SemanticSearchIndex(
            ollama_base_url=retrieval_config.ollama_base_url,
            model=retrieval_config.embedding_model,
        ),
        bm25_index=BM25SearchIndex(),
        reranker=CrossEncoderReranker(model_name=retrieval_config.reranker_model),
    )
    pipe = RegenerationPipeline(
        retrieval_pipeline=retrieval_pipeline, settings=settings
    )

    query = RegenerationQuery(
        query_text=args.query,
        phase_state=cast(PhaseState, args.phase_state),
        retrieval_query=RetrievalQuery(
            query_text=args.query, top_k=args.top_k
        ),
    )

    try:
        resp = await pipe.regenerate(query)
    except RegenerationStageError as exc:
        print(
            f"[error] stage={exc.stage} message={exc}",
            file=sys.stderr,
        )
        return 2

    if args.json:
        print(resp.model_dump_json())
        return 0

    print(resp.response_text)
    if args.verbose:
        print()
        print("=== CLAIM SPANS ===")
        for span in resp.claim_spans:
            print(
                f"[{span.certainty_band}] {span.text} "
                f"ids={span.supporting_grace_ids}"
            )
        print()
        print("=== LATENCY ms ===")
        print(json.dumps(resp.latency_ms, indent=2))
        print()
        print("=== TOKEN USAGE ===")
        print(json.dumps(resp.token_usage, indent=2))
    return 0


async def _amain(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.dry_run:
        return await _dry_run(args)
    return await _run_pipeline(args)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(argv))


if __name__ == "__main__":
    raise SystemExit(main())
