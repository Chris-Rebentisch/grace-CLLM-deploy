"""Tests for OpenCypher query construction helpers."""

from datetime import datetime, UTC

from src.graph.cypher_utils import (
    build_property_map,
    build_set_clause,
    escape_cypher_string,
    format_cypher_value,
)


# ===========================================================================
# escape_cypher_string tests
# ===========================================================================


def test_escape_cypher_string_single_quotes():
    assert escape_cypher_string("O'Brien") == "O\\'Brien"


def test_escape_cypher_string_backslashes():
    assert escape_cypher_string("path\\to\\file") == "path\\\\to\\\\file"


def test_escape_cypher_string_both():
    assert escape_cypher_string("it's a\\path") == "it\\'s a\\\\path"


# ===========================================================================
# format_cypher_value tests
# ===========================================================================


def test_format_cypher_value_string():
    assert format_cypher_value("hello") == "'hello'"


def test_format_cypher_value_int():
    assert format_cypher_value(42) == "42"


def test_format_cypher_value_float():
    assert format_cypher_value(3.14) == "3.14"


def test_format_cypher_value_bool_true():
    assert format_cypher_value(True) == "true"


def test_format_cypher_value_bool_false():
    assert format_cypher_value(False) == "false"


def test_format_cypher_value_none():
    assert format_cypher_value(None) == "null"


def test_format_cypher_value_datetime():
    dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
    result = format_cypher_value(dt)
    assert result.startswith("'")
    assert result.endswith("'")
    assert "2024-01-15" in result


# ===========================================================================
# build_property_map tests
# ===========================================================================


def test_build_property_map_basic():
    result = build_property_map({"name": "Alice", "age": 30})
    assert "name: 'Alice'" in result
    assert "age: 30" in result
    assert result.startswith("{")
    assert result.endswith("}")


def test_build_property_map_skips_none():
    result = build_property_map({"name": "Alice", "age": None})
    assert "name: 'Alice'" in result
    assert "age" not in result


def test_build_property_map_empty_dict():
    assert build_property_map({}) == "{}"


def test_build_property_map_all_none():
    assert build_property_map({"a": None, "b": None}) == "{}"


# ===========================================================================
# build_set_clause tests
# ===========================================================================


def test_build_set_clause_basic():
    result = build_set_clause("n", {"name": "Bob", "age": 25})
    assert result.startswith("SET ")
    assert "n.name = 'Bob'" in result
    assert "n.age = 25" in result


def test_build_set_clause_skips_none():
    result = build_set_clause("n", {"name": "Bob", "age": None})
    assert "n.name = 'Bob'" in result
    assert "age" not in result


def test_build_set_clause_empty():
    assert build_set_clause("n", {}) == ""


def test_build_set_clause_all_none():
    assert build_set_clause("n", {"a": None}) == ""
