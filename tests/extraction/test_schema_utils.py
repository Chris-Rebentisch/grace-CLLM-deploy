"""Tests for schema_utils.extract_allowed_types."""

from src.extraction.schema_utils import extract_allowed_types


class TestExtractAllowedTypes:
    def test_flat_grace_format(self, sample_ontology_schema):
        """Flat schema with entity_types + relationships -> both sets populated."""
        entity_types, predicates = extract_allowed_types(sample_ontology_schema)
        assert "Legal_Entity" in entity_types
        assert "Contract" in entity_types
        assert "party_to" in predicates

    def test_defs_format(self):
        """$defs schema -> entity types from keys, empty predicates."""
        schema = {"$defs": {"Legal_Entity": {}, "Contract": {}}}
        entity_types, predicates = extract_allowed_types(schema)
        assert entity_types == {"Legal_Entity", "Contract"}
        assert predicates == set()

    def test_empty_schema(self):
        """Schema with no recognized keys -> empty sets, log warning."""
        entity_types, predicates = extract_allowed_types({"foo": "bar"})
        assert entity_types == set()
        assert predicates == set()

    def test_flat_with_no_relationships(self):
        """entity_types present but no relationships key -> entities populated,
        predicates empty."""
        schema = {"entity_types": {"Person": {}}}
        entity_types, predicates = extract_allowed_types(schema)
        assert entity_types == {"Person"}
        assert predicates == set()

    def test_both_keys_populated(self, sample_ontology_schema):
        """Verify exact sets returned for fixture schema."""
        entity_types, predicates = extract_allowed_types(sample_ontology_schema)
        assert entity_types == {"Legal_Entity", "Contract"}
        assert predicates == {"party_to"}
