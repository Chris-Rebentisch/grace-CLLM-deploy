"""Scope header middleware for Chunk 27 D194, extended in Chunk 29 D229.

Reads ``X-Graph-Scope`` on every request and logs the value via structlog.
Parses ``segments:m1,m2,...`` syntax against an in-memory ontology_module
allowlist. Rejects invalid segment names with HTTP 422.
Applies no filtering -- middleware logs only; downstream endpoints filter
via stored Zustand selection.

Allowed values: ``all``, ``segment:<name>``, ``segments:<name1>,<name2>,...``.
"""

from __future__ import annotations

import re
import time
from typing import Callable

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = structlog.get_logger()

# In-memory allowlist cache with TTL
_allowlist_cache: set[str] | None = None
_allowlist_cache_time: float = 0.0
_ALLOWLIST_TTL_SECONDS = 60.0

# Validation pattern: alphanumeric, underscore, hyphen only
_SEGMENT_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _refresh_allowlist() -> set[str]:
    """Refresh the ontology_module allowlist from DB on cache miss."""
    global _allowlist_cache, _allowlist_cache_time
    now = time.monotonic()
    if _allowlist_cache is not None and (now - _allowlist_cache_time) < _ALLOWLIST_TTL_SECONDS:
        return _allowlist_cache

    try:
        from src.shared.database import get_db
        gen = get_db()
        db = next(gen)
        try:
            from sqlalchemy import text
            result = db.execute(
                text("SELECT DISTINCT ontology_module FROM entities WHERE ontology_module IS NOT NULL")
            )
            modules = {row[0] for row in result if row[0]}
            modules.add("_unclassified")
            _allowlist_cache = modules
            _allowlist_cache_time = now
            return modules
        finally:
            try:
                next(gen)
            except StopIteration:
                pass
    except Exception:
        # If DB is unavailable, return empty set (no validation)
        return _allowlist_cache or set()


def _parse_scope(scope: str) -> tuple[str, list[str] | None, str | None]:
    """Parse scope header value.

    Returns (scope_type, segments, error_message).
    scope_type is one of: 'all', 'segment', 'segments'.
    """
    if scope == "all":
        return "all", None, None

    if scope.startswith("segment:") and not scope.startswith("segments:"):
        name = scope[8:]  # len("segment:") = 8
        if not name or not _SEGMENT_NAME_RE.match(name):
            return "segment", None, f"Invalid segment name: {name!r}"
        return "segment", [name], None

    if scope.startswith("segments:"):
        names_str = scope[9:]  # len("segments:") = 9
        if not names_str:
            return "segments", None, "Empty segments list"
        names = names_str.split(",")
        for name in names:
            name = name.strip()
            if not name or not _SEGMENT_NAME_RE.match(name):
                return "segments", None, f"Invalid segment name: {name!r}"
        return "segments", [n.strip() for n in names], None

    return "all", None, None


class GraphScopeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        scope = request.headers.get("x-graph-scope") or "all"

        scope_type, segments, error = _parse_scope(scope)

        if error:
            logger.error(
                "scope.invalid_segment",
                error_type="scope_invalid_segment",
                scope=scope,
                error=error,
                path=request.url.path,
                method=request.method,
            )
            return JSONResponse(
                status_code=422,
                content={"detail": error, "error_type": "scope_invalid_segment"},
            )

        # Validate segment names against allowlist if segments provided
        if segments and scope_type in ("segment", "segments"):
            allowlist = _refresh_allowlist()
            if allowlist:
                invalid = [s for s in segments if s not in allowlist]
                if invalid:
                    logger.error(
                        "scope.invalid_segment",
                        error_type="scope_invalid_segment",
                        scope=scope,
                        invalid_segments=invalid,
                        path=request.url.path,
                        method=request.method,
                    )
                    return JSONResponse(
                        status_code=422,
                        content={
                            "detail": f"Unknown segment(s): {', '.join(invalid)}",
                            "error_type": "scope_invalid_segment",
                        },
                    )

        logger.info(
            "scope.request_received",
            scope=scope,
            scope_type=scope_type,
            path=request.url.path,
            method=request.method,
        )
        response = await call_next(request)
        return response
