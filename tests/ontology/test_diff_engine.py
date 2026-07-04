"""Tests for the ontology diff engine."""

import pytest

from src.ontology.diff_engine import (
    apply_rfc6902_patch,
    compute_entity_level_diff,
    compute_om4ov_diff,
    compute_rfc6902_patch,
    compute_schema_diff,
)


# --- RFC 6902 Patch Tests ---


def test_rfc6902_identical_schemas_empty_patch():
    """Identical schemas return empty patch."""
    schema = {"entity_types": {"Company": {"properties": {"name": "string"}}}}
    patch = compute_rfc6902_patch(schema, schema)
    assert patch == []


def test_rfc6902_added_key():
    """Added key produces 'add' operation."""
    old = {"a": 1}
    new = {"a": 1, "b": 2}
    patch = compute_rfc6902_patch(old, new)
    ops = [p["op"] for p in patch]
    assert "add" in ops


def test_rfc6902_removed_key():
    """Removed key produces 'remove' operation."""
    old = {"a": 1, "b": 2}
    new = {"a": 1}
    patch = compute_rfc6902_patch(old, new)
    ops = [p["op"] for p in patch]
    assert "remove" in ops


def test_rfc6902_changed_value():
    """Changed value produces 'replace' operation."""
    old = {"a": 1}
    new = {"a": 2}
    patch = compute_rfc6902_patch(old, new)
    ops = [p["op"] for p in patch]
    assert "replace" in ops


def test_rfc6902_nested_changes():
    """Nested changes are tracked."""
    old = {"entity_types": {"Company": {"name": "string"}}}
    new = {"entity_types": {"Company": {"name": "string", "tax_id": "string"}}}
    patch = compute_rfc6902_patch(old, new)
    assert len(patch) > 0
    paths = [p["path"] for p in patch]
    assert any("tax_id" in p for p in paths)


def test_apply_rfc6902_roundtrip():
    """Compute patch then apply produces new_schema."""
    old = {"a": 1, "b": {"c": 3}}
    new = {"a": 2, "b": {"c": 3, "d": 4}, "e": 5}
    patch = compute_rfc6902_patch(old, new)
    result = apply_rfc6902_patch(old, patch)
    assert result == new


def test_apply_rfc6902_invalid_patch():
    """Invalid patch raises ValueError."""
    schema = {"a": 1}
    bad_patch = [{"op": "remove", "path": "/nonexistent"}]
    with pytest.raises(ValueError, match="Failed to apply patch"):
        apply_rfc6902_patch(schema, bad_patch)


# --- OM4OV Diff Tests ---


def test_om4ov_identical_schemas():
    """Identical schemas return all remain, zero add/update/delete."""
    schema = {"a": 1, "b": 2}
    diff = compute_om4ov_diff(schema, schema)
    assert diff["summary"]["add_count"] == 0
    assert diff["summary"]["update_count"] == 0
    assert diff["summary"]["delete_count"] == 0
    assert diff["summary"]["remain_count"] == 2


def test_om4ov_added_key():
    """Added key appears in 'add' category."""
    old = {"a": 1}
    new = {"a": 1, "b": 2}
    diff = compute_om4ov_diff(old, new)
    assert diff["summary"]["add_count"] == 1
    assert any("b" in item for item in diff["add"])


def test_om4ov_removed_key():
    """Removed key appears in 'delete' category."""
    old = {"a": 1, "b": 2}
    new = {"a": 1}
    diff = compute_om4ov_diff(old, new)
    assert diff["summary"]["delete_count"] == 1
    assert any("b" in item for item in diff["delete"])


def test_om4ov_changed_value():
    """Changed value appears in 'update' category."""
    old = {"a": 1}
    new = {"a": 2}
    diff = compute_om4ov_diff(old, new)
    assert diff["summary"]["update_count"] == 1


def test_om4ov_summary_counts_correct():
    """Summary counts match category lengths."""
    old = {"a": 1, "b": 2, "c": 3}
    new = {"a": 1, "b": 99, "d": 4}
    diff = compute_om4ov_diff(old, new)
    assert diff["summary"]["add_count"] == len(diff["add"])
    assert diff["summary"]["update_count"] == len(diff["update"])
    assert diff["summary"]["delete_count"] == len(diff["delete"])
    assert diff["summary"]["remain_count"] == len(diff["remain"])


# --- compute_schema_diff ---


def test_compute_schema_diff_returns_both():
    """compute_schema_diff returns both RFC 6902 and OM4OV diff."""
    old = {"a": 1}
    new = {"a": 2, "b": 3}
    rfc6902, om4ov = compute_schema_diff(old, new)
    assert isinstance(rfc6902, list)
    assert isinstance(om4ov, dict)
    assert "summary" in om4ov


# --- Entity-Level Diff Tests ---


def test_entity_level_diff_added_types():
    """Identifies added entity types in flat structure."""
    old = {"entity_types": {"Company": {"properties": {}}}}
    new = {"entity_types": {"Company": {"properties": {}}, "Trust": {"properties": {}}}}
    diff = compute_entity_level_diff(old, new)
    assert "Trust" in diff["entity_types"]["added"]
    assert "Company" in diff["entity_types"]["unchanged"]


def test_entity_level_diff_removed_relationships():
    """Identifies removed relationships."""
    old = {"entity_types": {}, "relationships": {"owns": {}, "manages": {}}}
    new = {"entity_types": {}, "relationships": {"owns": {}}}
    diff = compute_entity_level_diff(old, new)
    assert "manages" in diff["relationships"]["removed"]
    assert "owns" in diff["relationships"]["unchanged"]


def test_entity_level_diff_defs_structure():
    """Handles Pydantic $defs schema structure."""
    old = {"$defs": {"Company": {"type": "object", "properties": {"name": {}}}}}
    new = {
        "$defs": {
            "Company": {"type": "object", "properties": {"name": {}}},
            "Trust": {"type": "object", "properties": {}},
        }
    }
    diff = compute_entity_level_diff(old, new)
    assert "Trust" in diff["entity_types"]["added"]
    assert "Company" in diff["entity_types"]["unchanged"]
