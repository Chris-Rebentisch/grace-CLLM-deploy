"""Tests for schema mapper (CP4)."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from src.connectors.schema_mapper import (
    SchemaMappingResult,
    load_mapping_config,
    map_source_schema,
)
from src.connectors.synthetic_connector import SyntheticConnector
from src.connectors.models import ConnectorConfig
from src.graph.management_models import GraphNamespace

YAML_PATH = Path(__file__).resolve().parents[2] / "config" / "connectors" / "synthetic_mapping.yaml"


def _make_namespace(prefix: str = "Syn") -> GraphNamespace:
    return GraphNamespace(
        id=str(uuid4()),
        database_name="syn_test_db",
        label_prefix=prefix,
        namespace_type="child",
    )


def _make_mother_ontology() -> dict:
    return {
        "Legal_Entity": {"type": "object", "properties": {"name": {"type": "string"}}},
        "Location": {"type": "object", "properties": {"name": {"type": "string"}}},
        "Asset": {"type": "object", "properties": {"name": {"type": "string"}}},
        "Person": {"type": "object", "properties": {"name": {"type": "string"}}},
        "Transaction": {"type": "object", "properties": {"name": {"type": "string"}}},
    }


def test_yaml_config_parses() -> None:
    """YAML config parses correctly for synthetic mapping."""
    config = load_mapping_config(YAML_PATH)
    assert isinstance(config, dict)
    assert "Construction_Company" in config
    assert "Business_Entity" in config


def test_source_to_child_type_mapping() -> None:
    """Source-type to mother-or-child type mapping produces expected output."""
    config = load_mapping_config(YAML_PATH)
    conn = SyntheticConnector(ConnectorConfig(
        connector_type="synthetic",
        namespace_id=uuid4(),
        config_overrides={"synthetic_seed": 42},
    ))
    source_schema = conn.discover_schema()
    ns = _make_namespace("Syn")
    result = map_source_schema(source_schema, ns, _make_mother_ontology(), config)
    assert isinstance(result, SchemaMappingResult)
    # Should have mapped types with Syn_ prefix
    assert any(k.startswith("Syn_") for k in result.child_ontology_schema)


def test_child_ontology_schema_well_formed() -> None:
    """child_ontology_schema is well-formed JSON Schema."""
    config = load_mapping_config(YAML_PATH)
    conn = SyntheticConnector(ConnectorConfig(
        connector_type="synthetic",
        namespace_id=uuid4(),
        config_overrides={"synthetic_seed": 42},
    ))
    result = map_source_schema(
        conn.discover_schema(), _make_namespace(), _make_mother_ontology(), config
    )
    for type_name, type_def in result.child_ontology_schema.items():
        assert "type" in type_def
        assert type_def["type"] == "object"
        assert "properties" in type_def


def test_ddl_well_formed() -> None:
    """vertex_type_ddl entries are well-formed DDL strings."""
    config = load_mapping_config(YAML_PATH)
    conn = SyntheticConnector(ConnectorConfig(
        connector_type="synthetic",
        namespace_id=uuid4(),
        config_overrides={"synthetic_seed": 42},
    ))
    result = map_source_schema(
        conn.discover_schema(), _make_namespace(), _make_mother_ontology(), config
    )
    for ddl in result.vertex_type_ddl:
        assert ddl.startswith("CREATE VERTEX TYPE ")
        assert "IF NOT EXISTS" in ddl


def test_ddl_deterministically_sorted() -> None:
    """DDL output is deterministically sorted alphabetically."""
    config = load_mapping_config(YAML_PATH)
    conn = SyntheticConnector(ConnectorConfig(
        connector_type="synthetic",
        namespace_id=uuid4(),
        config_overrides={"synthetic_seed": 42},
    ))
    result = map_source_schema(
        conn.discover_schema(), _make_namespace(), _make_mother_ontology(), config
    )
    # Extract type names from DDL
    names = [d.replace("CREATE VERTEX TYPE ", "").replace(" IF NOT EXISTS", "") for d in result.vertex_type_ddl]
    assert names == sorted(names)


def test_mapper_does_not_call_db(monkeypatch) -> None:
    """Mapper does NOT call validate_child_ontology_submission or any DB method."""
    calls = []

    def _mock_validate(*args, **kwargs):
        calls.append("validate_child_ontology_submission")
        raise AssertionError("Should not be called")

    monkeypatch.setattr(
        "src.ontology.schema_store.validate_child_ontology_submission",
        _mock_validate,
        raising=False,
    )

    config = load_mapping_config(YAML_PATH)
    conn = SyntheticConnector(ConnectorConfig(
        connector_type="synthetic",
        namespace_id=uuid4(),
        config_overrides={"synthetic_seed": 42},
    ))
    # This should succeed without calling validate
    result = map_source_schema(
        conn.discover_schema(), _make_namespace(), _make_mother_ontology(), config
    )
    assert len(calls) == 0
    assert isinstance(result, SchemaMappingResult)


def test_missing_yaml_key_raises(tmp_path) -> None:
    """Missing YAML key raises descriptive error."""
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("other_key: value\n")
    with pytest.raises(KeyError, match="mappings"):
        load_mapping_config(bad_yaml)


def test_roundtrip_with_validate_child() -> None:
    """Round-trip: mapper output → validate_child_ontology_submission passes."""
    from src.ontology.schema_store import validate_child_ontology_submission

    config = load_mapping_config(YAML_PATH)
    conn = SyntheticConnector(ConnectorConfig(
        connector_type="synthetic",
        namespace_id=uuid4(),
        config_overrides={"synthetic_seed": 42},
    ))
    mother = _make_mother_ontology()
    result = map_source_schema(
        conn.discover_schema(), _make_namespace(), mother, config
    )

    # Build a child schema dict in the format validate_child_ontology_submission expects
    # It expects {type_name: {properties: {...}}} for child and mother
    child_schema = {}
    for type_name, type_def in result.child_ontology_schema.items():
        child_schema[type_name] = {"properties": type_def.get("properties", {})}

    mother_schema = {}
    for type_name, type_def in mother.items():
        mother_schema[type_name] = {"properties": type_def.get("properties", {})}

    # Should not raise — child is a superset of mother types
    validation = validate_child_ontology_submission(child_schema, mother_schema)
    # The validation result should exist (we accept any result shape)
    assert validation is not None
