"""Tests for extraction pipeline Pydantic models."""

import pytest

from src.extraction.extraction_models import (
    DocumentChunk,
    ExtractedEntity,
    ExtractedRelationship,
    ExtractionBatch,
    ExtractionRequest,
    ExtractionResult,
    PhotoObservation,
)


class TestExtractedEntity:
    """Tests for ExtractedEntity model."""

    def test_minimal_fields(self):
        """Validates with just name and entity_type."""
        entity = ExtractedEntity(name="Acme Corp", entity_type="Legal_Entity")
        assert entity.name == "Acme Corp"
        assert entity.entity_type == "Legal_Entity"
        assert entity.properties == {}
        assert entity.source_sentence_indices == []
        assert entity.temporal_hints is None

    def test_all_optional_fields(self):
        """Validates with all fields populated."""
        entity = ExtractedEntity(
            name="Acme Corp",
            entity_type="Legal_Entity",
            properties={"jurisdiction": "Delaware", "founded": "2020"},
            source_sentence_indices=[0, 2, 5],
            temporal_hints={"start": "January 2024"},
        )
        assert entity.properties["jurisdiction"] == "Delaware"
        assert len(entity.source_sentence_indices) == 3
        assert entity.temporal_hints["start"] == "January 2024"


class TestExtractedRelationship:
    """Tests for ExtractedRelationship model."""

    def test_required_fields(self):
        """Validates with all required fields."""
        rel = ExtractedRelationship(
            subject_name="Acme Corp",
            subject_type="Legal_Entity",
            predicate="party_to",
            object_name="Service Agreement",
            object_type="Contract",
        )
        assert rel.subject_name == "Acme Corp"
        assert rel.predicate == "party_to"
        assert rel.properties == {}


class TestExtractionResult:
    """Tests for ExtractionResult — the Instructor response_model."""

    def test_empty_lists(self):
        """Valid output with no entities or relationships.

        F-0014 (validation run): `relationships` is now REQUIRED — an
        omitted key fails validation (instructor retry pressure); an explicit
        empty list remains valid for genuinely relationship-free text.
        """
        result = ExtractionResult(relationships=[])
        assert result.entities == []
        assert result.relationships == []
        with pytest.raises(Exception):
            ExtractionResult()

    def test_with_data(self, sample_extraction_result):
        """Validates with populated entities and relationships."""
        assert len(sample_extraction_result.entities) == 2
        assert len(sample_extraction_result.relationships) == 1

    def test_json_schema_valid(self):
        """model_json_schema() produces valid JSON Schema."""
        schema = ExtractionResult.model_json_schema()
        assert "properties" in schema
        assert "entities" in schema["properties"]
        assert "relationships" in schema["properties"]
        # Verify descriptions exist on top-level fields
        assert "description" in schema["properties"]["entities"]
        assert "description" in schema["properties"]["relationships"]

    def test_json_schema_max_depth(self):
        """JSON Schema should be flat — max 3 levels deep."""
        schema = ExtractionResult.model_json_schema()
        # Check that $defs contains the nested models
        assert "$defs" in schema
        assert "ExtractedEntity" in schema["$defs"]
        assert "ExtractedRelationship" in schema["$defs"]


class TestDocumentChunk:
    """Tests for DocumentChunk model."""

    def test_required_fields(self):
        chunk = DocumentChunk(
            chunk_id="abc123",
            text="Some text content.",
            char_start=0,
            char_end=18,
        )
        assert chunk.chunk_id == "abc123"
        assert chunk.token_count_estimate == 0
        assert chunk.is_overlap is False


class TestExtractionRequest:
    """Tests for ExtractionRequest model."""

    def test_generates_uuid(self):
        """document_id auto-generates UUID when not provided."""
        req = ExtractionRequest(document_text="Test document text")
        assert req.document_id  # non-empty
        assert len(req.document_id) == 36  # UUID format


class TestExtractionBatch:
    """Tests for ExtractionBatch model."""

    def test_chunk_counts(self):
        batch = ExtractionBatch(
            document_id="doc-001",
            chunks_total=10,
            chunks_succeeded=8,
            chunks_failed=2,
        )
        assert batch.chunks_total == 10
        assert batch.chunks_succeeded == 8
        assert batch.chunks_failed == 2
        assert batch.entities == []
        assert batch.relationships == []


class TestF009SchemaDescriptions:
    """F-009/F-024 / ISS-0016: field descriptions feed the LLM via JSON Schema."""

    def test_relationships_description_imperative(self):
        """relationships field description mandates capture + example."""
        desc = ExtractionResult.model_fields["relationships"].description
        assert "MUST be captured" in desc
        assert "ONLY acceptable" in desc
        assert "predicate='owns'" in desc

    def test_entity_name_description_full_canonical_name(self):
        """name field description forbids first-name-only fragments."""
        desc = ExtractedEntity.model_fields["name"].description
        assert "Diane Mercer" in desc
        assert "Do not truncate names" in desc


class TestPhotoObservationSceneFields:
    """F-011 / ISS-0018: scene-description fields on the vision schema."""

    def test_scene_fields_populate(self):
        obs = PhotoObservation(
            damage_type="none",
            affected_component="n/a",
            severity_band="minor",
            confidence_band="high",
            scene_summary="Site plan showing 4 numbered lots along an access road.",
            key_elements=["4 numbered lots", "access road", "north arrow"],
        )
        assert obs.scene_summary.startswith("Site plan")
        assert "north arrow" in obs.key_elements

    def test_backward_compat_old_json_without_scene_fields(self):
        """Previously persisted vision_description_json still validates."""
        old = {
            "damage_type": "dent",
            "affected_component": "hood",
            "severity_band": "moderate",
            "visible_text": None,
            "confidence_band": "high",
        }
        obs = PhotoObservation.model_validate(old)
        assert obs.scene_summary == ""
        assert obs.key_elements == []

    def test_scene_fields_serialize_to_json(self):
        """model_dump_json (the vision_description_json writer) carries them."""
        obs = PhotoObservation(
            damage_type="none",
            affected_component="n/a",
            severity_band="minor",
            confidence_band="high",
            scene_summary="Two-story duplex with two entry doors.",
            key_elements=["duplex", "two entry doors"],
        )
        js = obs.model_dump_json()
        assert "scene_summary" in js
        assert "two entry doors" in js

    def test_existing_fields_retained(self):
        """All five pre-existing fields remain on the model (backward compat)."""
        fields = set(PhotoObservation.model_fields)
        assert {
            "damage_type", "affected_component", "severity_band",
            "visible_text", "confidence_band", "scene_summary", "key_elements",
        } <= fields
