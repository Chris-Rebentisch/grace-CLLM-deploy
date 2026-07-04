"""Tests for entity/relationship Pydantic models."""

from src.graph.entity_models import (
    BulkInsertRequest,
    EntityCreate,
    EntityUpdate,
    RelationshipCreate,
)


def test_entity_create_minimal():
    """EntityCreate with minimal fields validates."""
    entity = EntityCreate(entity_type="Person", properties={"name": "Alice"})
    assert entity.entity_type == "Person"
    assert entity.properties == {"name": "Alice"}
    assert entity.human_validated is False
    assert entity.valid_from is None


def test_entity_create_all_system_properties():
    """EntityCreate with all system properties validates."""
    entity = EntityCreate(
        entity_type="Legal_Entity",
        properties={"name": "Acme"},
        valid_from="2024-01-01T00:00:00Z",
        valid_to="2024-12-31T23:59:59Z",
        extraction_confidence=0.95,
        source_document_id="doc-123",
        extraction_event_id="evt-456",
        schema_version=3,
        ontology_module="core",
        human_validated=True,
    )
    assert entity.extraction_confidence == 0.95
    assert entity.schema_version == 3
    assert entity.human_validated is True


def test_bulk_insert_request_empty_relationships():
    """BulkInsertRequest with empty relationships validates."""
    req = BulkInsertRequest(
        entities=[EntityCreate(entity_type="Person", properties={"name": "Alice"})],
    )
    assert len(req.entities) == 1
    assert req.relationships == []
    assert req.extraction_event_id is None


def test_relationship_create_requires_ids():
    """RelationshipCreate requires source_grace_id and target_grace_id."""
    rel = RelationshipCreate(
        relationship_type="owns",
        source_grace_id="src-uuid",
        target_grace_id="tgt-uuid",
    )
    assert rel.source_grace_id == "src-uuid"
    assert rel.target_grace_id == "tgt-uuid"
    assert rel.properties == {}


def test_entity_update_empty_properties():
    """EntityUpdate with empty properties validates (edge case)."""
    update = EntityUpdate(properties={})
    assert update.properties == {}
