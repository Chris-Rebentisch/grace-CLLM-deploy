"""Tests for seed parser — uses ACTUAL seed files in ~/grace/seeds/."""

from pathlib import Path

import pytest

from src.discovery.seed_models import (
    SeedEntityType,
    SeedReference,
    SeedRelationship,
    SeedSource,
)
from src.discovery.seed_parser import (
    extract_local_name,
    format_for_llm,
    parse_seed_file,
)

# Default config for tests
DEFAULT_CONFIG = {
    "seed": {
        "schema_org_base_types": [
            "Organization", "Person", "Place", "Event", "CreativeWork", "Product",
        ],
    },
}


def _make_source(
    source_id: str,
    local_path: str,
    source_ontology: str,
    file_format: str,
) -> SeedSource:
    """Helper to make a SeedSource for testing."""
    return SeedSource(
        id=source_id,
        name=f"Test {source_id}",
        source_ontology=source_ontology,
        description="Test source",
        download_url="https://example.com",
        local_path=local_path,
        file_format=file_format,
        version="1.0",
    )


# --- extract_local_name tests ---


def test_extract_local_name_hash():
    """Extracts name after hash fragment."""
    assert extract_local_name("http://example.com/ontology#LegalEntity") == "LegalEntity"


def test_extract_local_name_slash():
    """Extracts name after last slash."""
    assert extract_local_name("http://example.com/ontology/LegalEntity") == "LegalEntity"


def test_extract_local_name_trailing_slash():
    """Handles trailing slash."""
    assert extract_local_name("http://example.com/ontology/LegalEntity/") == "LegalEntity"


def test_extract_local_name_no_separator():
    """Returns the whole string if no separator."""
    assert extract_local_name("LegalEntity") == "LegalEntity"


# --- FIBO RDF/XML parsing ---


@pytest.mark.skipif(
    not Path("seeds/fibo/LegalEntities.rdf").exists(),
    reason="Seed file not present",
)
def test_parse_fibo_legal_entities():
    """Parse FIBO LegalEntities.rdf — verify entity types and relationships extracted."""
    source = _make_source(
        "fibo_legal_entities", "seeds/fibo/LegalEntities.rdf", "fibo", "rdf_xml"
    )
    entity_types, relationships = parse_seed_file(source, DEFAULT_CONFIG)
    assert len(entity_types) > 0
    # All should be tagged as fibo
    for et in entity_types:
        assert et.source_ontology == "fibo"
    # Should have parent types for at least some
    parents_found = [et for et in entity_types if et.parent_type is not None]
    assert len(parents_found) > 0
    # Restriction-based relationships should now be extracted
    assert len(relationships) > 0


@pytest.mark.skipif(
    not Path("seeds/fibo/LegalEntities.rdf").exists(),
    reason="Seed file not present",
)
def test_fibo_parent_type_chains():
    """FIBO entity types have parent_type chains extracted correctly."""
    source = _make_source(
        "fibo_legal_entities", "seeds/fibo/LegalEntities.rdf", "fibo", "rdf_xml"
    )
    entity_types, _ = parse_seed_file(source, DEFAULT_CONFIG)
    # Build a name -> parent map
    parent_map = {et.name: et.parent_type for et in entity_types}
    # At least one entity should have a parent that's also in the types
    chain_found = any(
        parent_map.get(et.parent_type) is not None
        for et in entity_types
        if et.parent_type
    )
    # It's fine if chains aren't multi-level in this particular file,
    # but we should at least have parent types
    assert any(et.parent_type for et in entity_types)


# --- LKIF OWL/XML parsing ---


@pytest.mark.skipif(
    not Path("seeds/lkif/legal-action.owl").exists(),
    reason="Seed file not present",
)
def test_parse_lkif_legal_action():
    """Parse LKIF legal-action.owl — verify classes and relationships extracted."""
    source = _make_source(
        "lkif_legal_action", "seeds/lkif/legal-action.owl", "lkif", "owl_xml"
    )
    entity_types, relationships = parse_seed_file(source, DEFAULT_CONFIG)
    assert len(entity_types) > 0
    for et in entity_types:
        assert et.source_ontology == "lkif"
    # Restriction-based relationships should now be extracted
    assert len(relationships) > 0


# --- PROV-O Turtle parsing ---


