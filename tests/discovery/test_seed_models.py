"""Tests for seed Pydantic models."""

from datetime import UTC, datetime

import pytest

from src.discovery.seed_models import (
    IndustryProfile,
    ProvisioningResult,
    SeedEntityType,
    SeedProperty,
    SeedReference,
    SeedRegistry,
    SeedRelationship,
    SeedSource,
    SeedStatus,
    SeedSuggestion,
    SuggestionResponse,
)


def test_seed_source_validation():
    """SeedSource validates with all required fields."""
    source = SeedSource(
        id="test_source",
        name="Test Source",
        source_ontology="fibo",
        description="A test source",
        download_url="https://example.com/test.rdf",
        local_path="seeds/test.rdf",
        file_format="rdf_xml",
        version="1.0",
        domains=["legal"],
        industry_verticals=["financial_services"],
    )
    assert source.id == "test_source"
    assert source.file_format == "rdf_xml"
    assert source.domains == ["legal"]


def test_industry_profile_validation():
    """IndustryProfile validates with required and recommended seeds."""
    profile = IndustryProfile(
        industry_id="test_industry",
        name="Test Industry",
        description="A test industry profile",
        required_seeds=["seed_a", "seed_b"],
        recommended_seeds=["seed_c"],
    )
    assert profile.industry_id == "test_industry"
    assert len(profile.required_seeds) == 2
    assert profile.recommended_seeds == ["seed_c"]


def test_seed_registry_validation():
    """SeedRegistry validates with sources and profiles."""
    registry = SeedRegistry(
        version="1.0.0",
        universal_sources=["schema_org_base"],
        sources=[
            SeedSource(
                id="schema_org_base",
                name="Schema.org",
                source_ontology="schema_org",
                description="Base types",
                download_url="https://schema.org/test.ttl",
                local_path="seeds/schema.ttl",
                file_format="turtle",
                version="28.0",
            )
        ],
        industry_profiles=[
            IndustryProfile(
                industry_id="general",
                name="General",
                description="General profile",
                required_seeds=["schema_org_base"],
            )
        ],
    )
    assert registry.version == "1.0.0"
    assert len(registry.sources) == 1
    assert len(registry.industry_profiles) == 1


def test_seed_reference_construction():
    """SeedReference can be constructed with sample data."""
    entity = SeedEntityType(
        name="LegalEntity",
        source_ontology="fibo",
        source_uri="http://example.com/LegalEntity",
        parent_type="Entity",
        description="A legal entity",
        properties=[
            SeedProperty(
                name="hasJurisdiction",
                uri="http://example.com/hasJurisdiction",
                range_type="xsd:string",
                description="Jurisdiction",
            )
        ],
    )
    rel = SeedRelationship(
        name="isOwnerOf",
        source_ontology="fibo",
        source_uri="http://example.com/isOwnerOf",
        domain_type="LegalEntity",
        range_type="Asset",
        description="Ownership relationship",
    )
    ref = SeedReference(
        entity_types=[entity],
        relationships=[rel],
        source_files=["seeds/test.rdf"],
        industry_profile="financial_services",
        registry_version="1.0.0",
        total_entity_types=1,
        total_relationships=1,
    )
    assert ref.total_entity_types == 1
    assert ref.total_relationships == 1
    assert ref.entity_types[0].properties[0].name == "hasJurisdiction"
    assert isinstance(ref.extracted_at, datetime)


def test_seed_property_defaults():
    """SeedProperty has sensible defaults."""
    prop = SeedProperty(
        name="testProp",
        uri="http://example.com/testProp",
        range_type="xsd:string",
    )
    assert prop.description == ""


def test_seed_status_model():
    """SeedStatus model validates correctly."""
    status = SeedStatus(
        source_id="test",
        name="Test",
        is_downloaded=True,
        is_parsed=False,
        local_path="seeds/test.rdf",
        file_size_bytes=1024,
    )
    assert status.is_downloaded is True
    assert status.entity_types_count is None


def test_provisioning_result_model():
    """ProvisioningResult model validates correctly."""
    result = ProvisioningResult(
        industry_profile="financial_services",
        sources_downloaded=["fibo_legal_entities"],
        sources_already_present=["schema_org_base"],
        sources_failed=[],
        total_files=2,
        errors=[],
    )
    assert result.total_files == 2
    assert len(result.sources_failed) == 0


def test_suggestion_response_model():
    """SuggestionResponse model validates correctly."""
    resp = SuggestionResponse(
        suggestions=[
            SeedSuggestion(
                source_id="lkif_norm",
                reason="Documents mention regulatory compliance",
                confidence=0.85,
                relevant_domains=["legal"],
            )
        ]
    )
    assert len(resp.suggestions) == 1
    assert resp.suggestions[0].confidence == 0.85
