"""Two-stage CQ-driven schema extraction pipeline orchestrator.

Stage 1: Lightweight — identifies type names, descriptions, CQ IDs per pass.
Stage 2: Focused — details one type at a time (properties, evidence, relationships).
"""

import argparse
import asyncio
from datetime import UTC, datetime

import numpy as np
import structlog
from sqlalchemy.orm import Session

from src.discovery.cq_database import list_cqs
from src.discovery.cq_models import CQStatus
from src.discovery.domain_batcher import (
    build_balanced_document_text,
    build_domain_batches,
)
from src.discovery.merge_embeddings import compute_similarity_matrix, embed_texts
from src.discovery.models import load_discovery_config
from src.discovery.schema_models import (
    PassOutput,
    ProposedEntityType,
    ProposedProperty,
    ProposedRelationship,
    SchemaExtractionRun,
    Stage1Output,
    Stage1RelSummary,
    Stage1TypeSummary,
    Stage2BatchOutput,
    Stage2Output,
)
from src.discovery.schema_prompts import (
    build_stage1_prompt,
    build_stage2_batch_prompt,
    build_stage2_prompt,
)
from src.discovery.seed_parser import format_for_llm
from src.discovery.seed_provisioner import parse_and_cache_seeds
from src.discovery.seed_registry import resolve_sources_for_industry
from src.shared.database import get_db
from src.shared.llm_provider import get_provider

logger = structlog.get_logger()

# --- In-memory storage for schema extraction runs ---
_schema_runs: dict[str, SchemaExtractionRun] = {}


def _get_schema_extraction_config() -> dict:
    """Get schema_extraction config from discovery.yaml."""
    config = load_discovery_config()
    return config.get("schema_extraction", {})


def _compute_cq_coverage(
    entity_types: list[ProposedEntityType],
    relationships: list[ProposedRelationship],
    total_cqs: int,
) -> float:
    """Compute the fraction of input CQs referenced by proposed types."""
    if total_cqs == 0:
        return 0.0
    referenced_ids: set[str] = set()
    for et in entity_types:
        referenced_ids.update(et.answerable_cqs)
        for prop in et.properties:
            referenced_ids.update(prop.answerable_cqs)
    for rel in relationships:
        referenced_ids.update(rel.answerable_cqs)
    return min(len(referenced_ids) / total_cqs, 1.0)


def _summary_to_skeleton_type(
    summary: Stage1TypeSummary, domain: str
) -> ProposedEntityType:
    """Promote a Stage-1 type summary to a skeleton ProposedEntityType (no Stage 2).

    Carries name, hierarchy, description, CQ linkage and seed alignment from Stage 1;
    leaves ``properties`` and ``evidence_documents`` empty — those are filled later by
    batched Stage-2 detailing, but only for the types a reviewer ratifies (Option B).
    No LLM call.
    """
    return ProposedEntityType(
        name=summary.name,
        parent_type=summary.parent_type,
        description=summary.description,
        display_label=summary.display_label,
        plain_description=summary.plain_description,
        example_snippet=summary.example_snippet,
        domain=summary.domain or domain,
        properties=[],
        answerable_cqs=summary.answerable_cqs,
        # Stage-1 now cites evidence documents directly (used for the reviewer's
        # "how common is this" signal); Stage-2 detailing may augment this later.
        evidence_documents=list(summary.evidence_documents),
        seed_alignment=summary.seed_alignment,
    )


def _parse_entity_types(raw_list: list[dict]) -> list[ProposedEntityType]:
    """Parse entity types from raw JSON dicts, skipping invalid entries."""
    results = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        raw_props = item.get("properties", [])
        props = []
        for p in raw_props:
            if isinstance(p, dict) and "name" in p:
                props.append(ProposedProperty.model_validate(p))
        item["properties"] = props
        try:
            results.append(ProposedEntityType.model_validate(item))
        except Exception:
            logger.warning("invalid_entity_type", item=item)
    return results


def _parse_relationships(raw_list: list[dict]) -> list[ProposedRelationship]:
    """Parse relationships from raw JSON dicts, skipping invalid entries."""
    results = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        raw_props = item.get("edge_properties", [])
        props = []
        for p in raw_props:
            if isinstance(p, dict) and "name" in p:
                props.append(ProposedProperty.model_validate(p))
        item["edge_properties"] = props
        try:
            results.append(ProposedRelationship.model_validate(item))
        except Exception:
            logger.warning("invalid_relationship", item=item)
    return results


