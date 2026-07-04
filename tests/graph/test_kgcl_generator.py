"""Tests for KGCL command generation from OM4OV diffs."""

from src.graph.kgcl_generator import generate_kgcl_commands


def test_kgcl_create_class():
    """Entity type added produces 'create class' command."""
    diff = {
        "entity_types": {"added": ["Insurance_Policy"], "removed": [], "modified": [], "unchanged": []},
        "relationships": {"added": [], "removed": [], "modified": [], "unchanged": []},
        "properties": {"added": [], "removed": [], "modified": []},
    }
    commands = generate_kgcl_commands(diff)
    assert "create class 'Insurance_Policy'" in commands


def test_kgcl_obsolete_class():
    """Entity type removed produces 'obsolete class' command."""
    diff = {
        "entity_types": {"added": [], "removed": ["Old_Entity"], "modified": [], "unchanged": []},
        "relationships": {"added": [], "removed": [], "modified": [], "unchanged": []},
        "properties": {"added": [], "removed": [], "modified": []},
    }
    commands = generate_kgcl_commands(diff)
    assert "obsolete class 'Old_Entity'" in commands


def test_kgcl_add_property():
    """Property added to type produces 'add property' command."""
    diff = {
        "entity_types": {"added": [], "removed": [], "modified": [], "unchanged": []},
        "relationships": {"added": [], "removed": [], "modified": [], "unchanged": []},
        "properties": {
            "added": [{"entity": "Insurance_Policy", "property": "expiry_date"}],
            "removed": [],
            "modified": [],
        },
    }
    commands = generate_kgcl_commands(diff)
    assert "add property 'expiry_date' to class 'Insurance_Policy'" in commands


def test_kgcl_create_relationship():
    """Relationship added produces 'create relationship' command."""
    diff = {
        "entity_types": {"added": [], "removed": [], "modified": [], "unchanged": []},
        "relationships": {"added": ["covers"], "removed": [], "modified": [], "unchanged": []},
        "properties": {"added": [], "removed": [], "modified": []},
    }
    commands = generate_kgcl_commands(diff)
    assert "create relationship 'covers'" in commands


def test_kgcl_obsolete_relationship():
    """Relationship removed produces 'obsolete relationship' command."""
    diff = {
        "entity_types": {"added": [], "removed": [], "modified": [], "unchanged": []},
        "relationships": {"added": [], "removed": ["old_rel"], "modified": [], "unchanged": []},
        "properties": {"added": [], "removed": [], "modified": []},
    }
    commands = generate_kgcl_commands(diff)
    assert "obsolete relationship 'old_rel'" in commands


def test_kgcl_empty_diff():
    """No changes produces empty command list."""
    diff = {
        "entity_types": {"added": [], "removed": [], "modified": [], "unchanged": ["Person"]},
        "relationships": {"added": [], "removed": [], "modified": [], "unchanged": []},
        "properties": {"added": [], "removed": [], "modified": []},
    }
    commands = generate_kgcl_commands(diff)
    assert commands == []
