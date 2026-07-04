"""Tests for KGCL inverter (Chunk 50, D399)."""

from __future__ import annotations

import pytest

from src.ontology.kgcl_inverter import invert


# --- Revertible commands produce correct inverses ---

def test_invert_create_class():
    assert invert("create class 'NewType'") == "obsolete class 'NewType'"


def test_invert_create_relationship():
    assert invert("create relationship 'has_parent'") == "obsolete relationship 'has_parent'"


def test_invert_add_property():
    assert invert("add property 'start_date' to class 'Contract'") == (
        "remove property 'start_date' from class 'Contract'"
    )


# --- Round-trip: each inverse parses via kgcl_parser ---

def test_roundtrip_obsolete_class():
    from src.ontology.kgcl_parser import parse_kgcl
    inv = invert("create class 'Widget'")
    result = parse_kgcl(inv)
    assert result.command_kind.value == "obsolete_class"


def test_roundtrip_obsolete_relationship():
    from src.ontology.kgcl_parser import parse_kgcl
    inv = invert("create relationship 'connects_to'")
    result = parse_kgcl(inv)
    assert result.command_kind.value == "obsolete_relationship"


def test_roundtrip_remove_property():
    from src.ontology.kgcl_parser import parse_kgcl
    inv = invert("add property 'weight' to class 'Edge'")
    result = parse_kgcl(inv)
    assert result.command_kind.value == "remove_property"


# --- add synonym returns None ---

def test_add_synonym_returns_none():
    assert invert("add synonym 'alias' for class 'Foo'") is None


# --- Other non-revertible commands return None ---

@pytest.mark.parametrize("cmd", [
    "obsolete class 'Old'",
    "rename class 'Foo' to 'Bar'",
    "remove property 'x' from class 'Y'",
    "merge types 'A' and 'B'",
    "add annotation 'note' to class 'Z'",
])
def test_non_revertible_returns_none(cmd: str):
    assert invert(cmd) is None