# --- Stage 1: Identify types (lightweight) ---


async def run_stage1_pass(
    pass_name: str,
    domain: str,
    document_text: str,
    cqs: list,
    seed_reference_text: str | None,
    config: dict,
    provider=None,
) -> tuple[Stage1Output | None, int, int, int, str]:
    """Run Stage 1 for a single pass on a single domain.

    Returns: (stage1_output, duration_ms, input_tokens, output_tokens, model)
    """
    try:
        if provider is None:
            provider = get_provider()

        system_prompt, user_prompt = build_stage1_prompt(
            pass_name=pass_name,
            domain=domain,
            document_text=document_text,
            cqs=cqs,
            seed_reference_text=seed_reference_text,
            config=config,
        )

        temperature = config.get("temperature", 0.0)
        # D444 — schema-conformant by construction on Tier A; Tier-B recovery in provider layer (D444.3). Authorization: D444.
        response = await provider.generate_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_model=Stage1Output,
            temperature=temperature,
        )

        output = response.parsed
        logger.info(
            "stage1_complete",
            pass_name=pass_name,
            domain=domain,
            types=len(output.entity_types),
            rels=len(output.relationships),
        )
        return output, response.duration_ms, response.input_tokens, response.output_tokens, response.model

    except Exception as e:
        # Phase-4 debug: str(e) was coming back empty (e.g. for Pydantic
        # ValidationError with no .args). Capture richer detail so silent
        # stage-1 failures surface their actual cause.
        import traceback as _tb
        logger.error(
            "stage1_failed",
            pass_name=pass_name,
            domain=domain,
            error=str(e) or repr(e),
            error_type=type(e).__name__,
            traceback=_tb.format_exc().splitlines()[-6:],
        )
        return None, 0, 0, 0, ""


# --- Stage 2: Detail one type ---


async def run_stage2_detail(
    type_summary: Stage1TypeSummary,
    domain: str,
    document_text: str,
    cqs: list,
    seed_reference_text: str | None,
    config: dict,
    provider=None,
) -> ProposedEntityType | None:
    """Run Stage 2 for a single entity type. Returns detailed ProposedEntityType."""
    try:
        if provider is None:
            provider = get_provider()

        system_prompt, user_prompt = build_stage2_prompt(
            type_name=type_summary.name,
            type_description=type_summary.description,
            domain=domain,
            document_text=document_text,
            cqs=cqs,
            seed_reference_text=seed_reference_text,
        )

        temperature = config.get("temperature", 0.0)
        # D444 — schema-conformant by construction on Tier A; Tier-B recovery in provider layer (D444.3). Authorization: D444.
        response = await provider.generate_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_model=Stage2Output,
            temperature=temperature,
        )

        stage2 = response.parsed

        # Parse properties from Stage2Output dicts into ProposedProperty instances
        props = []
        for p in stage2.properties:
            if isinstance(p, dict) and "name" in p:
                try:
                    props.append(ProposedProperty.model_validate(p))
                except Exception:
                    pass

        return ProposedEntityType(
            name=type_summary.name,
            parent_type=type_summary.parent_type,
            description=type_summary.description,
            domain=type_summary.domain,
            properties=props,
            answerable_cqs=type_summary.answerable_cqs,
            evidence_documents=stage2.evidence_documents,
            seed_alignment=type_summary.seed_alignment,
        )

    except Exception as e:
        logger.error("stage2_failed", type_name=type_summary.name, error=str(e))
        return None


def _detail_to_entity_type(
    summary: Stage1TypeSummary, detail
) -> ProposedEntityType:
    """Merge a Stage-2 detail (properties + evidence) onto a Stage-1 type summary."""
    props = []
    for p in detail.properties:
        if isinstance(p, dict) and "name" in p:
            try:
                props.append(ProposedProperty.model_validate(p))
            except Exception:
                pass
    return ProposedEntityType(
        name=summary.name,
        parent_type=summary.parent_type,
        description=summary.description,
        domain=summary.domain,
        properties=props,
        answerable_cqs=summary.answerable_cqs,
        evidence_documents=detail.evidence_documents,
        seed_alignment=summary.seed_alignment,
    )


