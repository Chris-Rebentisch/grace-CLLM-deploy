"""D546 guard — module-scoped schemas with list-shaped `properties` must be
normalized to the canonical dict shape the extraction pipeline expects.

Regression for C1 finding #13 (surfaced live by the bounded-heat apply-gate): the
per-module schema (`schema_modules[<module>]`, returned by
`OntologyRouter.resolve_schema(module_name)`) stores each type's `properties` as a
LIST, while the full `schema_json` stores it as a DICT. Downstream consumers
(`constraint_validator`, schema→model/prompt construction) call `.keys()` on it, so
module-scoped email extraction raised `'list' object has no attribute 'keys'` and the
per-module extraction path never worked end-to-end.

Heat-free: pure dict transformation, no LLM call.
"""

from src.extraction.schema_utils import extract_allowed_types, normalize_property_shape


# A faithful miniature of the wire shape returned for schema_modules['legal']:
# entity_types/relationships are dicts keyed by type name; each type's `properties`
# is a LIST of {name, required, data_type, ...} objects.
MODULE_SCHEMA_LIST_PROPS = {
    "entity_types": {
        "Legal_Entity": {
            "properties": [
                {"name": "legal_name", "required": True, "data_type": "string"},
                {"name": "entity_form", "required": False, "data_type": "string"},
            ]
        },
        "Payment_Term": {
            "properties": [
                {"name": "amount_or_rate", "required": False, "data_type": "string"},
            ]
        },
    },
    "relationships": {
        "party_to": {"properties": [{"name": "role", "required": False}]},
    },
}


def test_list_properties_normalized_to_dict():
    out = normalize_property_shape(
        {k: {tk: dict(tv) for tk, tv in v.items()} for k, v in MODULE_SCHEMA_LIST_PROPS.items()}
    )
    le_props = out["entity_types"]["Legal_Entity"]["properties"]
    assert isinstance(le_props, dict)
    assert set(le_props.keys()) == {"legal_name", "entity_form"}
    # values preserved intact
    assert le_props["legal_name"]["required"] is True
    # relationships normalized too
    assert isinstance(out["relationships"]["party_to"]["properties"], dict)
    assert "role" in out["relationships"]["party_to"]["properties"]


def test_dict_properties_pass_through_unchanged():
    """Already-canonical (full schema_json) dict properties must be left alone."""
    canonical = {
        "entity_types": {
            "Legal_Entity": {
                "properties": {
                    "legal_name": {"name": "legal_name", "required": True},
                }
            }
        }
    }
    out = normalize_property_shape(canonical)
    assert out["entity_types"]["Legal_Entity"]["properties"] == {
        "legal_name": {"name": "legal_name", "required": True}
    }


def test_normalized_schema_survives_keys_consumption():
    """The crash site pattern — type_def['properties'].keys() — must not raise."""
    out = normalize_property_shape(
        {k: {tk: dict(tv) for tk, tv in v.items()} for k, v in MODULE_SCHEMA_LIST_PROPS.items()}
    )
    for type_def in out["entity_types"].values():
        # mirrors constraint_validator.py:149
        assert set(type_def.get("properties", {}).keys()) is not None
    # extract_allowed_types still enumerates the module's types
    ents, preds = extract_allowed_types(out)
    assert ents == {"Legal_Entity", "Payment_Term"}
    assert preds == {"party_to"}


def test_none_and_non_dict_safe():
    assert normalize_property_shape(None) is None
    assert normalize_property_shape([]) == []