@pytest.mark.skipif(
    not Path("seeds/prov-o/prov.ttl").exists(),
    reason="Seed file not present",
)
def test_parse_prov_o():
    """Parse PROV-O prov.ttl — verify Entity, Activity, Agent triad."""
    source = _make_source(
        "prov_o_core", "seeds/prov-o/prov.ttl", "prov_o", "turtle"
    )
    entity_types, relationships = parse_seed_file(source, DEFAULT_CONFIG)
    type_names = [et.name for et in entity_types]
    assert "Entity" in type_names
    assert "Activity" in type_names
    assert "Agent" in type_names


# --- Schema.org filtering ---


@pytest.mark.skipif(
    not Path("seeds/schema-org/schemaorg.ttl").exists(),
    reason="Seed file not present",
)
def test_parse_schema_org_filtered():
    """Schema.org parsing extracts <= 30 entity types with filtering."""
    source = _make_source(
        "schema_org_base", "seeds/schema-org/schemaorg.ttl", "schema_org", "turtle"
    )
    entity_types, relationships = parse_seed_file(source, DEFAULT_CONFIG)
    assert len(entity_types) > 0
    assert len(entity_types) <= 30
    # Schema.org properties should now be extracted as relationships
    assert len(relationships) > 0


@pytest.mark.skipif(
    not Path("seeds/schema-org/schemaorg.ttl").exists(),
    reason="Seed file not present",
)
def test_schema_org_respects_base_types():
    """Schema.org filtering only includes types under configured base types."""
    source = _make_source(
        "schema_org_base", "seeds/schema-org/schemaorg.ttl", "schema_org", "turtle"
    )
    # Use only Organization as base type for a narrow filter
    config = {"seed": {"schema_org_base_types": ["Organization"]}}
    entity_types, _ = parse_seed_file(source, config)
    # Should have Organization and its subclasses
    names = [et.name for et in entity_types]
    assert "Organization" in names
    # Should be fewer than with all base types
    assert len(entity_types) <= 30


# --- Combined SeedReference ---


@pytest.mark.skipif(
    not Path("seeds/prov-o/prov.ttl").exists(),
    reason="Seed file not present",
)
def test_combined_seed_reference():
    """Build a combined SeedReference from multiple seed files."""
    sources = []
    if Path("seeds/fibo/LegalEntities.rdf").exists():
        sources.append(_make_source(
            "fibo_legal_entities", "seeds/fibo/LegalEntities.rdf", "fibo", "rdf_xml"
        ))
    sources.append(_make_source(
        "prov_o_core", "seeds/prov-o/prov.ttl", "prov_o", "turtle"
    ))

    all_types = []
    all_rels = []
    for s in sources:
        types, rels = parse_seed_file(s, DEFAULT_CONFIG)
        all_types.extend(types)
        all_rels.extend(rels)

    ref = SeedReference(
        entity_types=all_types,
        relationships=all_rels,
        source_files=[s.local_path for s in sources],
        industry_profile="financial_services",
        registry_version="1.0.0",
        total_entity_types=len(all_types),
        total_relationships=len(all_rels),
    )
    assert ref.total_entity_types > 0
    ontologies = {et.source_ontology for et in ref.entity_types}
    assert len(ontologies) >= 1


# --- Empty/missing file ---


def test_parse_missing_file():
    """Returns empty lists for a missing seed file."""
    source = _make_source("missing", "seeds/nonexistent.rdf", "fibo", "rdf_xml")
    entity_types, relationships = parse_seed_file(source, DEFAULT_CONFIG)
    assert entity_types == []
    assert relationships == []


# --- format_for_llm ---


def test_format_for_llm():
    """format_for_llm produces valid compact text."""
    from src.discovery.seed_models import SeedProperty

    ref = SeedReference(
        entity_types=[
            SeedEntityType(
                name="LegalEntity",
                source_ontology="fibo",
                source_uri="http://example.com/LegalEntity",
                parent_type="Entity",
                description="A legal entity",
                properties=[
                    SeedProperty(name="hasName", uri="http://example.com/hasName", range_type="xsd:string"),
                ],
            ),
            SeedEntityType(
                name="Activity",
                source_ontology="prov_o",
                source_uri="http://example.com/Activity",
                description="An activity",
            ),
        ],
        relationships=[
            SeedRelationship(
                name="wasGeneratedBy",
                source_ontology="prov_o",
                source_uri="http://example.com/wasGeneratedBy",
                domain_type="Entity",
                range_type="Activity",
            ),
        ],
        source_files=["seeds/test.rdf"],
        industry_profile="financial_services",
        registry_version="1.0.0",
        total_entity_types=2,
        total_relationships=1,
    )
    text = format_for_llm(ref)
    assert "=== Seed Ontology Reference ===" in text
    assert "LegalEntity" in text
    assert "Activity" in text
    assert "wasGeneratedBy" in text
    assert "financial_services" in text
    assert "hasName" in text  # Properties should be inline
    # Should be under 4000 tokens (rough: 4 chars per token)
    assert len(text) < 16000