async def run_stage2_batch(
    summaries: list[Stage1TypeSummary],
    domain: str,
    document_text: str,
    cqs: list,
    seed_reference_text: str | None,
    config: dict,
    provider=None,
) -> list[ProposedEntityType]:
    """Detail SEVERAL types in one LLM call; return one ProposedEntityType per input.

    Matches each returned detail back to its requested summary by normalized name.
    Any summary the model omits degrades gracefully to a skeleton type (empty
    properties) so the batch never silently drops a type. On any failure, every
    summary in the batch falls back to a skeleton.
    """
    if not summaries:
        return []
    try:
        if provider is None:
            provider = get_provider()

        system_prompt, user_prompt = build_stage2_batch_prompt(
            type_specs=[(s.name, s.description) for s in summaries],
            domain=domain,
            document_text=document_text,
            cqs=cqs,
            seed_reference_text=seed_reference_text,
        )
        temperature = config.get("temperature", 0.0)
        response = await provider.generate_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_model=Stage2BatchOutput,
            temperature=temperature,
        )
        by_name = {
            _normalize_type_name(d.name): d for d in (response.parsed.types or [])
        }
        results: list[ProposedEntityType] = []
        matched = 0
        for s in summaries:
            detail = by_name.get(_normalize_type_name(s.name))
            if detail is not None:
                results.append(_detail_to_entity_type(s, detail))
                matched += 1
            else:
                results.append(_summary_to_skeleton_type(s, domain))
        logger.info(
            "stage2_batch_complete",
            domain=domain,
            requested=len(summaries),
            detailed=matched,
        )
        return results
    except Exception as e:
        logger.error(
            "stage2_batch_failed",
            domain=domain,
            count=len(summaries),
            error=str(e),
        )
        # Don't lose the types — return skeletons so the caller can retry later.
        return [_summary_to_skeleton_type(s, domain) for s in summaries]


async def detail_types(
    summaries: list[Stage1TypeSummary],
    domain: str,
    document_text: str,
    cqs: list,
    seed_reference_text: str | None = None,
    config: dict | None = None,
    provider=None,
    batch_size: int = 4,
) -> list[ProposedEntityType]:
    """Fill properties for a ratified subset of types via batched Stage-2 calls.

    Chunks ``summaries`` into groups of ``batch_size`` (default 4) and details each
    group in one LLM call. Batches share the cached DOCUMENTS+CQ prefix, so only the
    small per-batch type list is re-prefilled. Returns detailed types in input order.
    """
    config = config or {}
    batch_size = max(1, int(batch_size))
    chunks = [
        summaries[i : i + batch_size] for i in range(0, len(summaries), batch_size)
    ]
    logger.info(
        "detail_types_started",
        domain=domain,
        types=len(summaries),
        batches=len(chunks),
        batch_size=batch_size,
    )
    detailed: list[ProposedEntityType] = []
    for chunk in chunks:
        detailed.extend(
            await run_stage2_batch(
                summaries=chunk,
                domain=domain,
                document_text=document_text,
                cqs=cqs,
                seed_reference_text=seed_reference_text,
                config=config,
                provider=provider,
            )
        )
    return detailed


# --- Deduplication ---


def _normalize_type_name(name: str) -> str:
    """Normalize a type name for Tier 1 string matching.

    Steps: lowercase, spaces to underscores, collapse repeated underscores,
    strip trailing digits/suffixes, strip leading/trailing underscores.
    """
    import re

    n = name.lower().replace(" ", "_")
    n = re.sub(r"_+", "_", n)       # collapse repeated underscores
    n = re.sub(r"_?\d+$", "", n)    # strip trailing digits
    return n.strip("_")


