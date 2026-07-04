"""Refused-route registry for support sessions (D373).

BLOCKED_FROM_SUPPORT_SESSION_ROUTES is the single source of truth for
routes blocked under support sessions.  The @no_support_session decorator
provides belt-side enforcement at the route handler (suspenders side is
the middleware pre-check in auth_middleware.py step 5).
"""

from __future__ import annotations

import functools
import inspect
from typing import Callable

from starlette.requests import Request
from starlette.responses import JSONResponse


BLOCKED_FROM_SUPPORT_SESSION_ROUTES: frozenset[tuple[str, str]] = frozenset({
    ("POST", "/api/llm/config"),
    ("POST", "/api/llm/config/test"),
    ("POST", "/api/ontology/ratify"),
    ("POST", "/api/permissions/matrix/ratify"),
})


def no_support_session(method: str, path: str) -> Callable:
    """Belt-side decorator blocking support sessions from sensitive routes.

    Import-time: asserts ``(method, path)`` is in the frozenset — crash on
    registration if the tuple is not in the registry.

    Request-time: if ``request.state.support_session_id`` is truthy, returns
    403 JSON response. Otherwise calls the wrapped handler normally.

    The decorator injects a ``_nss_request: Request`` parameter into the
    wrapped function signature so FastAPI provides the Starlette Request
    automatically, even if the original handler does not declare one.
    """
    assert (method, path) in BLOCKED_FROM_SUPPORT_SESSION_ROUTES, (
        f"({method!r}, {path!r}) not in BLOCKED_FROM_SUPPORT_SESSION_ROUTES"
    )

    def decorator(func: Callable) -> Callable:
        is_async = inspect.iscoroutinefunction(func)

        @functools.wraps(func)
        async def wrapper(*args, _nss_request: Request, **kwargs):
            if getattr(_nss_request.state, "support_session_id", None):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "route blocked for support sessions"},
                )
            if is_async:
                return await func(*args, **kwargs)
            return func(*args, **kwargs)

        # Patch the wrapper's signature to include _nss_request so FastAPI
        # sees it and injects the Starlette Request automatically.
        orig_sig = inspect.signature(func)
        nss_param = inspect.Parameter(
            "_nss_request",
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Request,
        )
        new_params = list(orig_sig.parameters.values()) + [nss_param]
        wrapper.__signature__ = orig_sig.replace(parameters=new_params)

        return wrapper

    return decorator
