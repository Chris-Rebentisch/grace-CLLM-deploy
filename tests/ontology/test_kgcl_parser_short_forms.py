"""D461: Short-form KGCL parser tests for rename property and add synonym.

Tests the ``schema_lookup_fn`` parameter on ``parse_kgcl()`` that enables
operator-natural short forms without the ``on class`` / ``class`` qualifier.
"""

from __future__ import annotations

import pytest

from src.ontology.kgcl_models import KGCLCommandKind, KGCLParseError, ProposedSchemaChange
from src.ontology.kgcl_parser import parse_kgcl


# ---------------------------------------------------------------------------
# Mock schema lookup helpers
# ---------------------------------------------------------------------------


def _lookup_single(name: str) -> list[str]:
    """Return a single match for any lookup — unambiguous."""
    return ["Legal_Entity"]


def _lookup_multiple(name: str) -> list[str]:
    """Return multiple matches — ambiguous."""
    return ["Legal_Entity", "Person"]


def _lookup_empty(name: str) -> list[str]:
    """Return no matches — entity not found."""
    return []


# ---------------------------------------------------------------------------
# rename property short form
# ---------------------------------------------------------------------------


def test_rename_property_short_form_unambiguous() -> None:
    """Single match returns ProposedSchemaChange with correct entity_name."""
    result = parse_kgcl(
        "rename property 'full_name' to 'legal_name'",
        schema_lookup_fn=_lookup_single,
    )
    assert isinstance(result, ProposedSchemaChange)
    assert result.command_kind == KGCLCommandKind.RENAME_PROPERTY
    assert result.property_name == "full_name"
    assert result.new_name == "legal_name"
    assert result.entity_name == "Legal_Entity"


def test_rename_property_short_form_ambiguous_raises() -> None:
    """Multiple matches raise KGCLParseError with AMBIGUOUS and candidates."""
    with pytest.raises(KGCLParseError) as exc_info:
        parse_kgcl(
            "rename property 'full_name' to 'legal_name'",
            schema_lookup_fn=_lookup_multiple,
        )
    err = exc_info.value
    assert err.error_kind == "AMBIGUOUS"
    assert err.candidates == ["Legal_Entity", "Person"]


def test_rename_property_short_form_not_found_raises() -> None:
    """Zero matches raise KGCLParseError with ENTITY_NOT_FOUND."""
    with pytest.raises(KGCLParseError) as exc_info:
        parse_kgcl(
            "rename property 'nonexistent' to 'new_name'",
            schema_lookup_fn=_lookup_empty,
        )
    err = exc_info.value
    assert err.error_kind == "ENTITY_NOT_FOUND"
    assert err.candidates is None


# ---------------------------------------------------------------------------
# add synonym short form
# ---------------------------------------------------------------------------


def test_add_synonym_short_form_unambiguous() -> None:
    """Single match returns ProposedSchemaChange with correct entity target."""
    result = parse_kgcl(
        "add synonym 'Corp' for 'Legal_Entity'",
        schema_lookup_fn=_lookup_single,
    )
    assert isinstance(result, ProposedSchemaChange)
    assert result.command_kind == KGCLCommandKind.ADD_SYNONYM
    assert result.synonym == "Corp"
    assert result.target_name == "Legal_Entity"


def test_add_synonym_short_form_ambiguous_raises() -> None:
    """Multiple matches raise KGCLParseError with AMBIGUOUS and candidates."""
    with pytest.raises(KGCLParseError) as exc_info:
        parse_kgcl(
            "add synonym 'Corp' for 'Legal_Entity'",
            schema_lookup_fn=_lookup_multiple,
        )
    err = exc_info.value
    assert err.error_kind == "AMBIGUOUS"
    assert err.candidates == ["Legal_Entity", "Person"]
