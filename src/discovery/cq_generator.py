"""Three-pass CQ generation pipeline orchestrator."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.discovery.cq_database import bulk_create_cqs
from src.discovery.cq_models import (
    CQPriority,
    CQSource,
    CQStatus,
    CQType,
    CompetencyQuestion,
)
from src.discovery.cq_prompts import build_combined_prompt, build_pass_prompt
from src.discovery.database import list_documents
from src.discovery.domain_batcher import (
    DomainBatch,
    _get_cq_gen_config,
    build_document_groups,
    build_document_text_for_prompt,
    build_domain_batches,
)
from src.discovery.models import ProcessingStatus
from src.discovery.ollama_client import OllamaConfig, _parse_json_robust
from src.shared.llm_provider import get_provider
from src.shared.database import get_db

logger = structlog.get_logger()

# --- Data models for generation results ---


class GeneratedCQ(BaseModel):
    """A single CQ as returned by the LLM, before DB storage."""

    question: str = Field(description="The competency question text")
    cq_type: str = Field(default="UNCLASSIFIED", description="CQ type string from LLM")
    rationale: str = Field(default="", description="Why this question matters")
    source_document_names: list[str] = Field(default_factory=list, description="Filenames from LLM")
    priority: str = Field(default="MEDIUM", description="Priority string from LLM")
    coverage_gap: bool = Field(default=False, description="True for negative-evidence CQs")


class GeneratedCQItem(BaseModel):
    """One CQ element of the grammar-constrained array schema."""

    question: str = Field(description="The competency question text")
    cq_type: str = Field(default="UNCLASSIFIED", description="CQ type")
    rationale: str = Field(default="", description="Why this question matters")
    source_document_names: list[str] = Field(default_factory=list)
    priority: str = Field(default="MEDIUM", description="HIGH|MEDIUM|LOW")


class GeneratedCQBatch(BaseModel):
    """Wrapper schema forcing the model to emit an ARRAY of CQs.

    Without this, `format=json` lets the model emit a single valid object and stop
    (Ollama treats one object as complete JSON), capping yield at ~1 CQ per pass.
    Grammar-constrained `generate_structured` against this schema makes the model
    fill an array, recovering the intended 15-25 CQs per pass (D-pending: CQ-gen
    array-output fix).
    """

    questions: list[GeneratedCQItem] = Field(
        default_factory=list, description="15-25 distinct competency questions"
    )


class GeneratedCQItemReordered(BaseModel):
    """One CQ with the rationale emitted BEFORE the question (think-then-ask).

    Field order is load-bearing for grammar-constrained decoding: the model fills
    fields in schema order, so emitting the rationale first nudges reasoning before
    the question is committed (Tam et al., EMNLP 2024). Used by the combined
    single-call generation path (A3).
    """

    rationale: str = Field(
        default="",
        description="First, which ontology element (entity type, relationship, or attribute) this question defines.",
    )
    cq_type: str = Field(default="UNCLASSIFIED", description="FOUNDATIONAL|SCOPING|RELATIONSHIP|METAPROPERTY|VALIDATING")
    question: str = Field(description="Then, the competency question text that follows from the rationale.")
    source_document_names: list[str] = Field(default_factory=list)
    priority: str = Field(default="MEDIUM", description="HIGH|MEDIUM|LOW")


class GeneratedCQBatchReordered(BaseModel):
    """Array wrapper for the rationale-first combined-generation schema."""

    questions: list[GeneratedCQItemReordered] = Field(
        default_factory=list, description="Distinct competency questions across all four angles"
    )


class PassResult(BaseModel):
    """Result of a single pass on a single domain batch."""

    pass_name: str = Field(description="Pass identifier")
    domain: str = Field(description="Domain this pass ran on")
    cqs: list[GeneratedCQ] = Field(default_factory=list, description="Generated CQs")
    raw_response: str = Field(default="", description="Full LLM output")
    model: str = Field(default="", description="Model used")
    duration_ms: int = Field(default=0, description="Duration in milliseconds")
    prompt_tokens: int = Field(default=0, description="Prompt token count")
    completion_tokens: int = Field(default=0, description="Completion token count")
    success: bool = Field(default=True, description="Whether this pass succeeded")
    error_message: str = Field(default="", description="Error details if failed")


class GenerationRun(BaseModel):
    """Complete result of running all passes across all domains."""

    run_id: str = Field(default_factory=lambda: str(uuid4()), description="Run identifier")
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = Field(default=None)
    model: str = Field(default="")
    pass_results: list[PassResult] = Field(default_factory=list)
    total_cqs_generated: int = Field(default=0)
    total_duration_ms: int = Field(default=0)
    pass_domain_weights: dict[str, dict[str, float]] = Field(default_factory=dict)
    cancelled: bool = Field(
        default=False, description="True if the run was stopped early by operator request"
    )
    error: str | None = Field(default=None, description="Error details if the run failed")


# --- In-memory storage for generation runs ---
_generation_runs: dict[str, GenerationRun] = {}

# Run IDs an operator has asked to stop. The pipeline checks this between passes
# and bails out at the next checkpoint (an in-flight LLM call can't be interrupted,
# so cancellation takes effect before the next pass, not mid-pass).
_cancelled_runs: set[str] = set()


def request_cancellation(run_id: str) -> bool:
    """Flag a generation run for early stop. Returns False if the run is unknown."""
    if run_id not in _generation_runs:
        return False
    _cancelled_runs.add(run_id)
    return True


def is_cancellation_requested(run_id: str) -> bool:
    """Whether an operator has asked this run to stop."""
    return run_id in _cancelled_runs


# --- Mapping helpers ---

_PASS_TO_SOURCE = {
    "top_down": CQSource.LLM_TOP_DOWN,
    "bottom_up": CQSource.LLM_BOTTOM_UP,
    "middle_out": CQSource.LLM_MIDDLE_OUT,
    "negative_evidence": CQSource.LLM_GAP_FILL,
    "combined": CQSource.LLM_COMBINED,
}


def map_cq_type_string(s: str) -> CQType:
    """Map a CQ type string from LLM output to CQType enum."""
    try:
        return CQType(s.upper().strip())
    except ValueError:
        return CQType.UNCLASSIFIED


def map_priority_string(s: str) -> CQPriority:
    """Map a priority string from LLM output to CQPriority enum."""
    try:
        return CQPriority(s.upper().strip())
    except ValueError:
        return CQPriority.MEDIUM


def map_pass_to_source(pass_name: str) -> CQSource:
    """Map a pass name to CQSource enum."""
    return _PASS_TO_SOURCE.get(pass_name, CQSource.SYSTEM_GENERATED)


def resolve_document_names_to_ids(filenames: list[str], db: Session) -> list[UUID]:
    """Given filenames from LLM output, find matching document UUIDs.

    Tries exact match, case-insensitive match, then substring match.
    """
    if not filenames:
        return []

    all_docs = list_documents(db, status=ProcessingStatus.COMPLETE, limit=10000)
    matched_ids: list[UUID] = []

    for fname in filenames:
        fname_lower = fname.lower().strip()
        found = False

        # Exact match
        for doc in all_docs:
            if doc.file_name == fname:
                matched_ids.append(doc.id)
                found = True
                break

        if found:
            continue

        # Case-insensitive match
        for doc in all_docs:
            if doc.file_name.lower() == fname_lower:
                matched_ids.append(doc.id)
                found = True
                break

        if found:
            continue

        # Substring match
        for doc in all_docs:
            if fname_lower in doc.file_name.lower() or doc.file_name.lower() in fname_lower:
                matched_ids.append(doc.id)
                break

    return matched_ids


def map_generated_cq_to_model(
    gen_cq: GeneratedCQ,
    pass_name: str,
    domain: str,
    linked_document_ids: list[UUID],
) -> CompetencyQuestion:
    """Convert a GeneratedCQ (LLM output) to a CompetencyQuestion (Chunk 3 model)."""
    metadata = {}
    if gen_cq.coverage_gap:
        metadata["coverage_gap"] = True

    return CompetencyQuestion(
        canonical_text=gen_cq.question,
        raw_user_input=gen_cq.question,
        cq_type=map_cq_type_string(gen_cq.cq_type),
        domain=domain,
        priority=map_priority_string(gen_cq.priority),
        source=map_pass_to_source(pass_name),
        source_pass=pass_name,
        status=CQStatus.DRAFT,
        generation_confidence=0.5,
        linked_document_ids=linked_document_ids,
        metadata_extra=metadata,
    )


def _parse_cqs_from_response(parsed_json: dict | list | None, is_negative: bool = False) -> list[GeneratedCQ]:
    """Parse GeneratedCQ objects from Ollama parsed JSON response."""
    if parsed_json is None:
        return []

    items = parsed_json if isinstance(parsed_json, list) else [parsed_json]
    cqs = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if "question" not in item:
            continue
        cqs.append(GeneratedCQ(
            question=item["question"],
            cq_type=item.get("cq_type", "UNCLASSIFIED"),
            rationale=item.get("rationale", ""),
            source_document_names=item.get("source_document_names", []),
            priority=item.get("priority", "MEDIUM"),
            coverage_gap=is_negative,
        ))
    return cqs


# --- Core pipeline functions ---


async def run_single_pass(
    pass_name: str,
    batch: DomainBatch,
    document_text: str,
    provider=None,
) -> PassResult:
    """Run a single pass on a single domain batch."""
    try:
        if provider is None:
            provider = get_provider()

        system_prompt, user_prompt = build_pass_prompt(
            pass_name=pass_name,
            domain=batch.domain,
            context_digest=batch.context_digest,
            key_terms=batch.key_terms,
            document_text=document_text,
        )

        # Grammar-constrained array output: force the model to emit a JSON ARRAY of
        # CQs instead of a single object. The old `generate(json_mode=True)` path let
        # the model emit one valid object and stop, capping yield at ~1 CQ/pass.
        response = await provider.generate_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_model=GeneratedCQBatch,
            temperature=0.0,
        )

        is_negative = pass_name == "negative_evidence"
        if response.parsed is not None:
            items = [item.model_dump() for item in response.parsed.questions]
        else:
            # Tier-B already attempted inside generate_structured; recover from raw text.
            items = _parse_json_robust(response.text)
        cqs = _parse_cqs_from_response(items, is_negative=is_negative)

        logger.info(
            "pass_complete",
            pass_name=pass_name,
            domain=batch.domain,
            cq_count=len(cqs),
            duration_ms=response.duration_ms,
        )

        return PassResult(
            pass_name=pass_name,
            domain=batch.domain,
            cqs=cqs,
            raw_response=response.text,
            model=response.model,
            duration_ms=response.duration_ms,
            prompt_tokens=response.input_tokens,
            completion_tokens=response.output_tokens,
            success=True,
        )

    except Exception as e:
        logger.error(
            "pass_failed",
            pass_name=pass_name,
            domain=batch.domain,
            error=str(e),
        )
        return PassResult(
            pass_name=pass_name,
            domain=batch.domain,
            success=False,
            error_message=str(e),
        )


async def run_combined_pass(
    batch: DomainBatch,
    document_text: str,
    provider=None,
    target: int = 30,
) -> PassResult:
    """Single multi-perspective generation call (A3) — replaces the 4 sequential passes.

    Validated 2026-06-08: matched/beat 4-pass theme coverage at ~8x less decode time with
    higher schema-shaping share and lower redundancy. Uses the rationale-first schema +
    few-shot + generality/conciseness prompt. PassResult.pass_name == "combined".
    """
    try:
        if provider is None:
            provider = get_provider()

        system_prompt, user_prompt = build_combined_prompt(
            domain=batch.domain,
            context_digest=batch.context_digest,
            key_terms=batch.key_terms,
            document_text=document_text,
            target=target,
        )

        response = await provider.generate_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_model=GeneratedCQBatchReordered,
            temperature=0.0,
        )

        if response.parsed is not None:
            items = [item.model_dump() for item in response.parsed.questions]
        else:
            items = _parse_json_robust(response.text)
        cqs = _parse_cqs_from_response(items, is_negative=False)

        logger.info(
            "combined_pass_complete",
            domain=batch.domain,
            cq_count=len(cqs),
            duration_ms=response.duration_ms,
        )

        return PassResult(
            pass_name="combined",
            domain=batch.domain,
            cqs=cqs,
            raw_response=response.text,
            model=response.model,
            duration_ms=response.duration_ms,
            prompt_tokens=response.input_tokens,
            completion_tokens=response.output_tokens,
            success=True,
        )

    except Exception as e:
        logger.error("combined_pass_failed", domain=batch.domain, error=str(e))
        return PassResult(
            pass_name="combined",
            domain=batch.domain,
            success=False,
            error_message=str(e),
        )


async def run_generation_pipeline(
    passes: list[str] | None = None,
    domains: list[str] | None = None,
    dry_run: bool = False,
    db: Session | None = None,
    run_id: str | None = None,
    generation_mode: str | None = None,
) -> GenerationRun:
    """Run the full CQ generation pipeline.

    Steps:
    1. Check Ollama health
    2. Build domain batches
    3. For each domain, run all passes
    4. Resolve document names to IDs
    5. Store CQs in database
    6. Return GenerationRun with full results

    If ``run_id`` names an existing run (created by the API endpoint before the
    background task starts), that run is reused so the caller's polled id and the
    executing run are the same object — which is also what makes cancellation
    observable. Otherwise a fresh run is created.
    """
    provider = get_provider()
    try:
        provider_model = str(getattr(provider, 'model', '') or getattr(getattr(provider, 'config', None), 'model', '') or '')
    except Exception:
        provider_model = ""
    if run_id is not None and run_id in _generation_runs:
        run = _generation_runs[run_id]
        run.model = provider_model
    else:
        run = GenerationRun(model=provider_model)
        _generation_runs[run.run_id] = run

    default_passes = ["top_down", "bottom_up", "negative_evidence", "middle_out"]
    active_passes = passes or default_passes

    # Initialize pass_domain_weights with defaults
    for p in active_passes:
        run.pass_domain_weights[p] = {}

    close_db = False
    if db is None:
        db_gen = get_db()
        db = next(db_gen)
        close_db = True

    try:
        # Build domain batches
        batches = build_domain_batches(db)
        if domains:
            batches = [b for b in batches if b.domain in domains]

        if not batches:
            logger.warning("no_domain_batches", message="No documents to process")
            run.completed_at = datetime.now(UTC)
            return run

        logger.info(
            "generation_started",
            domains=[b.domain for b in batches],
            passes=active_passes,
            dry_run=dry_run,
        )

        # Set default weights
        for p in active_passes:
            for b in batches:
                run.pass_domain_weights[p][b.domain] = 1.0

        if dry_run:
            # Build prompts only, don't call Ollama
            for batch in batches:
                doc_text = build_document_text_for_prompt(db, batch.domain)
                for pass_name in active_passes:
                    system_prompt, user_prompt = build_pass_prompt(
                        pass_name=pass_name,
                        domain=batch.domain,
                        context_digest=batch.context_digest,
                        key_terms=batch.key_terms,
                        document_text=doc_text,
                    )
                    logger.info(
                        "dry_run_prompt",
                        pass_name=pass_name,
                        domain=batch.domain,
                        system_prompt_len=len(system_prompt),
                        user_prompt_len=len(user_prompt),
                    )
                    run.pass_results.append(PassResult(
                        pass_name=pass_name,
                        domain=batch.domain,
                        success=True,
                    ))
            run.completed_at = datetime.now(UTC)
            return run

        # Check provider health
        health = await provider.health_check()
        if not health["healthy"]:
            raise RuntimeError(f"Provider is not healthy: {health.get('details', 'unknown')}")
        if not health.get("model_available", True) is False:
            pass  # Some providers can't check model availability

        # Run passes per domain. Per-document batching (default) splits each domain
        # into small, fully-covering document groups so EVERY document contributes CQs;
        # the legacy path concatenates the top-10-longest docs into one batch and the
        # model anchors on the largest two (docs/cq-generation-benchmark-25docs.md).
        gen_cfg = _get_cq_gen_config()
        per_document_batching = gen_cfg.get("per_document_batching", True)
        # generation_mode: "combined" (default, A3 — one multi-perspective call per group)
        # or "multi_pass" (legacy — the 4 sequential passes). Explicit arg overrides config.
        generation_mode = generation_mode or gen_cfg.get("generation_mode", "combined")
        combined_target = gen_cfg.get("combined_target_cqs", 30)
        all_cq_models: list[CompetencyQuestion] = []
        stopped_early = False

        for batch in batches:
            if is_cancellation_requested(run.run_id):
                stopped_early = True
                break

            if per_document_batching:
                groups = build_document_groups(db, batch.domain)
                units = [(g.label, g.text) for g in groups]
                if not units:  # defensive: fall back to the single-concat path
                    units = [("all", build_document_text_for_prompt(db, batch.domain))]
            else:
                units = [("all", build_document_text_for_prompt(db, batch.domain))]

            for unit_label, doc_text in units:
                if is_cancellation_requested(run.run_id):
                    stopped_early = True
                    break

                if generation_mode == "combined":
                    # One multi-perspective call per document group (A3, validated default).
                    results = [await run_combined_pass(
                        batch, doc_text, provider, target=combined_target
                    )]
                else:
                    # Legacy 4-pass mode. Cancellation takes effect between passes — an
                    # in-flight LLM call cannot be interrupted, so we stop before the next.
                    results = []
                    for pass_name in active_passes:
                        if is_cancellation_requested(run.run_id):
                            stopped_early = True
                            break
                        results.append(await run_single_pass(pass_name, batch, doc_text, provider))

                for result in results:
                    run.pass_results.append(result)
                    run.total_duration_ms += result.duration_ms
                    if result.success:
                        for gen_cq in result.cqs:
                            linked_ids = resolve_document_names_to_ids(
                                gen_cq.source_document_names, db
                            )
                            cq_model = map_generated_cq_to_model(
                                gen_cq, result.pass_name, batch.domain, linked_ids
                            )
                            all_cq_models.append(cq_model)
                if stopped_early:
                    break
            if stopped_early:
                break

        # Persist whatever was generated before the stop — partial work is kept.
        if all_cq_models:
            bulk_create_cqs(db, all_cq_models)
            run.total_cqs_generated = len(all_cq_models)
            logger.info("cqs_stored", count=len(all_cq_models))

        if stopped_early:
            run.cancelled = True
            logger.info(
                "generation_cancelled",
                run_id=run.run_id,
                total_cqs=run.total_cqs_generated,
            )

        run.completed_at = datetime.now(UTC)
        logger.info(
            "generation_complete",
            total_cqs=run.total_cqs_generated,
            total_duration_ms=run.total_duration_ms,
        )
        return run

    finally:
        _cancelled_runs.discard(run.run_id)
        if close_db:
            try:
                next(db_gen)
            except StopIteration:
                pass


def get_generation_run(run_id: str) -> GenerationRun | None:
    """Retrieve a generation run by ID from in-memory storage."""
    return _generation_runs.get(run_id)


# --- CLI entry point ---


def main() -> None:
    """CLI entry point for CQ generation pipeline."""
    parser = argparse.ArgumentParser(description="GrACE three-pass CQ generation pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Build prompts without calling Ollama")
    parser.add_argument("--domains", nargs="+", help="Specific domains to process")
    parser.add_argument("--passes", nargs="+", help="Specific passes to run")
    args = parser.parse_args()

    result = asyncio.run(
        run_generation_pipeline(
            passes=args.passes,
            domains=args.domains,
            dry_run=args.dry_run,
        )
    )

    print(f"\nGeneration run: {result.run_id}")
    print(f"Total CQs generated: {result.total_cqs_generated}")
    print(f"Total duration: {result.total_duration_ms}ms")
    for pr in result.pass_results:
        status = "OK" if pr.success else f"FAILED: {pr.error_message}"
        print(f"  {pr.pass_name} / {pr.domain}: {len(pr.cqs)} CQs [{status}]")


if __name__ == "__main__":
    main()
