"""Permission Matrix API middleware (Chunk 42, D334 / R7).

Composes **after** the admission tree (``AuthMiddleware``). Admission
errors return 401; permission denials return 403. Read-only requests
(GET/HEAD/OPTIONS and the ``READONLY_ROUTES`` POST allowlist) pass
through unmodified — the post-filter retrieval wrapper, retrieval
inspector, and visibility resolver gate read paths separately.

Mutating requests are routed to ``Enforcer.enforce()`` against a
``PrincipalContext`` constructed via
``principal_context.from_admission_tree()``. When the active matrix is
absent, the enforcer denies with ``no_active_matrix`` and the middleware
returns 403; this is OWASP A01 default-deny on a mutating route.

The middleware does NOT replace ``AuthMiddleware``. The admission tree
owns 401 and the loopback bypass; this middleware only owns 403 on the
permissions axis.
"""

from __future__ import annotations

import os
from typing import Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.mcp_server.server import READONLY_ROUTES
from src.permissions.enforcer import get_enforcer
from src.permissions.models import Allow
from src.permissions.principal_context import from_admission_tree


logger = structlog.get_logger()


def permission_enforcement_enabled() -> bool:
    """Whether the Permission Matrix is enforced on mutating ontology/graph routes.

    Opt-in via ``GRACE_PERMISSION_ENFORCEMENT_ENABLED`` (default OFF). The
    permission-matrix system (Chunk 42) is multi-tenant governance; a fresh
    single-operator / airgap deployment has no ratified operator matrix, so
    enforcing it would deny-all (``no_active_matrix``) and block basic ontology
    onboarding. Enforcement therefore stays OFF until an operator sets the flag
    AND ratifies a matrix that grants their principal. Set the env var to
    ``1``/``true``/``yes``/``on`` to enable.
    """
    return os.environ.get(
        "GRACE_PERMISSION_ENFORCEMENT_ENABLED", ""
    ).strip().lower() in {"1", "true", "yes", "on"}


def _match_path_template(template: str, path: str) -> bool:
    tpl = template.strip("/").split("/")
    req = path.strip("/").split("/")
    if len(tpl) != len(req):
        return False
    return all(
        (t.startswith("{") and t.endswith("}")) or t == r
        for t, r in zip(tpl, req)
    )


def _is_readonly(method: str, path: str) -> bool:
    if method in {"GET", "HEAD", "OPTIONS"}:
        return True
    for route_method, template in READONLY_ROUTES:
        if route_method == method and _match_path_template(template, path):
            return True
    return False


# Resource-kind / action mapping per route prefix. Routes outside the
# table are skipped (the enforcer is consulted on mutating routes only
# when a route is recognized as gating a permission-relevant resource).
_ROUTE_RESOURCE_TABLE: list[tuple[str, str, str, str]] = [
    # (path_prefix, method, resource_kind, action)
    ("/api/permissions/matrix/ratify", "POST", "ontology_module", "ratify"),
    ("/api/change-directives", "POST", "change_directive", "edit"),
    ("/api/graph/entities", "POST", "graph_entity", "edit"),
    ("/api/ontology", "POST", "ontology_module", "edit"),
]


def _resolve_resource(
    method: str, path: str
) -> tuple[str, str, str] | None:
    for prefix, route_method, kind, action in _ROUTE_RESOURCE_TABLE:
        if method == route_method and path.startswith(prefix):
            return (kind, prefix, action)
    return None


class PermissionMatrixMiddleware(BaseHTTPMiddleware):
    """Permission enforcement layer composing after AuthMiddleware.

    Read-only requests pass through; mutating requests are gated by
    ``Enforcer.enforce()``. Denials return 403 with a structured body.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        method = request.method
        path = request.url.path

        # Opt-in enforcement. When disabled (default), the permission engine is
        # bypassed entirely — every request passes through. See
        # permission_enforcement_enabled() for the rationale (single-operator
        # onboarding must not be deny-alled by an absent operator matrix).
        if not permission_enforcement_enabled():
            return await call_next(request)

        if _is_readonly(method, path):
            return await call_next(request)

        # Routes outside the resource table are not gated by this
        # middleware (R7 — admission middleware already admitted; the
        # permission engine only fires on permission-relevant routes).
        resource = _resolve_resource(method, path)
        if resource is None:
            return await call_next(request)

        principal = from_admission_tree(request)
        enforcer = get_enforcer()
        resource_kind, resource_label, action = resource
        decision = enforcer.enforce(
            principal,
            resource_kind,
            resource_label,
            action,
        )
        if isinstance(decision, Allow):
            return await call_next(request)

        # Deny — emit structured log and 403.
        reason_code = decision.reason.code
        logger.info(
            "permissions.deny",
            path=path,
            method=method,
            resource_kind=resource_kind,
            resource_label=resource_label,
            action=action,
            reason=reason_code,
        )
        return JSONResponse(
            status_code=403,
            content={
                "detail": "permission denied",
                "reason": reason_code,
            },
        )


__all__ = [
    "PermissionMatrixMiddleware",
]
