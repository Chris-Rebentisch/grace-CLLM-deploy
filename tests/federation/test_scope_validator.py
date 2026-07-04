"""Tests for the scope validator (Chunk 51 CP6, D405)."""

from __future__ import annotations

import pytest

from src.federation.scope_validator import validate_child_schema


def _schema(entity_types: dict) -> dict:
    """Helper to build a flat GrACE-format schema."""
    return {"entity_types": entity_types}


class TestValidateChildSchema:

    def test_add_property_passes(self):
        mother = _schema({
            "Legal_Entity": {
                "properties": {
                    "name": {"type": "string"},
                }
            }
        })
        child = _schema({
            "Legal_Entity": {
                "properties": {
                    "name": {"type": "string"},
                    "tax_id": {"type": "string"},
                }
            }
        })
        result = validate_child_schema(child, mother)
        assert result.passed is True

    def test_remove_property_fails(self):
        mother = _schema({
            "Legal_Entity": {
                "properties": {
                    "name": {"type": "string"},
                    "address": {"type": "string"},
                }
            }
        })
        child = _schema({
            "Legal_Entity": {
                "properties": {
                    "name": {"type": "string"},
                    # address removed
                }
            }
        })
        result = validate_child_schema(child, mother)
        assert result.passed is False
        assert any("address" in e for tr in result.type_results for e in tr.errors)

    def test_change_type_fails(self):
        mother = _schema({
            "Legal_Entity": {
                "properties": {
                    "name": {"type": "string"},
                }
            }
        })
        child = _schema({
            "Legal_Entity": {
                "properties": {
                    "name": {"type": "integer"},
                }
            }
        })
        result = validate_child_schema(child, mother)
        assert result.passed is False
        assert any("Type change" in e for tr in result.type_results for e in tr.errors)

    def test_new_type_passes(self):
        mother = _schema({
            "Legal_Entity": {
                "properties": {"name": {"type": "string"}}
            }
        })
        child = _schema({
            "Legal_Entity": {
                "properties": {"name": {"type": "string"}}
            },
            "Procore_Task": {
                "properties": {"task_name": {"type": "string"}}
            },
        })
        result = validate_child_schema(child, mother)
        assert result.passed is True

    def test_no_mother_types_in_child_passes(self):
        mother = _schema({
            "Legal_Entity": {
                "properties": {"name": {"type": "string"}}
            }
        })
        child = _schema({
            "Procore_Task": {
                "properties": {"task_name": {"type": "string"}}
            }
        })
        result = validate_child_schema(child, mother)
        assert result.passed is True

    def test_empty_schemas_pass(self):
        result = validate_child_schema({}, {})
        assert result.passed is True

    def test_nested_types_multiple_failures(self):
        mother = _schema({
            "Person": {
                "properties": {
                    "name": {"type": "string"},
                    "age": {"type": "integer"},
                }
            },
            "Address": {
                "properties": {
                    "street": {"type": "string"},
                }
            },
        })
        child = _schema({
            "Person": {
                "properties": {
                    "name": {"type": "string"},
                    # age removed
                }
            },
            "Address": {
                "properties": {
                    "street": {"type": "integer"},  # type changed
                }
            },
        })
        result = validate_child_schema(child, mother)
        assert result.passed is False
        assert len(result.type_results) == 2

    def test_defs_format_schema(self):
        """Handles Pydantic $defs format."""
        mother = {"$defs": {
            "Entity": {"properties": {"name": {"type": "string"}}}
        }}
        child = {"$defs": {
            "Entity": {"properties": {
                "name": {"type": "string"},
                "extra": {"type": "number"},
            }}
        }}
        result = validate_child_schema(child, mother)
        assert result.passed is True
