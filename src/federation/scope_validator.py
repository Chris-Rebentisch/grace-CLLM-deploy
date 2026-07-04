"""Two-tier ontology scope validator (Chunk 51, D405).

Validates that child schemas extend but never modify or remove
mother-defined properties. Pure-function module — no I/O.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TypeValidationResult(BaseModel):
    """Validation result for a single entity type."""

    type_name: str = Field(description="The entity type being validated")
    passed: bool = Field(description="Whether the type passed validation")
    errors: list[str] = Field(
        default_factory=list,
        description="List of validation errors for this type",
    )


class ValidationResult(BaseModel):
    """Aggregate validation result for a child schema."""

    model_config = ConfigDict(populate_by_name=True)

    passed: bool = Field(
        serialization_alias="valid",
        description="Whether all types passed validation",
    )
    type_results: list[TypeValidationResult] = Field(
        default_factory=list,
        serialization_alias="per_type_results",
        description="Per-type validation results",
    )


def validate_child_schema(
    child_schema: dict,
    mother_schema: dict,
) -> ValidationResult:
    """Validate that a child schema extends the mother schema.

    D405 extension-only contract:
    - For each mother-defined type present in the child schema:
      - All mother-defined properties must be present in the child.
      - Child cannot remove mother properties.
      - Child cannot change the type of mother properties.
      - Child may add new properties.
    - Types in the child but not in the mother are allowed (new types).
    - Types in the mother but not in the child are allowed (the child
      simply doesn't extend those types).

    Args:
        child_schema: The child ontology schema as a dict.
        mother_schema: The mother ontology schema as a dict.

    Returns:
        ValidationResult with per-type pass/fail.
    """
    mother_types = _extract_entity_types(mother_schema)
    child_types = _extract_entity_types(child_schema)

    type_results: list[TypeValidationResult] = []
    all_passed = True

    for type_name, mother_props in mother_types.items():
        if type_name not in child_types:
            # Child doesn't include this mother type — that's fine.
            continue

        child_props = child_types[type_name]
        errors: list[str] = []

        for prop_name, mother_prop_def in mother_props.items():
            if prop_name not in child_props:
                errors.append(
                    f"Missing mother-defined property '{prop_name}'"
                )
                continue

            child_prop_def = child_props[prop_name]
            mother_type = _extract_type(mother_prop_def)
            child_type = _extract_type(child_prop_def)

            if mother_type and child_type and mother_type != child_type:
                errors.append(
                    f"Type change for property '{prop_name}': "
                    f"mother={mother_type}, child={child_type}"
                )

        passed = len(errors) == 0
        if not passed:
            all_passed = False

        type_results.append(
            TypeValidationResult(
                type_name=type_name,
                passed=passed,
                errors=errors,
            )
        )

    return ValidationResult(passed=all_passed, type_results=type_results)


def _extract_entity_types(schema: dict) -> dict[str, dict]:
    """Extract entity types and their properties from a schema dict.

    Handles both flat GrACE format (``entity_types``) and Pydantic
    ``$defs`` format.
    """
    types: dict[str, dict] = {}

    if "entity_types" in schema:
        for type_name, type_def in schema["entity_types"].items():
            if isinstance(type_def, dict):
                types[type_name] = type_def.get("properties", {})
    elif "$defs" in schema:
        for type_name, type_def in schema["$defs"].items():
            if isinstance(type_def, dict):
                types[type_name] = type_def.get("properties", {})

    return types


def _extract_type(prop_def: dict) -> str | None:
    """Extract the type string from a property definition."""
    if isinstance(prop_def, dict):
        return prop_def.get("type")
    return None
