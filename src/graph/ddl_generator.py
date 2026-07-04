"""DDL statement generation for ArcadeDB schema from ontology JSON.

Pure functions — no side effects. Returns lists of SQL strings.
The executor (schema_sync.py) runs them against ArcadeDB.
"""

from src.graph.migration_types import META_EDGE_TYPES, META_ENTITY_TYPES
from src.graph.system_properties import EDGE_SYSTEM_PROPERTIES, VERTEX_SYSTEM_PROPERTIES
from src.graph.type_mapping import map_data_type


def _normalize_properties(properties) -> dict:
    """Coerce properties into the expected dict shape.

    Discovery's ratified schema ships ``properties`` as a LIST
    ``[{"name": ..., "data_type": ...}, ...]`` while the Pydantic
    ``$defs`` shape ships it as a DICT keyed by name. Normalize here
    so the DDL generator never crashes with `'list' object has no
    attribute 'items'` (Phase-4 finding — mirror of the same fix
    in src/extraction/extraction_prompts.py).
    """
    if isinstance(properties, dict):
        return properties
    if isinstance(properties, list):
        return {p["name"]: p for p in properties if isinstance(p, dict) and "name" in p}
    return {}


def generate_vertex_type_ddl(type_name: str, properties) -> list[str]:
    """Generate CREATE VERTEX TYPE + CREATE PROPERTY statements.

    Args:
        type_name: e.g. "Legal_Entity"
        properties: dict (canonical) or list (Discovery output) of property defs.

    Returns:
        List of SQL DDL strings with IF NOT EXISTS.
    """
    statements: list[str] = [f"CREATE VERTEX TYPE {type_name} IF NOT EXISTS"]
    for prop_name, prop_def in _normalize_properties(properties).items():
        arcade_type = map_data_type(prop_def.get("data_type", "string"))
        statements.append(
            f"CREATE PROPERTY {type_name}.{prop_name} IF NOT EXISTS {arcade_type}"
        )
    return statements


def generate_edge_type_ddl(
    rel_name: str,
    rel_def: dict,
    with_constraints: bool = False,
) -> list[str]:
    """Generate CREATE EDGE TYPE + CREATE PROPERTY statements.

    Args:
        rel_name: e.g. "owns"
        rel_def: e.g. {"source_type": "Legal_Entity", "target_type": ..., "properties": {...}}
        with_constraints: If True, generate @in/@out constraints. Defaults to False.

    Returns:
        List of SQL DDL strings with IF NOT EXISTS.
    """
    statements: list[str] = [f"CREATE EDGE TYPE {rel_name} IF NOT EXISTS"]
    if with_constraints:
        source = rel_def.get("source_type", "V")
        target = rel_def.get("target_type", "V")
        statements.append(
            f"CREATE PROPERTY {rel_name}.@out IF NOT EXISTS LINK {source}"
        )
        statements.append(
            f"CREATE PROPERTY {rel_name}.@in IF NOT EXISTS LINK {target}"
        )
    # Ratified relationships carry their domain attributes under
    # "edge_properties" (see _relationship_to_schema in
    # src/ontology/review_ops.py); fall back to "properties" for
    # back-compat with the canonical Pydantic $defs shape.
    edge_props = _normalize_properties(
        rel_def.get("edge_properties") or rel_def.get("properties", {})
    )
    for prop_name, prop_def in edge_props.items():
        arcade_type = map_data_type(prop_def.get("data_type", "string"))
        statements.append(
            f"CREATE PROPERTY {rel_name}.{prop_name} IF NOT EXISTS {arcade_type}"
        )
    return statements


def generate_system_properties_ddl(type_name: str, is_edge: bool = False) -> list[str]:
    """Generate system property DDL for a vertex or edge type.

    Adds temporal, provenance, and governance properties.
    """
    props = EDGE_SYSTEM_PROPERTIES if is_edge else VERTEX_SYSTEM_PROPERTIES
    return [
        f"CREATE PROPERTY {type_name}.{p['name']} IF NOT EXISTS {p['type']}"
        for p in props
    ]


def generate_embedding_property_ddl(type_name: str) -> str:
    """Generate CREATE PROPERTY DDL for the _embedding vector property.

    Returns a single DDL statement that adds a LIST property for storing
    768-dim float embeddings on a domain entity type.

    # D445.2 / D356 — embedding property on domain types only; LIST is
    # the accepted ArcadeDB 26.5.1 property type for vector storage
    # (EMBEDDEDLIST DOUBLE and VECTOR(N) rejected by 26.5.1 parser).
    # Authorization: D445.2.
    """
    return f"CREATE PROPERTY {type_name}._embedding IF NOT EXISTS LIST"


def generate_full_schema_ddl(
    schema_json: dict,
    with_constraints: bool = False,
) -> list[str]:
    """Generate complete DDL for an entire ontology schema.

    Reads schema_json with "entity_types" and "relationships" keys.
    Vertex types are created before edge types (edges may reference vertices).

    Returns:
        Ordered list of all DDL statements.
    """
    statements: list[str] = []
    entity_types = schema_json.get("entity_types", {})
    relationships = schema_json.get("relationships", {})

    # Vertex types first
    for type_name, type_def in entity_types.items():
        properties = type_def.get("properties", {})
        statements.extend(generate_vertex_type_ddl(type_name, properties))
        statements.extend(generate_system_properties_ddl(type_name, is_edge=False))
        # D445.2 / D356 — embedding property on domain entity types only;
        # meta-types (META_ENTITY_TYPES, META_EDGE_TYPES) are handled by
        # generate_meta_entity_ddl() and do NOT get _embedding.
        if type_name not in META_ENTITY_TYPES and type_name not in META_EDGE_TYPES:
            statements.append(generate_embedding_property_ddl(type_name))

    # Edge types second
    for rel_name, rel_def in relationships.items():
        statements.extend(generate_edge_type_ddl(rel_name, rel_def, with_constraints=with_constraints))
        statements.extend(generate_system_properties_ddl(rel_name, is_edge=True))

    # Meta-entity types (provenance layer)
    statements.extend(generate_meta_entity_ddl())

    return statements


def generate_meta_entity_ddl() -> list[str]:
    """Generate DDL for meta-entity vertex types and meta-edge types.

    Vertex types: Migration_Event, Correction_Event, Extraction_Event.
    Edge types: produced_by.
    These are provenance-layer types created alongside the domain ontology.
    """
    statements: list[str] = []
    # Meta vertex types
    for type_name, properties in META_ENTITY_TYPES.items():
        statements.append(f"CREATE VERTEX TYPE {type_name} IF NOT EXISTS")
        for prop in properties:
            statements.append(
                f"CREATE PROPERTY {type_name}.{prop['name']} IF NOT EXISTS {prop['type']}"
            )
    # Meta edge types (D105)
    for edge_name, properties in META_EDGE_TYPES.items():
        statements.append(f"CREATE EDGE TYPE {edge_name} IF NOT EXISTS")
        for prop in properties:
            statements.append(
                f"CREATE PROPERTY {edge_name}.{prop['name']} IF NOT EXISTS {prop['type']}"
            )
    return statements
