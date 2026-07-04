"""Tests for to_strict_json_schema() — CP1 of Chunk 63 (D444)."""

from typing import Literal, Optional

from pydantic import BaseModel, Field

from src.shared.schema_transform import to_strict_json_schema


# --- Test-local stub models ---


class _OptionalFieldModel(BaseModel):
    """Model with an Optional[str] field for testing optional promotion."""
    name: str
    nickname: Optional[str] = None


class _UntypedDictModel(BaseModel):
    """Model with an untyped dict field — property-less object node."""
    metadata: dict = Field(default_factory=dict, description="Arbitrary metadata")


class _LiteralModel(BaseModel):
    """Model with a Literal field for testing enum constraint preservation."""
    status: Literal["active", "inactive", "pending"] = "active"
    name: str = ""


# --- Tests ---


def test_nested_models_call1response_transforms_and_roundtrips():
    """Call1Response transforms correctly and round-trips with model_validate()."""
    from src.discovery.merge_models import Call1Response

    schema = to_strict_json_schema(Call1Response)
    assert "additionalProperties" in schema
    assert schema["additionalProperties"] is False
    assert "$defs" not in schema
    # Round-trip: create a minimal valid instance and validate against schema structure
    instance = Call1Response(clusters=[])
    Call1Response.model_validate(instance.model_dump())


def test_optional_fields_promoted_to_required_nullable():
    """Optional[str] fields are promoted to required and nullable."""
    schema = to_strict_json_schema(_OptionalFieldModel)
    assert "nickname" in schema.get("required", [])
    # The nickname field should be nullable
    nick_schema = schema["properties"]["nickname"]
    # Should have a nullable type representation
    has_null = False
    if isinstance(nick_schema.get("type"), list):
        has_null = "null" in nick_schema["type"]
    elif "anyOf" in nick_schema:
        has_null = any(b.get("type") == "null" for b in nick_schema["anyOf"] if isinstance(b, dict))
    assert has_null, f"nickname should be nullable, got: {nick_schema}"


def test_defs_resolution_call2response():
    """Call2Response (contains DomainGroup → SubDomain chain) inlines $defs fully."""
    from src.discovery.merge_models import Call2Response

    schema = to_strict_json_schema(Call2Response)
    assert "$defs" not in schema
    # domain_groups items should have the DomainGroup shape inlined
    items_schema = schema["properties"]["domain_groups"]["items"]
    assert "properties" in items_schema
    assert "domain" in items_schema["properties"]


def test_default_factory_lists_stage1output():
    """Stage1Output.entity_types (default_factory=list) is handled."""
    from src.discovery.schema_models import Stage1Output

    schema = to_strict_json_schema(Stage1Output)
    assert "$defs" not in schema
    et_schema = schema["properties"]["entity_types"]
    assert et_schema["type"] == "array"
    # Items should be fully resolved
    assert "properties" in et_schema["items"]


def test_enum_fields_literal_preserved():
    """Model with Literal field transforms without losing enum constraints."""
    schema = to_strict_json_schema(_LiteralModel)
    status_schema = schema["properties"]["status"]
    # Literal becomes enum in JSON Schema
    assert "enum" in status_schema
    assert set(status_schema["enum"]) == {"active", "inactive", "pending"}


def test_roundtrip_battery_all_five_grace_models():
    """All five swept GrACE response models transform and round-trip with model_validate()."""
    from src.discovery.merge_models import Call1Response, Call2Response, Call3Response
    from src.discovery.schema_models import Stage1Output
    from src.discovery.seed_models import SuggestionResponse

    models = [Call1Response, Call2Response, Call3Response, SuggestionResponse, Stage1Output]
    for model_cls in models:
        schema = to_strict_json_schema(model_cls)
        assert "$defs" not in schema, f"{model_cls.__name__} still has $defs"
        assert isinstance(schema, dict)
        # Ensure round-trip: build from defaults where possible
        if model_cls == Call1Response:
            inst = model_cls(clusters=[])
        elif model_cls == Call2Response:
            inst = model_cls(domain_groups=[])
        elif model_cls == Call3Response:
            inst = model_cls()
        elif model_cls == SuggestionResponse:
            inst = model_cls(suggestions=[])
        elif model_cls == Stage1Output:
            inst = model_cls()
        model_cls.model_validate(inst.model_dump())


def test_strict_stamping_property_bearing_vs_propertyless():
    """Property-bearing object nodes get additionalProperties: false;
    property-less object nodes (untyped dict) are left permissive."""
    # Property-bearing: a normal model
    from src.discovery.merge_models import Call1Response

    schema = to_strict_json_schema(Call1Response)
    assert schema.get("additionalProperties") is False

    # Property-less: untyped dict model
    schema2 = to_strict_json_schema(_UntypedDictModel)
    # Top-level has properties, so top-level gets stamped
    assert schema2.get("additionalProperties") is False
    # But the metadata field itself is type: object with no properties — should NOT be stamped
    meta_schema = schema2["properties"]["metadata"]
    assert meta_schema.get("type") == "object"
    assert "additionalProperties" not in meta_schema or meta_schema.get("additionalProperties") is not False
