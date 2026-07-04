"""Tests for seed registry loader, industry lookup, and source resolution."""

import pytest

from src.discovery.seed_models import IndustryProfile, SeedRegistry, SeedSource
from src.discovery.seed_registry import (
    get_industry_profile,
    get_source_by_id,
    list_industry_profiles,
    load_seed_registry,
    resolve_sources_for_industry,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear lru_cache before each test."""
    load_seed_registry.cache_clear()
    yield
    load_seed_registry.cache_clear()


def test_load_seed_registry():
    """Loads the actual config/seed_registry.json and validates."""
    registry = load_seed_registry()
    assert isinstance(registry, SeedRegistry)
    assert registry.version == "1.0.0"
    assert len(registry.sources) == 11
    assert len(registry.industry_profiles) == 8


def test_registry_version():
    """Registry has a version field."""
    registry = load_seed_registry()
    assert registry.version


def test_get_industry_profile_valid():
    """Returns profile for a valid industry_id."""
    profile = get_industry_profile("financial_services")
    assert profile is not None
    assert isinstance(profile, IndustryProfile)
    assert profile.name == "Financial Services"
    assert len(profile.required_seeds) > 0


def test_get_industry_profile_invalid():
    """Returns None for an unknown industry_id."""
    assert get_industry_profile("nonexistent") is None


def test_list_industry_profiles():
    """Returns all 8 profiles."""
    profiles = list_industry_profiles()
    assert len(profiles) == 8
    ids = [p.industry_id for p in profiles]
    assert "financial_services" in ids
    assert "general" in ids


def test_resolve_sources_includes_universal():
    """Resolved sources include universal sources (schema_org_base, prov_o_core)."""
    sources = resolve_sources_for_industry("general")
    source_ids = [s.id for s in sources]
    assert "schema_org_base" in source_ids
    assert "prov_o_core" in source_ids


def test_resolve_sources_includes_required_and_recommended():
    """Resolved sources include required + recommended seeds."""
    sources = resolve_sources_for_industry("financial_services")
    source_ids = [s.id for s in sources]
    # Required
    assert "fibo_legal_entities" in source_ids
    assert "fibo_ownership" in source_ids
    assert "lkif_norm" in source_ids
    # Recommended
    assert "fibo_formal_organizations" in source_ids
    assert "lkif_legal_action" in source_ids


def test_resolve_sources_deduplicates():
    """Resolved sources have no duplicates."""
    sources = resolve_sources_for_industry("financial_services")
    source_ids = [s.id for s in sources]
    assert len(source_ids) == len(set(source_ids))


def test_resolve_sources_invalid_industry():
    """Returns empty list for unknown industry."""
    sources = resolve_sources_for_industry("nonexistent_industry")
    assert sources == []


def test_get_source_by_id_valid():
    """Returns a source for a valid ID."""
    source = get_source_by_id("fibo_legal_entities")
    assert source is not None
    assert isinstance(source, SeedSource)
    assert source.source_ontology == "fibo"


def test_get_source_by_id_invalid():
    """Returns None for an unknown source ID."""
    assert get_source_by_id("nonexistent_source") is None
