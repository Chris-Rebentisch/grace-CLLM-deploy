"""Admin-key authentication middleware (Chunk 31, D236/D237; Chunk 45, D372).

This module implements a default-deny admission decision tree for HTTP
requests entering the GrACE FastAPI app. It sits between CORS (outer)
and ``GraphScopeMiddleware`` (inner) in the Starlette middleware stack.

Decision tree (in order):

1. Exempt paths (``EXEMPT_PATHS``) — always admitted.
2. Read-only verbs (``GET``/``HEAD``/``OPTIONS``) — admitted.
3. Read-only POST queries listed in ``READONLY_ROUTES`` (imported from
   ``src.mcp_server.server`` per D237) — admitted.
3b. Writable review routes (D363) — identified here, fall through to
    step 4 (localhost bypass) or step 5/6. NOT admitted unconditionally.
4. Localhost bypass: when ``GRACE_ADMIN_KEY`` is unset and the resolved
   peer is ``127.0.0.1`` or ``::1`` — admitted with structlog INFO.
5. Support-token bearer (Chunk 45, D372): ``Authorization: Bearer
   support:...`` — when ``GRACE_REMOTE_ACCESS_ENABLED=true``, validates
   token against ``support_sessions``, stamps ``request.state``, checks
   blocked routes. When ``false`` (default), complete no-op.
6. ``X-Admin-Key`` header compared via ``secrets.compare_digest`` —
   admitted on match, 401 on mismatch/absent (structlog WARN).

Configuration is env-only (``GRACE_ADMIN_KEY``, ``GRACE_REMOTE_ACCESS_ENABLED``).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from typing import Callable
from uuid import UUID

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.mcp_server.server import READONLY_ROUTES, WRITABLE_REVIEW_ROUTES


logger = structlog.get_logger()


# Read at module load; cached for lifetime of process.
GRACE_ADMIN_KEY: str = os.environ.get("GRACE_ADMIN_KEY", "")

# Chunk 45 D372: feature gate for support-token bearer admission.
# "true" (case-insensitive) enables step 5. Requires uvicorn restart
# to toggle (intentional — security-critical, R9).
GRACE_REMOTE_ACCESS_ENABLED: bool = (
    os.environ.get("GRACE_REMOTE_ACCESS_ENABLED", "false").lower() == "true"
)


EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "/metrics",
        "/metrics/",
        "/docs",
        "/openapi.json",
        "/redoc",
        "/api/health",
    }
)


# Starlette's TestClient sets ``scope["client"]`` to ``("testclient", 50000)``
# during in-process ASGI dispatch. That host never appears on a real network
# (uvicorn always reports the actual TCP peer), so treating it as loopback is
# semantically correct: in-process == loopback. Including it here keeps the
# pre-Chunk-31 testing convention working without forcing every test fixture
# to thread an explicit client tuple.
LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "testclient"})


def _match_path_template(template: str, path: str) -> bool:
    """Match a request path against a route template.

    Templates may include ``{placeholder}`` segments which match any
    single non-empty segment. Literal segments must match exactly.
    """
    tpl = template.strip("/").split("/")
    req = path.strip("/").split("/")
    if len(tpl) != len(req):
        return False
    return all(
        (t.startswith("{") and t.endswith("}")) or t == r
        for t, r in zip(tpl, req)
    )


def _is_readonly_route(method: str, path: str) -> bool:
    for route_method, template in READONLY_ROUTES:
        if route_method == method and _match_path_template(template, path):
            return True
    return False


def _is_writable_review_route(method: str, path: str) -> bool:
    """Step-3b (D363): identify writable review routes for MCP write tools."""
    for route_method, template in WRITABLE_REVIEW_ROUTES:
        if route_method == method and _match_path_template(template, path):
            return True
    return False


def _is_blocked_support_route(method: str, path: str) -> bool:
    """Check if (method, path) is in the blocked-from-support frozenset (D373)."""
    from src.support.refused_routes import BLOCKED_FROM_SUPPORT_SESSION_ROUTES
    return (method, path) in BLOCKED_FROM_SUPPORT_SESSION_ROUTES


def _lookup_support_session(token_hash_val: str):
    """Look up a support session by token hash. Returns the session or None.

    Lazy-imports to avoid circular imports and to keep the no-op path
    (GRACE_REMOTE_ACCESS_ENABLED=false) truly import-free.
    """
    try:
        from src.shared.database import get_session_factory
        from src.support.session_manager import lookup_by_token_hash

        db = get_session_factory()()
        try:
            return lookup_by_token_hash(db, token_hash_val)
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "auth.support_session_lookup_error",
            error=str(exc),
        )
        return None


class AuthMiddleware(BaseHTTPMiddleware):
    """Default-deny admin-key admission middleware (D236/D237, D372)."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        method = request.method

        # Step 0 (F-47 auth plumbing): optional asserted principal identity.
        # `from_admission_tree()` has always read request.state.user_id /
        # user_display_name, but nothing ever set them — so every retrieval
        # request resolved as an ANONYMOUS principal and per-principal
        # sensitivity zones (D521 + permission-matrix role clusters) could
        # never differentiate. The X-Principal-Id header (Person vertex
        # grace_id UUID) is an identity ASSERTION trusted at the same
        # perimeter as X-Admin-Key/loopback — it grants no admission and can
        # only select which sensitivity zone applies (anonymous falls back to
        # the matrix-global posture). Full authenticated identity is future
        # D-number territory; this mirrors the existing X-Admin-Key-User and
        # support-session request.state stamp patterns.
        principal_id = request.headers.get("X-Principal-Id", "").strip()
        if principal_id:
            try:
                request.state.user_id = str(UUID(principal_id))
                display = request.headers.get(
                    "X-Principal-Display-Name", ""
                ).strip()
                if display:
                    request.state.user_display_name = display[:200]
            except ValueError:
                logger.warning(
                    "auth.principal_id_header_invalid",
                    path=path,
                    value_prefix=principal_id[:12],
                )

        # Step 1: exempt paths.
        if path in EXEMPT_PATHS:
            return await call_next(request)

        # Step 2: read-only verbs.
        if method in {"GET", "HEAD", "OPTIONS"}:
            return await call_next(request)

        # Step 3: read-only POST queries (D237).
        if _is_readonly_route(method, path):
            return await call_next(request)

        # Step 3b (D363): writable review routes — identified here so they
        # fall through to step 4 (localhost bypass) or step 5/6 (support-token / admin-key).
        # They are NOT admitted unconditionally like read-only routes.
        if _is_writable_review_route(method, path):
            logger.debug("auth.writable_review_route", path=path)

        # Step 4: localhost bypass when no admin key is configured.
        client_host = request.client.host if request.client is not None else None
        if not GRACE_ADMIN_KEY and client_host in LOOPBACK_HOSTS:
            logger.info("auth.localhost_bypass", path=path)
            return await call_next(request)

        # Step 5: support-token bearer (Chunk 45, D372).
        # When GRACE_REMOTE_ACCESS_ENABLED is false (default), this entire
        # step is a no-op — no DB queries, no request.state stamps, no
        # imports triggered.
        if GRACE_REMOTE_ACCESS_ENABLED:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer support:"):
                token = auth_header[len("Bearer "):]
                token_hash_val = hashlib.sha256(token.encode()).hexdigest()

                # Query support_sessions for active session.
                session_record = _lookup_support_session(token_hash_val)

                if session_record is not None:
                    # Constant-time comparison (R4 — timing-attack mitigation).
                    # Invariant: D372 requires hmac.compare_digest for token
                    # hash comparison. Authorization: spec §13 R4.
                    if not hmac.compare_digest(
                        token_hash_val, session_record.token_hash
                    ):
                        # Should not happen (query matched), but defense-in-depth.
                        pass  # Fall through to step 6.
                    else:
                        # Stamp request state.
                        request.state.support_session_id = str(session_record.id)
                        request.state.support_session_email = (
                            session_record.granted_to_email
                        )
                        logger.info(
                            "auth.support_session_admit",
                            session_id=str(session_record.id),
                            path=path,
                        )

                        # Check blocked routes (D373 — belt side).
                        if _is_blocked_support_route(method, path):
                            logger.warning(
                                "auth.support_session_blocked_route",
                                session_id=str(session_record.id),
                                path=path,
                                method=method,
                            )
                            return JSONResponse(
                                status_code=403,
                                content={
                                    "detail": "route blocked for support sessions"
                                },
                            )

                        return await call_next(request)

                # Token not valid / expired / revoked — fall through to step 6.

        # Step 6: X-Admin-Key header check (renumbered from step 5, Chunk 45).
        submitted = request.headers.get("X-Admin-Key", "")
        if not GRACE_ADMIN_KEY:
            # Non-loopback (or unknown client) with no key configured.
            logger.warning("auth.admin_key_rejected", reason="missing")
            return JSONResponse(
                status_code=401,
                content={"detail": "admin key required"},
            )
        if not submitted:
            logger.warning("auth.admin_key_rejected", reason="missing")
            return JSONResponse(
                status_code=401,
                content={"detail": "admin key required"},
            )
        # Coalesce both operands to str to prevent TypeError (R4).
        if secrets.compare_digest(GRACE_ADMIN_KEY or "", submitted or ""):
            return await call_next(request)
        logger.warning("auth.admin_key_rejected", reason="mismatch")
        return JSONResponse(
            status_code=401,
            content={"detail": "admin key required"},
        )
