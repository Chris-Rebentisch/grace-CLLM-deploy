"""MCP route-surface contract test.

``src/mcp_server/server.py`` maintains two frozensets of
``(method, path)`` tuples — ``READONLY_ROUTES`` and
``WRITABLE_REVIEW_ROUTES`` — that gate which FastAPI routes MCP tools
may dispatch to. Nothing previously verified that those tuples still
correspond to routes the FastAPI app actually serves; a renamed or
removed route would silently strand its frozenset entry (tools would
404 at call time).

This test enumerates the live route table of ``src.api.main.app`` and
asserts every frozenset entry exists in it, catching stale entries at
CI time instead of at tool-call time.
"""

from __future__ import annotations

from fastapi.routing import APIRoute


def _app_route_table() -> frozenset[tuple[str, str]]:
    """Enumerate (method, path) pairs actually served by the app."""
    from src.api.main import app

    pairs: set[tuple[str, str]] = set()
    for route in app.routes:
        if isinstance(route, APIRoute):
            for method in route.methods or ():
                pairs.add((method, route.path))
    return frozenset(pairs)


def _frozensets() -> tuple[frozenset, frozenset]:
    from src.mcp_server.server import READONLY_ROUTES, WRITABLE_REVIEW_ROUTES

    return READONLY_ROUTES, WRITABLE_REVIEW_ROUTES


def test_readonly_routes_exist_in_app():
    """Every READONLY_ROUTES entry is a real (method, path) in the app."""
    readonly_routes, _ = _frozensets()
    table = _app_route_table()
    stale = sorted(r for r in readonly_routes if r not in table)
    assert not stale, (
        f"Stale READONLY_ROUTES entries not served by the FastAPI app: {stale}"
    )


def test_writable_review_routes_exist_in_app():
    """Every WRITABLE_REVIEW_ROUTES entry is a real (method, path) in the app."""
    _, writable_routes = _frozensets()
    table = _app_route_table()
    stale = sorted(r for r in writable_routes if r not in table)
    assert not stale, (
        "Stale WRITABLE_REVIEW_ROUTES entries not served by the FastAPI app: "
        f"{stale}"
    )
