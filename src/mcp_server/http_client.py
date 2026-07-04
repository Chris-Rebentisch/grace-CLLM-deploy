"""Airgap-enforced async HTTP client for upstream FastAPI calls.

Every MCP tool that makes an HTTP call goes through :func:`call`. It
re-checks the allowlist (D186, spec §6.4) and the loopback resolution
(D183, spec §5.4) before every outbound request.

The per-request loopback check is the DNS rebinding defense. Caching
its result would eliminate the defense, so it is deliberately never
cached. The hostname and port are parsed from ``MCP_GRACE_BASE_URL``
at module load and stashed in module-level constants — tools cannot
pass an attacker-controlled hostname to :func:`_assert_loopback`.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

from src.mcp_server.errors import (
    MCPAirgapViolation,
    MCPErrorEnvelope,
    MCPReadOnlyViolation,
)
from src.mcp_server.server import READONLY_ROUTES, WRITABLE_REVIEW_ROUTES


_LOOPBACK_LITERALS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})


def _literal_hostname_check(hostname: str | None) -> None:
    """Raise ``MCPAirgapViolation`` if the hostname is not a loopback literal.

    This is a pure string check — no DNS. Used at module load for a
    fast fail on obvious misconfiguration. The :func:`_assert_loopback`
    check performs the DNS-rebinding-defeating resolution step.
    """
    if hostname not in _LOOPBACK_LITERALS:
        raise MCPAirgapViolation(
            f"MCP_GRACE_BASE_URL hostname {hostname!r} not in "
            f"loopback allowlist {sorted(_LOOPBACK_LITERALS)}"
        )


def _parse_base_url(url: str) -> tuple[str, int, str]:
    """Parse ``MCP_GRACE_BASE_URL`` into ``(hostname, port, base_url)``.

    Intentionally does *not* run the literal hostname check here —
    that lives in :func:`src.mcp_server.cli._startup_validate` (spec
    §5.3) so a bad env var surfaces a clean ``MCPAirgapViolation``
    from the startup path, not a late import-time error. The
    per-request :func:`_assert_loopback` check (spec §5.4) is the
    DNS-rebinding defense and is mandatory.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 8000)
    return hostname, port, url


_BASE_URL: str = os.environ.get("MCP_GRACE_BASE_URL", "http://127.0.0.1:8000")
_HOST, _PORT, _BASE_URL = _parse_base_url(_BASE_URL)
_TIMEOUT: float = float(os.environ.get("MCP_TIMEOUT_SECONDS", "30"))


async def _assert_loopback(hostname: str, port: int) -> None:
    """Resolve ``hostname`` and raise ``MCPAirgapViolation`` on any
    non-loopback address, resolution failure, or empty result (D183,
    spec §5.4). Per-request — never cached."""
    try:
        infos = socket.getaddrinfo(
            hostname, port, proto=socket.IPPROTO_TCP
        )
    except (socket.gaierror, OSError) as exc:
        raise MCPAirgapViolation(
            f"DNS resolution failed for {hostname}"
        ) from exc
    if not infos:
        raise MCPAirgapViolation(
            f"No addresses returned for {hostname}"
        )
    for _family, _type, _proto, _canon, sockaddr in infos:
        addr = sockaddr[0]
        if not ipaddress.ip_address(addr).is_loopback:
            raise MCPAirgapViolation(
                f"Non-loopback address {addr} for {hostname}"
            )


def _envelope(
    tool: str,
    code: str,
    message: str,
    status: int | None = None,
    details: dict | None = None,
) -> dict:
    """Construct a Layer B error envelope as a plain dict (spec §7.4)."""
    return MCPErrorEnvelope(
        code=code,
        message=message,
        tool=tool,
        status=status,
        details=details,
    ).model_dump()


async def call(
    method: str,
    path: str,
    *,
    tool: str,
    path_params: dict[str, Any] | None = None,
    query_params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> dict:
    """Execute one allowlisted, loopback-checked upstream HTTP call.

    Returns either the upstream JSON body as a dict (success) or a
    Layer B error envelope dict (failure). Tools never see raw
    ``httpx`` exceptions.

    Enforcement order:

    1. ``(method, path) in READONLY_ROUTES`` — call-time check (D186).
    2. ``_assert_loopback(_HOST, _PORT)`` — DNS-rebinding defense.
    3. Realize the templated path with ``path_params``.
    4. Issue the request; map exceptions/non-2xx to envelopes per
       spec §7.5.
    """
    try:
        # Three-way gate (D363): verb bypass for GET/HEAD/OPTIONS mirrors
        # auth middleware step-2 (auth_middleware.py:97-99); read-only POST
        # routes pass via READONLY_ROUTES; write-tool POST routes pass via
        # WRITABLE_REVIEW_ROUTES; everything else is refused.
        if method not in {"GET", "HEAD", "OPTIONS"} and (
            (method, path) not in READONLY_ROUTES
            and (method, path) not in WRITABLE_REVIEW_ROUTES
        ):
            raise MCPReadOnlyViolation(
                f"({method}, {path}) is not in READONLY_ROUTES or "
                "WRITABLE_REVIEW_ROUTES and is not a read-only verb — "
                "upstream call refused"
            )
        await _assert_loopback(_HOST, _PORT)
    except MCPReadOnlyViolation as exc:
        return _envelope(
            tool=tool,
            code="READONLY_VIOLATION",
            message=str(exc),
        )
    except MCPAirgapViolation as exc:
        return _envelope(
            tool=tool,
            code="AIRGAP_VIOLATION",
            message=str(exc),
        )

    realized_path = path.format(**(path_params or {}))

    filtered_query: dict[str, Any] | None
    if query_params is None:
        filtered_query = None
    else:
        filtered_query = {k: v for k, v in query_params.items() if v is not None}
        if not filtered_query:
            filtered_query = None

    try:
        async with httpx.AsyncClient(
            base_url=_BASE_URL, timeout=_TIMEOUT
        ) as client:
            response = await client.request(
                method,
                realized_path,
                params=filtered_query,
                json=json_body,
            )
    except (httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
        return _envelope(
            tool=tool,
            code="UPSTREAM_TIMEOUT",
            message=f"Upstream request timed out after {_TIMEOUT}s: {exc}",
            status=None,
        )
    except (httpx.ConnectError, httpx.NetworkError) as exc:
        return _envelope(
            tool=tool,
            code="UPSTREAM_UNAVAILABLE",
            message=f"Upstream connection failed: {exc}",
            status=None,
        )
    status = response.status_code

    if status == 404:
        return _envelope(
            tool=tool,
            code="UPSTREAM_NOT_FOUND",
            message=f"Upstream returned 404 for {method} {realized_path}",
            status=404,
        )

    if status >= 500:
        return _envelope(
            tool=tool,
            code="UPSTREAM_UNAVAILABLE",
            message=f"Upstream returned {status} for {method} {realized_path}",
            status=status,
        )

    try:
        return response.json()
    except ValueError:
        return _envelope(
            tool=tool,
            code="UPSTREAM_UNAVAILABLE",
            message=(
                f"Upstream returned {status} with a non-JSON body for "
                f"{method} {realized_path}"
            ),
            status=status,
        )