async def _deduplicate_types(
    all_summaries: list[tuple[str, Stage1TypeSummary]],
    threshold: float,
) -> list[tuple[Stage1TypeSummary, list[str]]]:
    """Two-tier hybrid dedup: normalized string match then enriched embedding similarity.

    Tier 1: Group types whose normalized names are identical (fast, high-precision).
    Tier 2: Merge remaining groups if enriched embedding similarity >= threshold.

    Args:
        all_summaries: List of (pass_name, Stage1TypeSummary) tuples.
        threshold: Embedding similarity threshold for Tier 2 dedup (0.85 default).

    Returns:
        List of (canonical_summary, source_passes) tuples.
    """
    if not all_summaries:
        return []

    # --- Tier 1: Normalized string matching ---
    norm_groups: dict[str, list[int]] = {}
    for i, (_, summary) in enumerate(all_summaries):
        key = _normalize_type_name(summary.name)
        norm_groups.setdefault(key, []).append(i)

    tier1_groups: list[tuple[Stage1TypeSummary, list[str]]] = []

    for indices in norm_groups.values():
        # Pick best summary (most CQs)
        best_idx = max(indices, key=lambda i: len(all_summaries[i][1].answerable_cqs))
        best_summary = all_summaries[best_idx][1]
        source_passes = list({all_summaries[i][0] for i in indices})
        tier1_groups.append((best_summary, source_passes))

    # --- Tier 2: Enriched embedding similarity on Tier 1 representatives ---
    if len(tier1_groups) <= 1:
        return tier1_groups

    # Enrich with "clustering: name — description" prefix per nomic-embed-text docs
    enriched_texts = [
        f"clustering: {group[0].name} — {group[0].description}"
        for group in tier1_groups
    ]

    try:
        embeddings = await embed_texts(enriched_texts)
        sim_matrix = compute_similarity_matrix(embeddings)
    except Exception:
        logger.warning("tier2_embedding_failed", message="Skipping Tier 2, returning Tier 1 results")
        return tier1_groups

    # Merge groups that exceed threshold
    used = set()
    final_groups: list[tuple[Stage1TypeSummary, list[str]]] = []

    for i, (summary_i, passes_i) in enumerate(tier1_groups):
        if i in used:
            continue
        used.add(i)
        merged_passes = list(passes_i)
        best = summary_i

        for j in range(i + 1, len(tier1_groups)):
            if j in used:
                continue
            if float(sim_matrix[i][j]) >= threshold:
                used.add(j)
                merged_passes.extend(tier1_groups[j][1])
                if len(tier1_groups[j][0].answerable_cqs) > len(best.answerable_cqs):
                    best = tier1_groups[j][0]

        # Deduplicate pass names
        merged_passes = list(set(merged_passes))
        final_groups.append((best, merged_passes))

    return final_groups


# --- Orchestrator ---


def _load_seed_reference_text(config: dict, industry_profile_override: str | None = None) -> str | None:
    """Load seed reference text if an industry profile is configured."""
    discovery_config = load_discovery_config()
    seed_config = discovery_config.get("seed", {})
    industry_profile = industry_profile_override or seed_config.get("industry_profile")

    if not industry_profile:
        logger.info("no_industry_profile", message="Running without seed reference")
        return None

    sources = resolve_sources_for_industry(industry_profile)
    if not sources:
        logger.info("no_seed_sources", industry=industry_profile)
        return None

    try:
        seed_ref = parse_and_cache_seeds(sources, discovery_config, industry_id=industry_profile)
        if seed_ref.total_entity_types == 0:
            return None
        return format_for_llm(seed_ref)
    except Exception as e:
        logger.warning("seed_reference_load_failed", error=str(e))
        return None


def _get_domain_cqs(all_cqs: list, domain: str) -> list:
    """Get CQs for a domain. 'other' gets all CQs."""
    if domain == "other":
        return all_cqs
    domain_cqs = [cq for cq in all_cqs if cq.domain == domain]
    return domain_cqs if domain_cqs else all_cqs


