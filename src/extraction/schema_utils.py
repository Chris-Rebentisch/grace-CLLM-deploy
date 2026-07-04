"""Schema type extraction utilities for Extraction module.

Extracts allowed entity type names and relationship predicate names from
ontology JSON Schema. Supports both flat GrACE format and Pydantic $defs
format. Designed for reuse by eval_checkpoint.py (Chunk 18) and
constraint_validator.py (Chunk 21).
"""

import structlog

log = structlog.get_logger()


def extract_allowed_types(schema: dict) -> tuple[set[str], set[str]]:
    """Extract allowed entity type names and relationship predicate names.

    Args:
        schema: Ontology JSON Schema dict (from router or --schema file).

    Returns:
        (entity_type_names, relationship_predicate_names)
        Empty set means "unknown" — not "none allowed".

    Schema shapes supported:
    - Flat GrACE format: {"entity_types": {...}, "relationships": {...}}
      -> entity_type_names = keys of entity_types
      -> relationship_predicate_names = keys of relationships
    - Pydantic $defs format: {"$defs": {...}, ...}
      -> entity_type_names = keys of $defs
      -> relationship_predicate_names = empty set (log warning)
    """
    entity_types: set[str] = set()
    predicates: set[str] = set()

    # Flat GrACE format
    if "entity_types" in schema:
        entity_types = set(schema["entity_types"].keys())
    elif "$defs" in schema:
        entity_types = set(schema["$defs"].keys())
        log.warning(
            "schema_utils_defs_format",
            msg="$defs schema: treating all definitions as entity types. "
                "Relationship predicate enumeration unavailable.",
        )
    else:
        log.warning("schema_utils_unknown_format", keys=list(schema.keys())[:5])

    if "relationships" in schema:
        predicates = set(schema["relationships"].keys())

    return entity_types, predicates


def normalize_property_shape(schema: dict | None) -> dict | None:
    """Normalize per-type ``properties`` to the canonical dict shape.

    D546 — capture-the-why: GrACE stores a type's ``properties`` two different ways.
    The full active ``schema_json`` (what the extraction pipeline was built and tested
    against) keys ``properties`` as a **dict** ``{prop_name: {name, required, data_type,
    ...}}``. The per-module schema in ``schema_modules[<module>]`` (returned by
    ``OntologyRouter.resolve_schema(module_name)`` and ``GET /api/ontology/modules/{m}``)
    stores ``properties`` as a **list** of those same objects. Downstream consumers call
    ``.keys()`` on it (``constraint_validator`` claim validation, schema→model/prompt
    construction), so a module-scoped extraction raised ``'list' object has no attribute
    'keys'`` and the per-module extraction path never worked end-to-end (C1 finding #13,
    surfaced live by the bounded-heat apply-gate). This normalizer is tolerant of both
    shapes: list ``properties`` are rekeyed by each element's ``name`` (falling back to a
    positional key only if ``name`` is absent); already-dict ``properties`` pass through
    unchanged. Applied once at the resolution boundary so every consumer sees the
    canonical dict shape. No schema-contract / data migration required.
    """
    if not isinstance(schema, dict):
        return schema

    def _fix_type_map(type_map: object) -> None:
        if not isinstance(type_map, dict):
            return
        for type_def in type_map.values():
            if not isinstance(type_def, dict):
                continue
            props = type_def.get("properties")
            if isinstance(props, list):
                type_def["properties"] = {
                    (p.get("name") if isinstance(p, dict) and p.get("name") else str(i)): p
                    for i, p in enumerate(props)
                }

    _fix_type_map(schema.get("entity_types"))
    _fix_type_map(schema.get("relationships"))
    _fix_type_map(schema.get("relationship_types"))
    return schema
