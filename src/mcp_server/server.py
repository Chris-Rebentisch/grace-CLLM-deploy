"""FastMCP server instance and read-only route allowlist (D186, spec §6).

This module owns two things:

1. ``READONLY_ROUTES`` — the frozen set of ``(method, path)`` tuples
   every MCP tool in Chunk 26 is allowed to target. The name is part
   of the Chunk 31 forward contract (spec §4.4): Chunk 31's admin-key
   middleware must classify routes by semantics (not verb), and the
   two approved POSTs below are read-only queries that Chunk 31 must
   exempt. Future mutating tools live in a separate allowlist with a
   separate decorator, not an extension of this one.

2. ``readonly_tool(method, path)`` — the tool registration decorator
   that asserts ``(method, path) in READONLY_ROUTES`` at import time.
   A bad tuple raises ``MCPReadOnlyViolation`` before the server can
   start, which is the intended forcing function.

The shared ``mcp`` FastMCP instance is also defined here so every
``tools_*`` module can import it and register tools against one
server.
"""

from __future__ import annotations

from typing import Callable

from mcp.server.fastmcp import FastMCP

from src.mcp_server.errors import MCPReadOnlyViolation


# D363 — Chunk 44: five mutating review routes exposed to MCP write tools.
# Disjoint from READONLY_ROUTES by invariant; enforced by unit test.
WRITABLE_REVIEW_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/api/ontology/review/start"),
        ("POST", "/api/ontology/review/{session_id}/decide"),
        ("POST", "/api/elicitation/events"),
        ("POST", "/api/regeneration/close-summary"),
        ("POST", "/api/regeneration/close-confirm"),
        # Chunk 72a (D468): extraction job spawn + claim disposition +
        # retrieval index rebuild.
        ("POST", "/api/extraction/jobs"),
        ("POST", "/api/claims/{claim_id}/accept"),
        ("POST", "/api/claims/{claim_id}/reject"),
        ("POST", "/api/retrieval/build-indexes"),
    }
)

READONLY_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        # GET routes — unambiguously read-only.
        ("GET", "/api/graph/entities/{grace_id}"),
        ("GET", "/api/graph/entities/lookup"),
        ("GET", "/api/graph/entities/{grace_id}/neighborhood"),
        ("GET", "/api/graph/relationships/{grace_id}"),
        ("GET", "/api/graph/health"),
        ("GET", "/api/graph/info"),
        ("GET", "/api/graph/counts"),
        ("GET", "/api/graph/aggregate"),
        ("GET", "/api/graph/relationship-coverage"),
        ("GET", "/api/ontology/active"),
        ("GET", "/api/ontology/modules/{module_name}"),
        ("GET", "/api/ontology/versions"),
        ("GET", "/api/discovery/cqs"),
        ("GET", "/api/discovery/cqs/summary"),
        ("GET", "/api/discovery/ollama-health"),
        ("GET", "/api/change-directives"),
        ("GET", "/api/change-directives/{directive_id}"),
        # Approved read-only POST routes — queries with request bodies
        # per FastAPI convention; semantically read-only.
        ("POST", "/api/retrieval/query"),
        ("POST", "/api/regeneration/query"),
        # Chunk 41 (D237 / D328 spec §7.5): Layer 6 sample-CQ generation
        # is an LLM call + transient response with no DB writes —
        # semantically read-only despite the path-templated POST.
        ("POST", "/api/decomposition/runs/{run_id}/layer6/sample-cqs"),
        # Chunk 42 (CP8, D246 mirror): both trigger routes spawn the
        # permissions CLI subprocess and persist a placeholder run /
        # queue row only. They are read-only from the request thread's
        # perspective; the CLI owns any subsequent state mutation.
        ("POST", "/api/permissions/matrix/hypothesis/generate"),
        ("POST", "/api/permissions/drift/run"),
        # Chunk 48 (D393/D237): proposal preview is read-only — parses
        # KGCL, applies to active schema JSON, computes diff. No DB write.
        ("POST", "/api/ontology/proposals/{proposal_id}/preview"),
        # D522 session: review assist is an LLM explanation call with no
        # review-session mutation — semantically read-only despite the POST.
        ("POST", "/api/ontology/review/{session_id}/assist"),
        # Chunk 51 (D402/D404): federation resolve + validate are read-only.
        ("POST", "/api/federation/registry/resolve"),
        ("POST", "/api/federation/validate-child-schema"),
        # Chunk 53 (D409): connector read-only GET routes.
        ("GET", "/api/connectors"),
        ("GET", "/api/connectors/{connector_type}/health"),
        ("GET", "/api/connectors/{connector_type}/sync/status"),
        # Chunk 58 (CP8): draft-guidance is a read-only POST — builds
        # LLM-consumable style payload from DB without any writes.
        ("POST", "/api/communications/draft-guidance"),
        # Chunk 72a (D468): extraction event/job/claim read routes.
        ("GET", "/api/extraction/events"),
        ("GET", "/api/extraction/events/{event_id}"),
        ("GET", "/api/extraction/jobs/{job_id}"),
        ("GET", "/api/extraction/jobs"),
        ("GET", "/api/claims/{claim_id}"),
        ("GET", "/api/claims"),
    }
)


mcp: FastMCP = FastMCP(name="grace")


def readonly_tool(method: str, path: str) -> Callable:
    """Register an MCP tool that targets one allowlisted route.

    Registration-time enforcement (D186, spec §6.3): a tuple not in
    ``READONLY_ROUTES`` raises ``MCPReadOnlyViolation`` at import
    time, preventing server startup. Tool functions also get a
    ``__grace_route__`` attribute used by the endpoint-mapping
    contract test (spec §11.2 item 2).

    For tools that dispatch to more than one allowlisted route
    (currently only ``grace_get_entity``), bypass this decorator —
    use a bare ``@mcp.tool()`` and set ``__grace_routes__`` on the
    function object after definition.
    """
    if (method, path) not in READONLY_ROUTES:
        raise MCPReadOnlyViolation(
            f"({method}, {path}) not in READONLY_ROUTES"
        )

    def decorator(fn: Callable) -> Callable:
        fn.__grace_route__ = (method, path)  # type: ignore[attr-defined]
        return mcp.tool()(fn)

    return decorator


