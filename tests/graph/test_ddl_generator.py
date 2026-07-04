"""Tests for DDL statement generation."""

from src.graph.ddl_generator import (
    generate_edge_type_ddl,
    generate_full_schema_ddl,
    generate_system_properties_ddl,
    generate_vertex_type_ddl,
)
from src.graph.system_properties import EDGE_SYSTEM_PROPERTIES, VERTEX_SYSTEM_PROPERTIES


def test_vertex_type_ddl_basic():
    """Single type with properties generates CREATE VERTEX TYPE + property statements."""
    stmts = generate_vertex_type_ddl(
        "Legal_Entity",
        {"name": {"data_type": "string"}, "jurisdiction": {"data_type": "string"}},
    )
    assert stmts[0] == "CREATE VERTEX TYPE Legal_Entity IF NOT EXISTS"
    assert "CREATE PROPERTY Legal_Entity.name IF NOT EXISTS STRING" in stmts
    assert "CREATE PROPERTY Legal_Entity.jurisdiction IF NOT EXISTS STRING" in stmts
    assert len(stmts) == 3


def test_vertex_type_ddl_all_data_types():
    """Covers every type mapping in a single vertex type."""
    props = {
        "s": {"data_type": "string"},
        "i": {"data_type": "integer"},
        "l": {"data_type": "long"},
        "f": {"data_type": "float"},
        "d": {"data_type": "double"},
        "b": {"data_type": "boolean"},
        "dt": {"data_type": "date"},
        "dtt": {"data_type": "datetime"},
        "r": {"data_type": "reference"},
        "li": {"data_type": "list"},
        "t": {"data_type": "text"},
    }
    stmts = generate_vertex_type_ddl("AllTypes", props)
    # 1 CREATE VERTEX TYPE + 11 properties
    assert len(stmts) == 12
    # float -> DOUBLE
    assert "CREATE PROPERTY AllTypes.f IF NOT EXISTS DOUBLE" in stmts
    # text -> STRING
    assert "CREATE PROPERTY AllTypes.t IF NOT EXISTS STRING" in stmts


def test_edge_type_ddl_basic():
    """Edge with properties generates correct DDL."""
    rel_def = {
        "source_type": "Legal_Entity",
        "target_type": "Legal_Entity",
        "properties": {"ownership_percentage": {"data_type": "float"}},
    }
    stmts = generate_edge_type_ddl("owns", rel_def)
    assert stmts[0] == "CREATE EDGE TYPE owns IF NOT EXISTS"
    assert "CREATE PROPERTY owns.ownership_percentage IF NOT EXISTS DOUBLE" in stmts
    assert len(stmts) == 2


def test_edge_type_ddl_no_properties():
    """Edge without properties generates only CREATE EDGE TYPE."""
    stmts = generate_edge_type_ddl("related_to", {"source_type": "A", "target_type": "B"})
    assert len(stmts) == 1
    assert stmts[0] == "CREATE EDGE TYPE related_to IF NOT EXISTS"


def test_edge_type_ddl_edge_properties_key():
    """Ratified relationships carry attributes under ``edge_properties``.

    ``_relationship_to_schema`` in src/ontology/review_ops.py projects
    relationship attributes under ``edge_properties``, not ``properties``.
    The generator must emit CREATE PROPERTY for those domain edge attributes
    so attributed edges keep their attributes when synced to ArcadeDB.
    """
    rel_def = {
        "source_type": "Legal_Entity",
        "target_type": "License",
        "edge_properties": {
            "license_scope": {"data_type": "string"},
            "exclusive": {"data_type": "boolean"},
        },
    }
    stmts = generate_edge_type_ddl("grants_license", rel_def)
    assert "CREATE PROPERTY grants_license.license_scope IF NOT EXISTS STRING" in stmts
    assert "CREATE PROPERTY grants_license.exclusive IF NOT EXISTS BOOLEAN" in stmts


def test_full_schema_ddl_edge_properties_emitted():
    """Full-schema DDL emits domain edge properties from ``edge_properties``."""
    schema = {
        "entity_types": {
            "Legal_Entity": {"properties": {"name": {"data_type": "string"}}},
        },
        "relationships": {
            "party_to": {
                "source_type": "Legal_Entity",
                "target_type": "Legal_Entity",
                "edge_properties": {"party_role": {"data_type": "string"}},
            },
        },
    }
    stmts = generate_full_schema_ddl(schema)
    assert "CREATE PROPERTY party_to.party_role IF NOT EXISTS STRING" in stmts


def test_system_properties_vertex():
    """All vertex system properties are generated."""
    stmts = generate_system_properties_ddl("Person", is_edge=False)
    assert len(stmts) == len(VERTEX_SYSTEM_PROPERTIES)
    for prop in VERTEX_SYSTEM_PROPERTIES:
        expected = f"CREATE PROPERTY Person.{prop['name']} IF NOT EXISTS {prop['type']}"
        assert expected in stmts


def test_system_properties_edge():
    """All edge system properties are generated (including edge-specific)."""
    stmts = generate_system_properties_ddl("owns", is_edge=True)
    assert len(stmts) == len(EDGE_SYSTEM_PROPERTIES)
    # Edge-specific property
    assert "CREATE PROPERTY owns.relationship_confidence IF NOT EXISTS DOUBLE" in stmts


def test_full_schema_ddl_order():
    """Vertices are created before edges in full schema DDL."""
    schema = {
        "entity_types": {
            "Person": {"properties": {"name": {"data_type": "string"}}},
        },
        "relationships": {
            "knows": {
                "source_type": "Person",
                "target_type": "Person",
                "properties": {},
            },
        },
    }
    stmts = generate_full_schema_ddl(schema)
    vertex_idx = next(i for i, s in enumerate(stmts) if "VERTEX TYPE Person" in s)
    edge_idx = next(i for i, s in enumerate(stmts) if "EDGE TYPE knows" in s)
    assert vertex_idx < edge_idx


def test_full_schema_ddl_empty():
    """Empty schema produces only meta-entity/edge DDL."""
    stmts = generate_full_schema_ddl({})
    # No domain types, but meta types are always included
    assert any("Migration_Event" in s for s in stmts)
    assert any("Correction_Event" in s for s in stmts)
    assert any("Extraction_Event" in s for s in stmts)
    assert any("EDGE TYPE produced_by" in s for s in stmts)
    assert len(stmts) > 0


def test_idempotent_if_not_exists():
    """Every DDL statement contains IF NOT EXISTS."""
    schema = {
        "entity_types": {
            "Company": {"properties": {"name": {"data_type": "string"}}},
        },
        "relationships": {
            "employs": {
                "source_type": "Company",
                "target_type": "Person",
                "properties": {"since": {"data_type": "date"}},
            },
        },
    }
    stmts = generate_full_schema_ddl(schema)
    for stmt in stmts:
        assert "IF NOT EXISTS" in stmt, f"Missing IF NOT EXISTS: {stmt}"


def test_edge_constraints_flag():
    """with_constraints=True generates @in/@out link properties."""
    rel_def = {
        "source_type": "Company",
        "target_type": "Person",
        "properties": {},
    }
    stmts = generate_edge_type_ddl("employs", rel_def, with_constraints=True)
    assert "CREATE PROPERTY employs.@out IF NOT EXISTS LINK Company" in stmts
    assert "CREATE PROPERTY employs.@in IF NOT EXISTS LINK Person" in stmts
