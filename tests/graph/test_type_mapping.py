"""Tests for GrACE to ArcadeDB data type mapping."""

from src.graph.type_mapping import map_data_type


def test_map_string():
    """String maps to STRING."""
    assert map_data_type("string") == "STRING"


def test_map_datetime():
    """Datetime maps to DATETIME."""
    assert map_data_type("datetime") == "DATETIME"


def test_map_float_to_double():
    """GrACE float maps to ArcadeDB DOUBLE for precision."""
    assert map_data_type("float") == "DOUBLE"


def test_map_unknown_type_returns_string_with_warning():
    """Unknown types fall back to STRING with a warning."""
    result = map_data_type("foobar")
    assert result == "STRING"
