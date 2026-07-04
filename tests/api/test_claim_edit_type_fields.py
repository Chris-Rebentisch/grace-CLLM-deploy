"""F-16 regression: Edit-and-Accept must allow correcting entity/subject/object
TYPE fields (so type-error quarantines have a supersession cure) and re-validate
the corrected claim against the active ontology."""

from __future__ import annotations

from types import SimpleNamespace

from src.api.claim_routes import ModifiedClaimPayload, _revalidate_against_active


def test_payload_accepts_type_fields():
    p = ModifiedClaimPayload(
        subject_name="Acme",
        entity_type="Legal_Entity",
        subject_type="Legal_Entity",
        object_type="Person",
    )
    assert p.entity_type == "Legal_Entity"
    assert p.subject_type == "Legal_Entity"
    assert p.object_type == "Person"


def _active(monkeypatch, schema):
    monkeypatch.setattr(
        "src.ontology.database.get_active_version",
        lambda _db, *a, **k: SimpleNamespace(schema_json=schema),
    )


def test_revalidate_flags_invalid_corrected_type(monkeypatch):
    schema = {"entity_types": {"Legal_Entity": {"properties": {"name": {}}}}}
    _active(monkeypatch, schema)
    violations = _revalidate_against_active(
        None,
        {
            "entity_type": "Still_Not_A_Type",
            "subject_name": "X",
            "subject_type": "Still_Not_A_Type",
            "properties_json": {"name": "X"},
            "confidence": 0.9,
            "schema_version": 1,
        },
    )
    rules = {getattr(v, "rule", None) for v in violations}
    assert "invalid_entity_type" in rules


def test_revalidate_valid_corrected_type_no_type_error(monkeypatch):
    schema = {"entity_types": {"Legal_Entity": {"properties": {"name": {}}}}}
    _active(monkeypatch, schema)
    violations = _revalidate_against_active(
        None,
        {
            "entity_type": "Legal_Entity",
            "subject_name": "X",
            "subject_type": "Legal_Entity",
            "properties_json": {"name": "X"},
            "confidence": 0.9,
            "schema_version": 1,
        },
    )
    rules = {getattr(v, "rule", None) for v in violations}
    assert "invalid_entity_type" not in rules


def test_revalidate_no_active_schema_returns_empty(monkeypatch):
    monkeypatch.setattr(
        "src.ontology.database.get_active_version", lambda _db, *a, **k: None
    )
    assert _revalidate_against_active(None, {"entity_type": "X", "subject_name": "Y"}) == []
