"""Spec §11.2 item 9 — happy-path test for the three discovery tools.

* ``grace_list_cqs`` (with and without filters)
* ``grace_cq_summary``
* ``grace_ollama_health``

Filters set to ``None`` are dropped before the upstream call so the
URL never contains ``?status=None``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.mcp_server.conftest import build_async_client


@pytest.mark.asyncio
async def test_discovery_tools_happy_path(loopback_dns):
    from src.mcp_server.tools_discovery import (
        grace_cq_summary,
        grace_list_cqs,
        grace_ollama_health,
    )

    # --- grace_list_cqs no filters -----
    cqs: list[dict] = []
    client = build_async_client(status_code=200, json_body=cqs)
    with patch(
        "src.mcp_server.http_client.httpx.AsyncClient", return_value=client
    ):
        result = await grace_list_cqs()
    assert result == cqs
    call = client.request.call_args
    assert call.args == ("GET", "/api/discovery/cqs")
    assert call.kwargs.get("params") is None

    # --- grace_list_cqs with filters -----
    client2 = build_async_client(status_code=200, json_body=cqs)
    with patch(
        "src.mcp_server.http_client.httpx.AsyncClient", return_value=client2
    ):
        result = await grace_list_cqs(
            status="validated", domain="corporate"
        )
    call = client2.request.call_args
    params = call.kwargs.get("params")
    assert params == {"status": "validated", "domain": "corporate"}

    # --- grace_cq_summary -----
    summary = {"total": 10, "by_status": {"validated": 10}}
    client3 = build_async_client(status_code=200, json_body=summary)
    with patch(
        "src.mcp_server.http_client.httpx.AsyncClient", return_value=client3
    ):
        result = await grace_cq_summary()
    assert result == summary
    assert client3.request.call_args.args == (
        "GET",
        "/api/discovery/cqs/summary",
    )

    # --- grace_ollama_health -----
    ollama = {"status": "ok", "models": ["qwen2.5:7b"]}
    client4 = build_async_client(status_code=200, json_body=ollama)
    with patch(
        "src.mcp_server.http_client.httpx.AsyncClient", return_value=client4
    ):
        result = await grace_ollama_health()
    assert result == ollama
    assert client4.request.call_args.args == (
        "GET",
        "/api/discovery/ollama-health",
    )