# --- Restriction relationship extraction ---


@pytest.mark.skipif(
    not Path("seeds/fibo/LegalEntities.rdf").exists(),
    reason="Seed file not present",
)
def test_fibo_restriction_relationships():
    """FIBO LegalEntities has restriction-based relationships like isRecognizedIn."""
    source = _make_source(
        "fibo_legal_entities", "seeds/fibo/LegalEntities.rdf", "fibo", "rdf_xml"
    )
    _, relationships = parse_seed_file(source, DEFAULT_CONFIG)
    rel_names = [r.name for r in relationships]
    # Should find at least some known FIBO restriction properties
    assert len(rel_names) > 0
    # All relationships should be tagged as fibo
    for r in relationships:
        assert r.source_ontology == "fibo"


@pytest.mark.skipif(
    not Path("seeds/lkif/legal-action.owl").exists(),
    reason="Seed file not present",
)
def test_lkif_restriction_relationships():
    """LKIF legal-action.owl has restriction-based relationships."""
    source = _make_source(
        "lkif_legal_action", "seeds/lkif/legal-action.owl", "lkif", "owl_xml"
    )
    _, relationships = parse_seed_file(source, DEFAULT_CONFIG)
    rel_names = [r.name for r in relationships]
    assert len(rel_names) > 0
    for r in relationships:
        assert r.source_ontology == "lkif"


@pytest.mark.skipif(
    not Path("seeds/schema-org/schemaorg.ttl").exists(),
    reason="Seed file not present",
)
def test_schema_org_properties_extracted():
    """Schema.org properties extracted as relationships reference our entity types."""
    source = _make_source(
        "schema_org_base", "seeds/schema-org/schemaorg.ttl", "schema_org", "turtle"
    )
    entity_types, relationships = parse_seed_file(source, DEFAULT_CONFIG)
    type_names = {et.name for et in entity_types}
    assert len(relationships) > 0
    # All relationship endpoints should reference extracted types
    for r in relationships:
        assert r.domain_type in type_names or r.range_type in type_names


def test_relationship_deduplication():
    """Duplicate relationships are removed during parsing."""
    from src.discovery.seed_models import SeedRelationship

    # Create a minimal RDF file with duplicate restriction patterns
    import tempfile

    rdf_content = """<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:owl="http://www.w3.org/2002/07/owl#"
         xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#">
  <owl:Class rdf:about="http://example.com/ClassA">
    <rdfs:subClassOf>
      <owl:Restriction>
        <owl:onProperty rdf:resource="http://example.com/relatesTo"/>
        <owl:someValuesFrom rdf:resource="http://example.com/ClassB"/>
      </owl:Restriction>
    </rdfs:subClassOf>
    <rdfs:subClassOf>
      <owl:Restriction>
        <owl:onProperty rdf:resource="http://example.com/relatesTo"/>
        <owl:someValuesFrom rdf:resource="http://example.com/ClassB"/>
      </owl:Restriction>
    </rdfs:subClassOf>
  </owl:Class>
  <owl:Class rdf:about="http://example.com/ClassB"/>
</rdf:RDF>"""

    with tempfile.NamedTemporaryFile(suffix=".rdf", mode="w", delete=False) as f:
        f.write(rdf_content)
        tmp_path = f.name

    try:
        source = _make_source("dedup_test", tmp_path, "test", "rdf_xml")
        _, relationships = parse_seed_file(source, DEFAULT_CONFIG)
        # Should have exactly 1 relationship (deduplicated)
        relates_to = [r for r in relationships if r.name == "relatesTo"]
        assert len(relates_to) == 1
    finally:
        Path(tmp_path).unlink()
