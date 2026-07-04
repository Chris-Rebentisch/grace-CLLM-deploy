"""READONLY_ROUTES additive-extension tests (Chunk 42, CP8).

The two read-only POSTs added by Chunk 42 (hypothesis generate +
drift run) extend the existing ``READONLY_ROUTES`` frozenset; no
prior entries are removed and no in-place restructure of
``src/mcp_server/server.py`` happens.
"""

from __future__ import annotations

from src.mcp_server.server import READONLY_ROUTES


def test_chunk42_readonly_posts_present():
    """The two new tuples must be in the frozenset."""
    assert (
        "POST",
        "/api/permissions/matrix/hypothesis/generate",
    ) in READONLY_ROUTES
    assert (
        "POST",
        "/api/permissions/drift/run",
    ) in READONLY_ROUTES


def test_prior_readonly_entries_preserved():
    """No pre-Chunk-42 tuples have been removed."""
    expected_prior = {
        ("GET", "/api/graph/entities/{grace_id}"),
        ("GET", "/api/graph/entities/lookup"),
        ("GET", "/api/graph/relationships/{grace_id}"),
        ("GET", "/api/graph/health"),
        ("GET", "/api/graph/info"),
        ("GET", "/api/ontology/active"),
        ("GET", "/api/ontology/modules/{module_name}"),
        ("GET", "/api/ontology/versions"),
        ("GET", "/api/discovery/cqs"),
        ("GET", "/api/discovery/cqs/summary"),
        ("GET", "/api/discovery/ollama-health"),
        ("GET", "/api/change-directives"),
        ("GET", "/api/change-directives/{directive_id}"),
        ("POST", "/api/retrieval/query"),
        ("POST", "/api/regeneration/query"),
        ("POST", "/api/decomposition/runs/{run_id}/layer6/sample-cqs"),
    }
    missing = expected_prior - READONLY_ROUTES
    assert not missing, f"Pre-Chunk-42 READONLY_ROUTES entries missing: {missing}"


def test_readonly_routes_is_frozenset():
    """READONLY_ROUTES must remain a frozenset (additive only)."""
    assert isinstance(READONLY_ROUTES, frozenset)
