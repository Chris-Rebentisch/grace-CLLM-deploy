"""LLM-powered seed suggestion from document metadata and CQ corpus."""

import json

import structlog
from sqlalchemy.orm import Session

from src.discovery.seed_models import SeedSuggestion, SuggestionResponse
from src.discovery.seed_registry import (
    get_industry_profile,
    load_seed_registry,
    resolve_sources_for_industry,
)
from src.shared.llm_provider import LLMResponse, get_provider

logger = structlog.get_logger()

SYSTEM_PROMPT = """You are an ontology engineer analyzing an organization's documents and competency questions
to recommend additional ontology seed sources. You have access to a registry of available
ontology sources. Recommend sources that would help model entity types and relationships
mentioned in the documents and CQs but not covered by the currently selected seeds.
Respond with ONLY valid JSON."""


def _build_suggestion_prompt(
    doc_summary: dict,
    cq_summary: dict,
    industry_id: str,
    available_sources: list[dict],
) -> str:
    """Build the user prompt for the seed suggester."""
    prompt_data = {
        "document_summary": doc_summary,
        "cq_summary": cq_summary,
        "current_industry_profile": industry_id,
        "available_seed_sources": available_sources,
        "instructions": (
            "Analyze the documents and CQs. Recommend seed sources from the available list "
            "that would help model entity types and relationships not yet covered. "
            "Return JSON with format: {\"suggestions\": [{\"source_id\": \"...\", "
            "\"reason\": \"...\", \"confidence\": 0.0-1.0, \"relevant_domains\": [...]}]}"
        ),
    }
    return json.dumps(prompt_data, indent=2)


async def suggest_additional_seeds(
    config: dict, db: Session | None = None
) -> list[SeedSuggestion]:
    """Use the LLM to suggest additional seed sources based on document and CQ analysis.

    Args:
        config: Discovery config dict (used for industry_profile and LLM settings).
        db: Optional database session for fetching document/CQ summaries.

    Returns:
        List of SeedSuggestion objects.
    """
    seed_config = config.get("seed", {})
    industry_id = seed_config.get("industry_profile", "")

    if not industry_id:
        logger.warning("no_industry_profile_set")
        return []

    # Get current sources (already selected)
    current_sources = resolve_sources_for_industry(industry_id)
    current_ids = {s.id for s in current_sources}

    # Get available sources not yet selected
    registry = load_seed_registry()
    available = [
        {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "domains": s.domains,
        }
        for s in registry.sources
        if s.id not in current_ids
    ]

    if not available:
        logger.info("no_additional_seeds_available")
        return []

    # Get document and CQ summaries
    doc_summary: dict = {}
    cq_summary: dict = {}

    if db is not None:
        try:
            from src.discovery.database import get_processing_summary
            doc_summary = get_processing_summary(db)
        except Exception as e:
            logger.warning("doc_summary_fetch_failed", error=str(e))

        try:
            from src.discovery.cq_database import get_cq_summary
            cq_summary = get_cq_summary(db)
        except Exception as e:
            logger.warning("cq_summary_fetch_failed", error=str(e))

    if not doc_summary and not cq_summary:
        logger.info("no_document_or_cq_data_for_suggestions")
        return []

    # Build prompt and call LLM
    user_prompt = _build_suggestion_prompt(
        doc_summary, cq_summary, industry_id, available
    )

    provider = get_provider()

    try:
        # D444 — schema-conformant by construction on Tier A; Tier-B recovery in provider layer (D444.3). Authorization: D444.
        response: LLMResponse = await provider.generate_structured(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_model=SuggestionResponse,
            temperature=0.3,
        )
        suggestion_resp = response.parsed
        logger.info(
            "seed_suggestions_generated",
            count=len(suggestion_resp.suggestions),
        )
        return suggestion_resp.suggestions
    except Exception as e:
        logger.error("seed_suggestion_failed", error=str(e))
        return []
