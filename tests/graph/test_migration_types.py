"""Tests for migration meta-entity types and deprecation system properties."""

from src.graph.ddl_generator import generate_full_schema_ddl, generate_meta_entity_ddl
from src.graph.migration_types import (
    CORRECTION_EVENT_PROPERTIES,
    META_ENTITY_TYPES,
    MIGRATION_EVENT_PROPERTIES,
)
from src.graph.system_properties import EDGE_SYSTEM_PROPERTIES, VERTEX_SYSTEM_PROPERTIES


def test_migration_event_ddl_generated():
    """Migration_Event vertex type with all properties is generated."""
    stmts = generate_meta_entity_ddl()
    assert any("CREATE VERTEX TYPE Migration_Event IF NOT EXISTS" in s for s in stmts)
    for prop in MIGRATION_EVENT_PROPERTIES:
        expected = f"CREATE PROPERTY Migration_Event.{prop['name']} IF NOT EXISTS {prop['type']}"
        assert expected in stmts


def test_correction_event_ddl_generated():
    """Correction_Event vertex type with all properties is generated."""
    stmts = generate_meta_entity_ddl()
    assert any("CREATE VERTEX TYPE Correction_Event IF NOT EXISTS" in s for s in stmts)
    for prop in CORRECTION_EVENT_PROPERTIES:
        expected = f"CREATE PROPERTY Correction_Event.{prop['name']} IF NOT EXISTS {prop['type']}"
        assert expected in stmts


def test_meta_types_in_full_schema():
    """Meta-entity types are included in generate_full_schema_ddl output."""
    schema = {
        "entity_types": {
            "Person": {"properties": {"name": {"data_type": "string"}}},
        },
        "relationships": {},
    }
    stmts = generate_full_schema_ddl(schema)
    assert any("Migration_Event" in s for s in stmts)
    assert any("Correction_Event" in s for s in stmts)


def test_deprecation_properties_in_system():
    """_deprecated and _deprecated_at are present in system properties."""
    vertex_names = [p["name"] for p in VERTEX_SYSTEM_PROPERTIES]
    assert "_deprecated" in vertex_names
    assert "_deprecated_at" in vertex_names

    edge_names = [p["name"] for p in EDGE_SYSTEM_PROPERTIES]
    assert "_deprecated" in edge_names
    assert "_deprecated_at" in edge_names