def writable_review_tool(method: str, path: str) -> Callable:
    """Register an MCP tool that targets one writable review route (D363).

    Import-time enforcement: a tuple not in ``WRITABLE_REVIEW_ROUTES``
    raises ``MCPReadOnlyViolation`` before the server can start. Sets
    ``__grace_route__`` for endpoint-mapping contract tests.
    """
    if (method, path) not in WRITABLE_REVIEW_ROUTES:
        raise MCPReadOnlyViolation(
            f"({method}, {path}) not in WRITABLE_REVIEW_ROUTES"
        )

    def decorator(fn: Callable) -> Callable:
        fn.__grace_route__ = (method, path)  # type: ignore[attr-defined]
        return mcp.tool()(fn)

    return decorator


# ----- Defense-in-depth Layer 4: MCP tool gate (Chunk 42, CP9, D335) ----


def permission_gated_tool(
    resource_kind: str,
    resource_label: str,
    action: str = "view",
) -> Callable:
    """Wrap an MCP tool function so each invocation consults the
    process-global :class:`Enforcer` (D334) before executing.

    The gate is enforcement-only — tool registration, the tool
    inventory, and the readonly-route allowlist are unaffected. Stack
    the decorator INSIDE ``@readonly_tool`` (or a bare ``@mcp.tool()``)
    so the route-binding and the runtime gate are independently
    auditable:

        @readonly_tool("POST", "/api/retrieval/query")
        @permission_gated_tool("retrieval_query_event", "global", "view")
        async def grace_search(...): ...

    Denials raise :class:`PermissionError` so the FastMCP server returns
    a structured tool-error to the caller; the tool body is never
    invoked. The gate is intentionally simple: it does not introspect
    arguments. Per-resource-instance gating belongs at the API layer
    (the post-filter wrapper for retrieval, the visibility resolver
    for change directives).

    The gate looks up the principal via
    :func:`src.permissions.principal_context.from_admission_tree`
    against the FastMCP request context when one is available; in plain
    direct-call usage (as in tests) the gate falls back to a default
    :class:`User` with ``user_id=None``, which is treated as a
    permission-evaluation-time anonymous caller and subject to the
    matrix's ``default_decision`` (default-deny).

    F-032e / ISS-0022: the gate honors
    :func:`src.permissions.api_middleware.permission_enforcement_enabled`
    (``GRACE_PERMISSION_ENFORCEMENT_ENABLED``, default OFF) so the MCP
    and REST enforcement planes agree — disabled means pass-through on
    both, enabled means both enforce.
    """

    def decorator(fn: Callable) -> Callable:
        import functools

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            _consult_enforcer(resource_kind, resource_label, action)
            return await fn(*args, **kwargs)

        @functools.wraps(fn)
        def sync_wrapper(*args, **kwargs):
            _consult_enforcer(resource_kind, resource_label, action)
            return fn(*args, **kwargs)

        wrapper: Callable
        if _is_coroutine_function(fn):
            wrapper = async_wrapper
        else:
            wrapper = sync_wrapper

        # Carry the gate parameters on the wrapper so tests + the
        # endpoint-mapping contract can inspect them without monkey-
        # patching.
        wrapper.__grace_permission_gate__ = (  # type: ignore[attr-defined]
            resource_kind,
            resource_label,
            action,
        )
        # Preserve any pre-existing readonly route binding.
        for attr in ("__grace_route__", "__grace_routes__"):
            if hasattr(fn, attr):
                setattr(wrapper, attr, getattr(fn, attr))
        return wrapper

    return decorator


def _is_coroutine_function(fn: Callable) -> bool:
    import inspect

    return inspect.iscoroutinefunction(fn)


def _consult_enforcer(
    resource_kind: str, resource_label: str, action: str
) -> None:
    """Lazy-import the enforcer so this module stays import-cheap and so
    the permissions package's import order remains acyclic.
    """
    from src.permissions.api_middleware import permission_enforcement_enabled
    from src.permissions.enforcer import get_enforcer
    from src.permissions.models import Allow
    from src.permissions.principal_context import User

    # F-032e / ISS-0022: enforcement-plane parity with the REST API. The
    # API's PermissionMatrixMiddleware is gated on the same helper
    # (GRACE_PERMISSION_ENFORCEMENT_ENABLED, default OFF — D528 posture);
    # this gate previously enforced unconditionally, so with no ratified
    # matrix the MCP claim tools were unusable (no_active_matrix) while
    # the REST API allowed the identical operation. When enforcement is
    # disabled the MCP gate passes through exactly like the REST plane;
    # when enabled, both planes enforce.
    if not permission_enforcement_enabled():
        return

    enforcer = get_enforcer()
    # MCP tool calls run from the FastMCP request thread; v1 does not
    # thread an authenticated principal through the SDK call. The
    # default User (user_id=None) is treated as anonymous and falls to
    # the matrix's default_decision (deny under OWASP A01).
    principal = User(user_id=None, admin_key_present=False)
    decision = enforcer.enforce(principal, resource_kind, resource_label, action)
    if not isinstance(decision, Allow):
        reason = getattr(getattr(decision, "reason", None), "code", "denied")
        raise PermissionError(
            f"permission denied: {reason} "
            f"(kind={resource_kind}, label={resource_label}, action={action})"
        )
