"""Pure-function schema mapper with YAML-config-driven type mapping.

Maps source-system schemas to child-ontology schemas with DDL output.
Does NOT call ``validate_child_ontology_submission()`` or any DB method —
DDL execution and ratification calls live in the sync pipeline (CP6).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from src.graph.management_models import GraphNamespace


class SchemaMappingResult(BaseModel):
    """Result of mapping a source schema to a child ontology schema."""

    child_ontology_schema: dict = Field(
        description="JSON-Schema-like dict for the child ontology"
    )
    vertex_type_ddl: list[str] = Field(
        description="DDL statements for creating vertex types in ArcadeDB"
    )


def load_mapping_config(config_path: str | Path) -> dict:
    """Load a YAML mapping config file.

    Raises:
        FileNotFoundError: if the config file does not exist.
        KeyError: if required 'mappings' key is missing.
    """
    path = Path(config_path)
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "mappings" not in data:
        raise KeyError(
            f"Mapping config at {path} must contain a top-level 'mappings' key"
        )
    return data["mappings"]


def map_source_schema(
    source_schema: dict[str, Any],
    namespace: GraphNamespace,
    mother_ontology: dict[str, Any],
    mapping_config: dict[str, Any],
) -> SchemaMappingResult:
    """Map a source system schema to a child ontology schema.

    Pure function — no ArcadeDB, no Postgres I/O.

    Args:
        source_schema: JSON-Schema-like dict from ``discover_schema()``.
        namespace: Target federation namespace (provides ``label_prefix``).
        mother_ontology: Mother ontology schema for reference.
        mapping_config: Loaded mapping config (the ``mappings`` dict).

    Returns:
        SchemaMappingResult with child_ontology_schema and vertex_type_ddl.
    """
    prefix = namespace.label_prefix or ""
    child_types: dict[str, dict] = {}
    ddl_set: set[str] = set()

    for source_type, source_def in source_schema.items():
        mapping = mapping_config.get(source_type)
        if not mapping:
            continue

        target_type = mapping["target_type"]
        # Build prefixed child type name
        child_type_name = f"{prefix}_{target_type}" if prefix else target_type

        # Build child type schema from source + mapping
        child_properties: dict[str, dict] = {}
        prop_mapping = mapping.get("properties", {})
        source_props = source_def.get("properties", {})

        for source_prop, target_prop in prop_mapping.items():
            if source_prop in source_props:
                child_properties[target_prop] = source_props[source_prop]
            else:
                child_properties[target_prop] = {"type": "string"}

        # Include mother ontology properties if the target type exists
        mother_type_def = mother_ontology.get(target_type, {})
        mother_props = mother_type_def.get("properties", {})
        for mprop, mdef in mother_props.items():
            if mprop not in child_properties:
                child_properties[mprop] = mdef

        child_types[child_type_name] = {
            "type": "object",
            "properties": child_properties,
            "source_type": source_type,
            "target_type": target_type,
        }

        ddl_set.add(
            f"CREATE VERTEX TYPE {child_type_name} IF NOT EXISTS"
        )

    # DDL output is deterministically sorted alphabetically by type name
    vertex_type_ddl = sorted(ddl_set)

    return SchemaMappingResult(
        child_ontology_schema=child_types,
        vertex_type_ddl=vertex_type_ddl,
    )
