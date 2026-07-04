"""Tier 3: LLM calls for canonical phrasing, hierarchy, and gap analysis."""

import structlog

from src.discovery.merge_models import Call1Response, Call2Response, Call3Response
from src.discovery.merge_prompts import (
    build_call1_prompt,
    build_call2_prompt,
    build_call3_prompt,
)
from src.shared.llm_provider import get_provider

logger = structlog.get_logger()


async def call1_canonical_phrasing(
    clusters_data: list[dict], config: dict
) -> Call1Response | None:
    """Tier 3 Call 1: Select canonical phrasing for each cluster.

    Uses the LLM to choose the best representative CQ text for each semantic
    cluster and recommend splits for semantically distinct members.

    Args:
        clusters_data: List of cluster dicts with members.
        config: cq_merge config section.

    Returns:
        Call1Response or None if LLM call or validation fails.
    """
    provider = get_provider()
    system_prompt, user_prompt = build_call1_prompt(clusters_data)

    logger.info(
        "call1_start",
        n_clusters=len(clusters_data),
        provider=provider.provider_name,
    )

    try:
        # D444 — schema-conformant by construction on Tier A; Tier-B recovery in provider layer (D444.3). Authorization: D444.
        response = await provider.generate_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_model=Call1Response,
            temperature=0.0,
        )
        result = response.parsed
        logger.info(
            "call1_complete",
            n_clusters_returned=len(result.clusters),
            duration_ms=response.duration_ms,
        )
        return result
    except Exception as e:
        logger.error("call1_error", error=str(e))
        return None


async def call2_hierarchy(
    canonical_cqs: list[dict], singletons: list[dict], config: dict
) -> Call2Response | None:
    """Tier 3 Call 2: Organize CQs into domain/sub-domain hierarchy.

    Uses the LLM to build a hierarchical organization of canonical CQs
    and singletons, and identify cross-domain relationships.

    Args:
        canonical_cqs: List of canonical CQ dicts.
        singletons: List of singleton CQ dicts.
        config: cq_merge config section.

    Returns:
        Call2Response or None if LLM call or validation fails.
    """
    provider = get_provider()
    system_prompt, user_prompt = build_call2_prompt(canonical_cqs, singletons)

    logger.info(
        "call2_start",
        n_canonical=len(canonical_cqs),
        n_singletons=len(singletons),
        provider=provider.provider_name,
    )

    try:
        # D444 — schema-conformant by construction on Tier A; Tier-B recovery in provider layer (D444.3). Authorization: D444.
        response = await provider.generate_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_model=Call2Response,
            temperature=0.0,
        )
        result = response.parsed
        logger.info(
            "call2_complete",
            n_domain_groups=len(result.domain_groups),
            n_cross_links=len(result.cross_domain_links),
            duration_ms=response.duration_ms,
        )
        return result
    except Exception as e:
        logger.error("call2_error", error=str(e))
        return None


async def call3_gap_analysis(
    hierarchy: dict,
    gap_report: dict,
    canonical_cqs: list[dict],
    singletons: list[dict],
    config: dict,
) -> Call3Response | None:
    """Tier 3 Call 3: Gap analysis, gap-fill CQ generation, and path annotation.

    Uses the LLM to propose new CQs that fill coverage gaps and annotate
    existing CQs with expected ontology paths.

    Args:
        hierarchy: Call 2 response as dict.
        gap_report: GapReport as dict.
        canonical_cqs: List of canonical CQ dicts.
        singletons: List of singleton CQ dicts.
        config: cq_merge config section.

    Returns:
        Call3Response or None if LLM call or validation fails.
    """
    gap_fill_max = config.get("gap_fill_max_cqs", 15)
    provider = get_provider()
    system_prompt, user_prompt = build_call3_prompt(
        hierarchy, gap_report, canonical_cqs, singletons, gap_fill_max
    )

    logger.info(
        "call3_start",
        gap_fill_max=gap_fill_max,
        provider=provider.provider_name,
    )

    try:
        # D444 — schema-conformant by construction on Tier A; Tier-B recovery in provider layer (D444.3). Authorization: D444.
        response = await provider.generate_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_model=Call3Response,
            temperature=0.3,
        )
        result = response.parsed

        # Enforce gap-fill cap
        if len(result.gap_fill_cqs) > gap_fill_max:
            logger.warning(
                "call3_gap_fill_cap_exceeded",
                returned=len(result.gap_fill_cqs),
                cap=gap_fill_max,
                msg=f"Truncating gap-fill CQs from {len(result.gap_fill_cqs)} to {gap_fill_max}",
            )
            result.gap_fill_cqs = result.gap_fill_cqs[:gap_fill_max]

        logger.info(
            "call3_complete",
            n_gap_fills=len(result.gap_fill_cqs),
            n_path_annotations=len(result.path_annotations),
            duration_ms=response.duration_ms,
        )
        return result
    except Exception as e:
        logger.error("call3_error", error=str(e))
        return None
