"""Spec §11.2 items 2 and 3 — read-only allowlist contract tests.

Item 2 (``test_every_tool_in_readonly_allowlist``): every route a
tool may dispatch to is present in ``READONLY_ROUTES``.

Item 3 (``test_no_orphan_allowlist_entries``): every entry in
``READONLY_ROUTES`` is claimed by at least one registered tool —
orphans are dead code and fail CI.

Tools that dispatch to a single route bind ``__grace_route__`` on
the function object via ``@readonly_tool``. Tools that dispatch to
multiple allowlisted routes (currently only ``grace_get_entity``)
set ``__grace_routes__`` explicitly after definition. The meta tool
has neither — it has no HTTP leg.
"""

from __future__ import annotations


# Tools that have no HTTP leg. Skipped by item 2 and do not
# contribute to item 3's "claimed" set.
META_TOOL_NAMES = frozenset({"grace_explain_capabilities"})


# Read-only routes that intentionally do NOT have an MCP tool exposure.
# These appear in ``READONLY_ROUTES`` purely to opt them out of the
# default-deny admin-key admission tree (D237) — they are surfaced via
# the FastAPI HTTP API for the operator UI, not via the MCP read-only
# tool surface. Entries here must be justified by D-number.
#
# - ``POST /api/decomposition/runs/{run_id}/layer6/sample-cqs``
#   (Chunk 41, D328 spec §7.5): semantically read-only Layer 6
#   sample-CQ generation; no DB writes; consumed by the operator
#   Decomposition UI, not by any MCP client.
# - ``POST /api/permissions/matrix/hypothesis/generate`` and
#   ``POST /api/permissions/drift/run`` (Chunk 42, D246 mirror):
#   spawn the permissions CLI subprocess; the CLI owns persistence.
#   Read-only from the request thread's perspective. Consumed by the
#   operator Permissions UI; no MCP client surface required for v1.
# - ``POST /api/ontology/proposals/{proposal_id}/preview`` (Chunk 48, D392):
#   read-only POST returning parsed KGCL + diff without persisting.
#   Operator-facing; no MCP client surface required for v1.
# - ``POST /api/federation/registry/resolve`` and
#   ``POST /api/federation/validate-child-schema`` (Chunk 51, D402/D404):
#   federation read-only POSTs on READONLY_ROUTES; HTTP operator surface only.
# - ``GET /api/connectors``, ``GET /api/connectors/{connector_type}/health``,
#   ``GET /api/connectors/{connector_type}/sync/status`` (Chunk 53, D413):
#   connector operator GET surface; no MCP tool mapping in v1.
NO_MCP_TOOL_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/api/decomposition/runs/{run_id}/layer6/sample-cqs"),
        ("POST", "/api/permissions/matrix/hypothesis/generate"),
        ("POST", "/api/permissions/drift/run"),
        ("POST", "/api/ontology/proposals/{proposal_id}/preview"),
        ("POST", "/api/federation/registry/resolve"),
        ("POST", "/api/federation/validate-child-schema"),
        ("GET", "/api/connectors"),
        ("GET", "/api/connectors/{connector_type}/health"),
        ("GET", "/api/connectors/{connector_type}/sync/status"),
        # Chunk 58, D423 — communications draft-guidance is a read-only POST
        # exposed via the HTTP surface only; no MCP tool mapping in v1.
        ("POST", "/api/communications/draft-guidance"),
        # Chunk 72a, D468 — extraction jobs list is in READONLY_ROUTES for
        # admin-key bypass but has no dedicated MCP tool (operators use the
        # HTTP API; agents use grace_extraction_job_status for single-job
        # polling).
        ("GET", "/api/extraction/jobs"),
        # D522 session — review assist is a frontend-only conversational
        # endpoint (the "Something's off?" drawer); no MCP tool in v1.
        ("POST", "/api/ontology/review/{session_id}/assist"),
    }
)


def _import_tools_and_mcp():
    from src.mcp_server import (  # noqa: F401
        tools_change_directives,
        tools_discovery,
        tools_extraction,
        tools_graph,
        tools_meta,
        tools_ontology,
        tools_retrieval,
        tools_session,
        tools_review,
    )
    from src.mcp_server.server import READONLY_ROUTES, WRITABLE_REVIEW_ROUTES, mcp

    return mcp, READONLY_ROUTES, WRITABLE_REVIEW_ROUTES


def _routes_by_tool(mcp) -> dict[str, frozenset[tuple[str, str]]]:
    """Walk every registered tool and collect its bound routes.

    Supports both single-route (``__grace_route__``) and multi-route
    (``__grace_routes__``) tools. Returns ``frozenset()`` for tools
    with no HTTP leg (the meta tool).
    """
    bindings: dict[str, frozenset[tuple[str, str]]] = {}
    for name, tool in mcp._tool_manager._tools.items():
        fn = tool.fn
        single = getattr(fn, "__grace_route__", None)
        multi = getattr(fn, "__grace_routes__", None)
        if single is not None and multi is not None:
            raise AssertionError(
                f"{name} has both __grace_route__ and __grace_routes__"
            )
        if single is not None:
            bindings[name] = frozenset({single})
        elif multi is not None:
            bindings[name] = frozenset(multi)
        else:
            bindings[name] = frozenset()
    return bindings


def test_every_tool_in_allowlist():
    """Every tool route is in READONLY_ROUTES or WRITABLE_REVIEW_ROUTES."""
    mcp, readonly_routes, writable_routes = _import_tools_and_mcp()
    bindings = _routes_by_tool(mcp)
    combined = readonly_routes | writable_routes

    for name, routes in bindings.items():
        if name in META_TOOL_NAMES:
            assert not routes, (
                f"meta tool {name} should have no routes, got {routes}"
            )
            continue
        assert routes, f"{name} has no route binding"
        for route in routes:
            # GET routes are admitted via verb bypass (CP1 D363) —
            # they do not need frozenset membership.
            method = route[0]
            if method in {"GET", "HEAD", "OPTIONS"}:
                continue
            assert route in combined, (
                f"{name}: route {route} not in READONLY_ROUTES or "
                f"WRITABLE_REVIEW_ROUTES"
            )


def test_no_orphan_allowlist_entries():
    mcp, readonly_routes, writable_routes = _import_tools_and_mcp()
    bindings = _routes_by_tool(mcp)

    claimed: frozenset[tuple[str, str]] = frozenset().union(
        *bindings.values()
    )
    # Allowlist of routes that intentionally have no MCP tool (D237 +
    # ``NO_MCP_TOOL_ROUTES`` justification above).
    orphans = readonly_routes - claimed - NO_MCP_TOOL_ROUTES
    assert not orphans, f"Orphan READONLY_ROUTES entries: {orphans}"
