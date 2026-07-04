"""Spec §11.2 item 1 — test_all_tools_registered.

All tools register on the shared ``FastMCP`` instance at import
time. Each has a non-empty description and a valid input JSON Schema
produced by the SDK from the function signature.
"""

from __future__ import annotations

import asyncio


EXPECTED_TOOL_NAMES = {
    # Retrieval
    "grace_search",
    "grace_answer",
    # Graph
    "grace_get_entity",
    "grace_get_neighborhood",
    "grace_get_relationship",
    "grace_graph_health",
    "grace_graph_info",
    "grace_graph_counts",
    "grace_graph_aggregate",
    "grace_relationship_coverage",
    # Ontology
    "grace_get_active_schema",
    "grace_get_module_schema",
    "grace_list_schema_versions",
    # Discovery
    "grace_list_cqs",
    "grace_cq_summary",
    "grace_ollama_health",
    # Change directives (Chunk 39)
    "grace_list_change_directives",
    "grace_get_change_directive",
    # Meta
    "grace_explain_capabilities",
    # Session lifecycle (Chunk 44, D365)
    "grace_session_start",
    "grace_session_advance_phase",
    "grace_session_close",
    # Review decision (Chunk 44, D366–D367)
    "grace_review_next_element",
    "grace_review_session_summary",
    "grace_review_decide",
    "grace_laddering_followup",
    "grace_teachback_capture",
    # Extraction (Chunk 72a, D468)
    "grace_list_extraction_events",
    "grace_get_extraction_event",
    "grace_list_quarantined_claims",
    "grace_get_claim",
    "grace_extraction_job_status",
    "grace_extract_document",
    "grace_batch_extract",
    "grace_accept_claim",
    "grace_reject_claim",
    "grace_build_retrieval_indexes",
}


def test_all_tools_registered():
    # Import tool modules for registration side effects.
    from src.mcp_server import (  # noqa: F401
        tools_change_directives,
        tools_discovery,
        tools_extraction,
        tools_graph,
        tools_meta,
        tools_ontology,
        tools_retrieval,
        tools_review,
        tools_session,
    )
    from src.mcp_server.server import mcp

    tools = asyncio.run(mcp.list_tools())
    assert len(tools) == len(EXPECTED_TOOL_NAMES)
    actual_names = {t.name for t in tools}
    assert actual_names == EXPECTED_TOOL_NAMES

    for tool in tools:
        assert tool.description, f"{tool.name} missing description"
        assert tool.description.strip(), f"{tool.name} empty description"
        assert tool.inputSchema is not None, (
            f"{tool.name} missing inputSchema"
        )
        assert isinstance(tool.inputSchema, dict)
        assert tool.inputSchema.get("type") == "object"
