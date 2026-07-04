"""Shared fixtures for MCP server tests.

The MCP adapter mocks upstream HTTP via ``patch``ing
``src.mcp_server.http_client.httpx.AsyncClient``. The airgap check
runs before every call, so tests that exercise a tool's HTTP leg must
also stub ``socket.getaddrinfo`` to return a loopback address — the
``loopback_dns`` fixture below handles that.

Tests live in a package under ``tests/mcp_server/``. Running them via
``pytest tests/mcp_server`` or ``pytest tests`` from the repo root both
work; Python sees the package via the repo-root ``src`` / ``tests``
layout already established by existing suites.
"""

from __future__ import annotations

import socket
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def loopback_dns(monkeypatch):
    """Stub ``socket.getaddrinfo`` so :func:`_assert_loopback` passes.

    Returns a single IPv4 loopback tuple. The per-request loopback
    check then succeeds and the test proceeds to the httpx leg
    (which is also mocked).
    """

    def _fake(host: str, port: int, *args: Any, **kwargs: Any) -> list:
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                0,
                "",
                ("127.0.0.1", port),
            )
        ]

    monkeypatch.setattr(socket, "getaddrinfo", _fake)


def build_async_client(
    *,
    status_code: int = 200,
    json_body: Any = None,
    json_exception: Exception | None = None,
    request_exception: Exception | None = None,
) -> AsyncMock:
    """Build an ``httpx.AsyncClient`` mock suitable for ``async with``.

    Pass ``request_exception`` to simulate a transport failure
    (timeout, connect error). Pass ``json_exception`` to simulate a
    non-JSON response body. Otherwise ``status_code`` and ``json_body``
    shape the successful response.
    """
    response = MagicMock()
    response.status_code = status_code
    if json_exception is not None:
        response.json = MagicMock(side_effect=json_exception)
    else:
        response.json = MagicMock(return_value=json_body)

    client = AsyncMock()
    if request_exception is not None:
        client.request = AsyncMock(side_effect=request_exception)
    else:
        client.request = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client
