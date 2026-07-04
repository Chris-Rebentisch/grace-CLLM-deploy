"""OpenCypher query construction helpers.

Builds safe Cypher literals and clauses for ArcadeDB DML/DQL operations.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def escape_cypher_string(value: str) -> str:
    """Escape backslashes and single quotes for Cypher string literals."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def format_cypher_value(value: Any) -> str:
    """Format a Python value as a Cypher literal.

    - str -> 'escaped_string'
    - int/float -> numeric literal
    - bool -> true/false
    - None -> null
    - datetime -> 'ISO8601_string' (ArcadeDB auto-coerces to DATETIME)
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(value)
    if isinstance(value, datetime):
        return f"'{escape_cypher_string(value.isoformat())}'"
    if isinstance(value, str):
        return f"'{escape_cypher_string(value)}'"
    return f"'{escape_cypher_string(str(value))}'"


def build_property_map(props: dict[str, Any]) -> str:
    """Build a Cypher property map string: {key1: 'val1', key2: 42}.

    Skips None values. Uses format_cypher_value for each value.
    Returns empty string if no non-None values remain.
    """
    parts = []
    for key, value in props.items():
        if value is None:
            continue
        parts.append(f"{key}: {format_cypher_value(value)}")
    if not parts:
        return "{}"
    return "{" + ", ".join(parts) + "}"


def build_set_clause(variable: str, props: dict[str, Any]) -> str:
    """Build a Cypher SET clause: SET n.key1 = 'val1', n.key2 = 42.

    For partial updates. Skips None values.
    Returns empty string if no non-None values remain.
    """
    parts = []
    for key, value in props.items():
        if value is None:
            continue
        parts.append(f"{variable}.{key} = {format_cypher_value(value)}")
    if not parts:
        return ""
    return "SET " + ", ".join(parts)
