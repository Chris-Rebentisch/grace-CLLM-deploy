"""Chunk 72a CP3 — MCP tool count and per-decorator inventory tests."""

from __future__ import annotations

import asyncio


def test_mcp_tool_count_37():
    """Total MCP tool count is 37 (36 + grace_relationship_coverage)."""
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
    assert len(tools) == 37, (
        f"Expected 37 tools, got {len(tools)}: "
        f"{sorted(t.name for t in tools)}"
    )


def test_mcp_decorator_inventory():
    """Per-decorator inventory: 22 @readonly_tool + 4 @mcp.tool() + 11 @writable_review_tool.

    Counts verified by scanning tool module functions for __grace_route__
    attributes and classifying against READONLY_ROUTES / WRITABLE_REVIEW_ROUTES.
    """
    import inspect

    from src.mcp_server import (
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
    from src.mcp_server.server import (
        READONLY_ROUTES,
        WRITABLE_REVIEW_ROUTES,
        mcp,
    )

    # Collect all registered tool names from FastMCP
    registered_tools = asyncio.run(mcp.list_tools())
    registered_names = {t.name for t in registered_tools}

    # Scan module-level functions that match registered tool names
    modules = [
        tools_change_directives, tools_discovery, tools_extraction,
        tools_graph, tools_meta, tools_ontology, tools_retrieval,
        tools_review, tools_session,
    ]

    readonly_count = 0
    writable_count = 0
    bare_count = 0

    for mod in modules:
        for name, obj in inspect.getmembers(mod, inspect.isfunction):
            if name not in registered_names:
                continue

            route = getattr(obj, "__grace_route__", None)
            routes = getattr(obj, "__grace_routes__", None)

            if route and route in READONLY_ROUTES:
                readonly_count += 1
            elif route and route in WRITABLE_REVIEW_ROUTES:
                writable_count += 1
            else:
                bare_count += 1

    assert readonly_count == 22, f"Expected 22 @readonly_tool, got {readonly_count}"
    assert bare_count == 4, f"Expected 4 @mcp.tool(), got {bare_count}"
    assert writable_count == 11, f"Expected 11 @writable_review_tool, got {writable_count}"