async def run_schema_extraction(
    db: Session | None = None,
    dry_run: bool = False,
    domains: list[str] | None = None,
    passes: list[str] | None = None,
    industry_profile: str | None = None,
    run_id: str | None = None,
) -> SchemaExtractionRun:
    """Run the two-stage schema extraction pipeline.

    For each domain:
    1. Stage 1: Run all passes (lightweight type identification)
    2. Deduplicate types across passes
    3. Stage 2: Detail each unique type (one call per type, concurrent)
    4. Assemble PassOutput objects from Stage 1 + Stage 2 data
    """
    config = _get_schema_extraction_config()
    default_passes = config.get("passes", ["top_down", "bottom_up", "middle_out"])
    active_passes = passes or default_passes
    stage2_concurrency = config.get("stage2_concurrency", 5)
    dedup_threshold = config.get("stage2_dedup_threshold", 0.85)
    # stage2_mode controls per-type property detailing during extraction:
    #   "deferred" (default) — skip Stage 2; emit a fast skeleton (types + relationships,
    #       empty properties). Properties are filled later, batched, only for the types a
    #       reviewer ratifies (refined Option B). Keeps extraction well under the UI's
    #       15-min poll timeout and cuts gpt-oss load dramatically.
    #   "inline" — detail every unique type with a per-type LLM call during extraction
    #       (legacy behavior; slow at scale — 17 serial calls overran the UI timeout).
    stage2_mode = config.get("stage2_mode", "deferred")

    provider = get_provider()
    try:
        provider_model = str(
            getattr(provider, "model", "")
            or getattr(getattr(provider, "config", None), "model", "")
            or ""
        )
    except Exception:
        provider_model = ""

    # Reuse the caller-supplied run (the one the API/frontend is polling) so the
    # polled run_id IS the run that does the work. Without this, the route's
    # placeholder run never gets marked complete and the UI times out even though
    # extraction succeeded (the schema-extraction analogue of the CQ-gen run_id fix).
    if run_id is not None and run_id in _schema_runs:
        run = _schema_runs[run_id]
        run.model = provider_model
        run.provider = provider.provider_name
    else:
        run = SchemaExtractionRun(model=provider_model, provider=provider.provider_name)
        _schema_runs[run.run_id] = run

    close_db = False
    if db is None:
        db_gen = get_db()
        db = next(db_gen)
        close_db = True

    try:
        # Load CQs
        all_cqs = list_cqs(db, status=CQStatus.ACCEPTED, limit=10000)
        if not all_cqs:
            all_cqs = list_cqs(db, status=CQStatus.DRAFT, limit=10000)
            if all_cqs:
                logger.info("using_draft_cqs", count=len(all_cqs))

        run.cqs_used = len(all_cqs)

        # Load seed reference
        seed_reference_text = _load_seed_reference_text(config, industry_profile_override=industry_profile)
        run.seed_reference_used = seed_reference_text is not None

        # Build domain batches
        batches = build_domain_batches(db)
        if domains:
            batches = [b for b in batches if b.domain in domains]

        if not batches:
            logger.warning("no_domain_batches", message="No documents to process")
            run.status = "completed"
            run.completed_at = datetime.now(UTC)
            return run

        logger.info(
            "schema_extraction_started",
            domains=[b.domain for b in batches],
            passes=active_passes,
            cqs=len(all_cqs),
            seed_available=run.seed_reference_used,
            dry_run=dry_run,
        )

        if dry_run:
            for batch in batches:
                doc_text = build_balanced_document_text(db, batch.domain)
                domain_cqs = _get_domain_cqs(all_cqs, batch.domain)
                for pass_name in active_passes:
                    system_prompt, user_prompt = build_stage1_prompt(
                        pass_name=pass_name,
                        domain=batch.domain,
                        document_text=doc_text,
                        cqs=domain_cqs,
                        seed_reference_text=seed_reference_text,
                        config=config,
                    )
                    logger.info(
                        "dry_run_prompt",
                        pass_name=pass_name,
                        domain=batch.domain,
                        system_prompt_len=len(system_prompt),
                        user_prompt_len=len(user_prompt),
                        cqs_in_prompt=len(domain_cqs),
                    )
                    run.pass_outputs.append(PassOutput(pass_name=pass_name, domain=batch.domain, success=True))
                run.domains_processed.append(batch.domain)
            run.status = "completed"
            run.completed_at = datetime.now(UTC)
            return run

        # Check provider health
        health = await provider.health_check()
        if not health["healthy"]:
            raise RuntimeError(f"Provider not healthy: {health.get('details', 'unknown')}")

        # --- Two-stage extraction per domain ---
        for batch in batches:
            doc_text = build_balanced_document_text(db, batch.domain)
            domain_cqs = _get_domain_cqs(all_cqs, batch.domain)

            # Stage 1: All passes for this domain
            stage1_results: dict[str, Stage1Output] = {}
            all_type_summaries: list[tuple[str, Stage1TypeSummary]] = []
            all_rel_summaries: list[tuple[str, Stage1RelSummary]] = []
            total_s1_duration = 0

            for pass_name in active_passes:
                s1_output, dur, inp_tok, out_tok, model = await run_stage1_pass(
                    pass_name=pass_name,
                    domain=batch.domain,
                    document_text=doc_text,
                    cqs=domain_cqs,
                    seed_reference_text=seed_reference_text,
                    config=config,
                    provider=provider,
                )
                total_s1_duration += dur
                if s1_output:
                    stage1_results[pass_name] = s1_output
                    for ts in s1_output.entity_types:
                        all_type_summaries.append((pass_name, ts))
                    for rs in s1_output.relationships:
                        all_rel_summaries.append((pass_name, rs))

            # Stage 2: Deduplicate and detail each unique type
            dedup_groups = await _deduplicate_types(all_type_summaries, dedup_threshold)

            logger.info(
                "stage2_dedup",
                domain=batch.domain,
                total_types=len(all_type_summaries),
                unique_types=len(dedup_groups),
            )

            detailed_types: list[tuple[ProposedEntityType | None, list[str]]] = []

            if stage2_mode == "deferred":
                # Skeleton: no per-type LLM detailing. Properties are deferred to
                # batched, ratified-subset Stage-2 detailing (Option B).
                detailed_types = [
                    (_summary_to_skeleton_type(summary, batch.domain), src_passes)
                    for summary, src_passes in dedup_groups
                ]
                logger.info(
                    "stage2_deferred",
                    domain=batch.domain,
                    skeleton_types=len(detailed_types),
                )
            else:
                # inline: detail each unique type with its own LLM call (legacy).
                semaphore = asyncio.Semaphore(stage2_concurrency)

                async def _detail_one(summary: Stage1TypeSummary, src_passes: list[str]):
                    async with semaphore:
                        result = await run_stage2_detail(
                            type_summary=summary,
                            domain=batch.domain,
                            document_text=doc_text,
                            cqs=domain_cqs,
                            seed_reference_text=seed_reference_text,
                            config=config,
                            provider=provider,
                        )
                        return result, src_passes

                tasks = [_detail_one(s, sp) for s, sp in dedup_groups]
                detailed_types = await asyncio.gather(*tasks)

            # Assemble relationships from Stage 1 (no Stage 2 for rels — merge handles richness)
            all_rels: list[ProposedRelationship] = []
            for pass_name, rs in all_rel_summaries:
                all_rels.append(ProposedRelationship(
                    name=rs.name,
                    source_type=rs.source_type,
                    target_type=rs.target_type,
                    description=rs.description,
                    display_label=rs.display_label,
                    plain_description=rs.plain_description,
                    example_snippet=rs.example_snippet,
                    answerable_cqs=rs.answerable_cqs,
                    seed_alignment=rs.seed_alignment,
                ))

            # Build per-pass PassOutput objects
            for pass_name in active_passes:
                pass_types: list[ProposedEntityType] = []
                pass_rels: list[ProposedRelationship] = []

                # Include types where this pass was a source
                for maybe_et, src_passes in detailed_types:
                    if maybe_et is not None and pass_name in src_passes:
                        pass_types.append(maybe_et)

                # Include rels from this pass
                if pass_name in stage1_results:
                    for rs in stage1_results[pass_name].relationships:
                        pass_rels.append(ProposedRelationship(
                            name=rs.name,
                            source_type=rs.source_type,
                            target_type=rs.target_type,
                            description=rs.description,
                            display_label=rs.display_label,
                            plain_description=rs.plain_description,
                            example_snippet=rs.example_snippet,
                            answerable_cqs=rs.answerable_cqs,
                            seed_alignment=rs.seed_alignment,
                        ))

                cq_coverage = _compute_cq_coverage(pass_types, pass_rels, len(domain_cqs))
                run.pass_outputs.append(PassOutput(
                    pass_name=pass_name,
                    domain=batch.domain,
                    entity_types=pass_types,
                    relationships=pass_rels,
                    total_cq_coverage=cq_coverage,
                    model=provider_model,
                    duration_ms=total_s1_duration // max(len(active_passes), 1),
                    success=pass_name in stage1_results,
                    error_message="" if pass_name in stage1_results else "Stage 1 failed",
                ))
                run.total_entity_types += len(pass_types)
                run.total_relationships += len(pass_rels)

            run.total_duration_ms += total_s1_duration
            run.domains_processed.append(batch.domain)

        run.status = "completed"
        run.completed_at = datetime.now(UTC)
        logger.info(
            "schema_extraction_complete",
            total_entity_types=run.total_entity_types,
            total_relationships=run.total_relationships,
            total_duration_ms=run.total_duration_ms,
            domains=run.domains_processed,
        )

        try:
            _store_extraction_run_db(db, run)
        except Exception as e:
            logger.warning("extraction_run_db_store_failed", error=str(e))

        return run

    except Exception as e:
        run.status = "failed"
        run.error_message = str(e)
        run.completed_at = datetime.now(UTC)
        logger.error("schema_extraction_failed", error=str(e))
        return run

    finally:
        if close_db:
            try:
                next(db_gen)
            except StopIteration:
                pass


