"""Seed registry loader, industry profile lookup, and source resolution."""

import json
from functools import lru_cache
from pathlib import Path

import structlog

from src.discovery.seed_models import IndustryProfile, SeedRegistry, SeedSource

logger = structlog.get_logger()

_REGISTRY_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "seed_registry.json"


@lru_cache
def load_seed_registry() -> SeedRegistry:
    """Load and cache the seed registry from config/seed_registry.json."""
    with open(_REGISTRY_PATH) as f:
        data = json.load(f)
    registry = SeedRegistry.model_validate(data)
    logger.info(
        "seed_registry_loaded",
        version=registry.version,
        sources=len(registry.sources),
        profiles=len(registry.industry_profiles),
    )
    return registry


def get_industry_profile(industry_id: str) -> IndustryProfile | None:
    """Look up an industry profile by ID. Returns None if not found."""
    registry = load_seed_registry()
    for profile in registry.industry_profiles:
        if profile.industry_id == industry_id:
            return profile
    return None


def list_industry_profiles() -> list[IndustryProfile]:
    """Return all available industry profiles."""
    return load_seed_registry().industry_profiles


def get_source_by_id(source_id: str) -> SeedSource | None:
    """Look up a seed source by ID. Returns None if not found."""
    registry = load_seed_registry()
    for source in registry.sources:
        if source.id == source_id:
            return source
    return None


def resolve_sources_for_industry(industry_id: str) -> list[SeedSource]:
    """Resolve all seed sources for an industry: universal + required + recommended.

    Returns deduplicated list of SeedSource objects.
    """
    registry = load_seed_registry()
    profile = get_industry_profile(industry_id)
    if profile is None:
        logger.warning("industry_profile_not_found", industry_id=industry_id)
        return []

    # Collect all source IDs: universal + required + recommended
    source_ids: list[str] = []
    seen: set[str] = set()

    for sid in registry.universal_sources + profile.required_seeds + profile.recommended_seeds:
        if sid not in seen:
            source_ids.append(sid)
            seen.add(sid)

    # Resolve to full SeedSource objects
    source_map = {s.id: s for s in registry.sources}
    sources = [source_map[sid] for sid in source_ids if sid in source_map]

    logger.info(
        "sources_resolved",
        industry_id=industry_id,
        count=len(sources),
        source_ids=[s.id for s in sources],
    )
    return sources
