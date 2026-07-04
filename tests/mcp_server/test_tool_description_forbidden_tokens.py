"""EC-12 forbidden-token scan for MCP tool descriptions (Chunk 46, D378.g).

Three tests scanning all 23 registered MCP tool descriptions for the
EC-12 forbidden vocabulary: ``incorrect``, ``failure``, ``deficit``,
``drift``, ``blind spot``, ``mistake``, ``wrong``.
"""

from __future__ import annotations

import re


EC12_FORBIDDEN_TOKENS: tuple[str, ...] = (
    "incorrect",
    "failure",
    "deficit",
    "drift",
    "blind spot",
    "mistake",
    "wrong",
)


def _get_tool_descriptions() -> dict[str, str]:
    """Import all MCP tool modules and return {name: description}."""
    from src.mcp_server import (  # noqa: F401
        tools_change_directives,
        tools_discovery,
        tools_graph,
        tools_meta,
        tools_ontology,
        tools_retrieval,
        tools_session,
        tools_review,
    )
    from src.mcp_server.server import mcp

    return {
        name: (tool.fn.__doc__ or "").strip()
        for name, tool in mcp._tool_manager._tools.items()
    }


def test_no_ec12_forbidden_tokens_in_tool_descriptions():
    """No MCP tool description contains EC-12 forbidden vocabulary."""
    descs = _get_tool_descriptions()
    compiled = [re.compile(rf"\b{re.escape(tok)}\b", re.IGNORECASE) for tok in EC12_FORBIDDEN_TOKENS]
    violations = []
    for name, desc in descs.items():
        for pat in compiled:
            if pat.search(desc):
                violations.append(f"{name}: matches '{pat.pattern}'")
    assert not violations, f"EC-12 violations found:\n" + "\n".join(violations)


def test_all_23_tools_have_descriptions():
    """Every registered MCP tool has a non-empty description."""
    descs = _get_tool_descriptions()
    assert len(descs) == 27, f"Expected 27 tools, got {len(descs)}"
    for name, desc in descs.items():
        assert desc, f"Tool {name!r} has empty description"


def test_ec12_forbidden_tokens_list_is_complete():
    """The forbidden-token list matches the canonical EC-12 set.

    Guard: if someone adds a new forbidden token to the frontend copy.ts
    files, this test reminds them to add it here too.
    """
    expected = {"incorrect", "failure", "deficit", "drift", "blind spot", "mistake", "wrong"}
    actual = set(EC12_FORBIDDEN_TOKENS)
    assert actual == expected, f"Token list drift: {actual.symmetric_difference(expected)}"
