"""Tests for D267 Query_Event / Response_Event / retrieved_from DDL generation.

Chunk 35b CP1 — verifies that the existing
:func:`src.graph.ddl_generator.generate_meta_entity_ddl` and the
``META_ENTITY_TYPES`` / ``META_EDGE_TYPES`` registries produce DDL for the
two new vertex types and the new edge type with all spec'd properties.
No code change is expected in ``ddl_generator.py``; the iterators handle
the new entries automatically.
"""

from __future__ import annotations

from src.graph.ddl_generator import generate_meta_entity_ddl
from src.graph.migration_types import (
    META_EDGE_TYPES,
    META_ENTITY_TYPES,
    QUERY_EVENT_PROPERTIES,
    RESPONSE_EVENT_PROPERTIES,
)


def test_vertex_ddl_contains_query_event():
    """`generate_meta_entity_ddl` emits a CREATE VERTEX TYPE Query_Event statement
    plus a CREATE PROPERTY for every Query_Event property.
    """
    stmts = generate_meta_entity_ddl()
    assert "CREATE VERTEX TYPE Query_Event IF NOT EXISTS" in stmts
    for prop in QUERY_EVENT_PROPERTIES:
        expected = (
            f"CREATE PROPERTY Query_Event.{prop['name']} IF NOT EXISTS {prop['type']}"
        )
        assert expected in stmts, f"Missing Query_Event property DDL: {expected}"


def test_vertex_ddl_contains_response_event():
    """`generate_meta_entity_ddl` emits a CREATE VERTEX TYPE Response_Event statement
    plus a CREATE PROPERTY for every Response_Event property.
    """
    stmts = generate_meta_entity_ddl()
    assert "CREATE VERTEX TYPE Response_Event IF NOT EXISTS" in stmts
    for prop in RESPONSE_EVENT_PROPERTIES:
        expected = (
            f"CREATE PROPERTY Response_Event.{prop['name']} IF NOT EXISTS {prop['type']}"
        )
        assert expected in stmts, f"Missing Response_Event property DDL: {expected}"


def test_edge_ddl_contains_retrieved_from():
    """`generate_meta_entity_ddl` emits CREATE EDGE TYPE retrieved_from
    plus a CREATE PROPERTY for every retrieved_from edge property.
    """
    stmts = generate_meta_entity_ddl()
    assert "CREATE EDGE TYPE retrieved_from IF NOT EXISTS" in stmts
    edge_props = META_EDGE_TYPES["retrieved_from"]
    for prop in edge_props:
        expected = (
            f"CREATE PROPERTY retrieved_from.{prop['name']} "
            f"IF NOT EXISTS {prop['type']}"
        )
        assert expected in stmts, f"Missing retrieved_from property DDL: {expected}"


def test_property_completeness():
    """Spec §8 property contracts: ensure every spec'd property is present
    in the registry — guards against silent registry drift.
    """
    # Query_Event
    qe_names = {p["name"] for p in META_ENTITY_TYPES["Query_Event"]}
    assert qe_names == {
        "query_event_id",
        "query_text",
        "query_timestamp",
        "session_id",
        "retrieval_mode",
        "strategies_fired",
        "total_candidates",
        # D349 / Chunk 43 — sensitivity audit annotation on Query_Event
        "sensitivity_tags",
        "sensitivity_tags_matrix_id",
        # D377 / Chunk 45 — support session audit stamp
        "support_session_id",
        # D364 / Chunk 44 — MCP agent identity audit trail
        "agent_id",
    }

    # Response_Event
    re_names = {p["name"] for p in META_ENTITY_TYPES["Response_Event"]}
    assert re_names == {
        "response_event_id",
        "query_event_id",
        "result_count",
        "serialization_format",
        "latency_ms_total",
        "created_at",
    }

    # retrieved_from edge
    rf_names = {p["name"] for p in META_EDGE_TYPES["retrieved_from"]}
    assert rf_names == {"grace_id", "created_at", "query_event_id", "rank_ordinal"}