# --- DB persistence ---


def _store_extraction_run_db(db: Session, run: SchemaExtractionRun) -> None:
    """Persist a schema extraction run to the database."""
    from src.discovery.cq_database import SchemaExtractionRunRow

    pass_outputs_json = [po.model_dump(mode="json") for po in run.pass_outputs]
    row = SchemaExtractionRunRow(
        id=run.run_id,
        started_at=run.started_at,
        completed_at=run.completed_at,
        status=run.status,
        model=run.model,
        provider=run.provider,
        total_entity_types=run.total_entity_types,
        total_relationships=run.total_relationships,
        total_duration_ms=run.total_duration_ms,
        cqs_used=run.cqs_used,
        seed_reference_used=run.seed_reference_used,
        domains_processed=run.domains_processed,
        pass_outputs_json=pass_outputs_json,
        error_message=run.error_message,
    )
    db.add(row)
    db.commit()
    logger.info("extraction_run_persisted", run_id=run.run_id)


def load_extraction_run_from_db(db: Session, run_id: str) -> SchemaExtractionRun | None:
    """Load a schema extraction run from the database."""
    from src.discovery.cq_database import SchemaExtractionRunRow

    row = db.query(SchemaExtractionRunRow).filter(SchemaExtractionRunRow.id == run_id).first()
    if row is None:
        return None

    pass_outputs = []
    for po_data in (row.pass_outputs_json or []):
        pass_outputs.append(PassOutput.model_validate(po_data))

    return SchemaExtractionRun(
        run_id=row.id,
        started_at=row.started_at,
        completed_at=row.completed_at,
        status=row.status or "completed",
        model=row.model or "",
        provider=row.provider or "",
        pass_outputs=pass_outputs,
        total_entity_types=row.total_entity_types or 0,
        total_relationships=row.total_relationships or 0,
        total_duration_ms=row.total_duration_ms or 0,
        cqs_used=row.cqs_used or 0,
        seed_reference_used=row.seed_reference_used or False,
        domains_processed=row.domains_processed or [],
        error_message=row.error_message or "",
    )


