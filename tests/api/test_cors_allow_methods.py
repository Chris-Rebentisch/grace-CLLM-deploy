"""D450 contract test — introspection sweep (Chunk 66).

Iterates ``app.routes`` and asserts every registered PATCH / DELETE / PUT
route's methods are a subset of ``CORSMiddleware.allow_methods``.  Drift-
catcher: adding a route with an unlisted verb fails automatically.
"""

from __future__ import annotations

import pytest
from starlette.middleware.cors import CORSMiddleware

from src.api.main import app


def _get_cors_allow_methods() -> set[str]:
    """Read allow_methods from the live CORSMiddleware on ``app``."""
    for mw in app.user_middleware:
        if mw.cls is CORSMiddleware:
            return set(mw.kwargs["allow_methods"])
    pytest.fail("CORSMiddleware not found on app.user_middleware")


def _routes_with_verb(verb: str) -> list[str]:
    """Return full paths of routes that register *verb*."""
    matched: list[str] = []
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if methods and verb in methods:
            matched.append(route.path)
    return matched


class TestEveryRouteVerbAdmittedByCORS:
    """One test per verb that needs browser preflight admission."""

    def test_every_PATCH_route_admitted(self):
        """All PATCH routes must be admitted by CORSMiddleware."""
        allow = _get_cors_allow_methods()
        routes = _routes_with_verb("PATCH")
        assert len(routes) >= 1, "Expected at least one PATCH route"
        assert "PATCH" in allow, (
            f"PATCH is used by {routes} but missing from allow_methods"
        )

    def test_every_DELETE_route_admitted(self):
        """All DELETE routes must be admitted by CORSMiddleware."""
        allow = _get_cors_allow_methods()
        routes = _routes_with_verb("DELETE")
        assert len(routes) >= 1, "Expected at least one DELETE route"
        assert "DELETE" in allow, (
            f"DELETE is used by {routes} but missing from allow_methods"
        )

    def test_every_PUT_route_admitted(self):
        """All PUT routes must be admitted by CORSMiddleware.

        The 3 legacy PUT routes are semantically PATCH (partial updates);
        the PUT→PATCH refactor is deferred housekeeping (spec §14, D449).
        """
        allow = _get_cors_allow_methods()
        routes = _routes_with_verb("PUT")
        assert len(routes) >= 1, "Expected at least one PUT route"
        assert "PUT" in allow, (
            f"PUT is used by {routes} but missing from allow_methods"
        )