def get_latest_completed_extraction_run_from_db(db: Session) -> SchemaExtractionRun | None:
    """Load the most recent completed extraction run from the database."""
    from src.discovery.cq_database import SchemaExtractionRunRow

    row = (
        db.query(SchemaExtractionRunRow)
        .filter(SchemaExtractionRunRow.status == "completed")
        .order_by(SchemaExtractionRunRow.started_at.desc())
        .first()
    )
    if row is None:
        return None
    return load_extraction_run_from_db(db, row.id)


def get_schema_run(run_id: str) -> SchemaExtractionRun | None:
    """Retrieve a schema extraction run by ID from in-memory storage."""
    return _schema_runs.get(run_id)


# Keep backward compat for old single-pass callers (tests may reference this)
run_schema_pass = run_stage1_pass


# --- CLI entry point ---


def main() -> None:
    """CLI entry point for CQ-driven schema extraction pipeline."""
    parser = argparse.ArgumentParser(description="GrACE two-stage schema extraction pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Build prompts only, no LLM calls")
    parser.add_argument("--domains", nargs="+", help="Specific domains to process")
    parser.add_argument("--passes", nargs="+", help="Specific passes to run")
    parser.add_argument("--industry", help="Industry profile for seed reference")
    args = parser.parse_args()

    result = asyncio.run(
        run_schema_extraction(
            dry_run=args.dry_run,
            domains=args.domains,
            passes=args.passes,
            industry_profile=args.industry,
        )
    )

    print(f"\nSchema extraction run: {result.run_id}")
    print(f"Status: {result.status}")
    print(f"CQs used: {result.cqs_used}")
    print(f"Seed reference: {'yes' if result.seed_reference_used else 'no'}")
    print(f"Total entity types: {result.total_entity_types}")
    print(f"Total relationships: {result.total_relationships}")
    print(f"Total duration: {result.total_duration_ms}ms")
    for po in result.pass_outputs:
        status = "OK" if po.success else f"FAILED: {po.error_message}"
        print(f"  {po.pass_name} / {po.domain}: {len(po.entity_types)} types, {len(po.relationships)} rels [{status}]")


if __name__ == "__main__":
    main()
