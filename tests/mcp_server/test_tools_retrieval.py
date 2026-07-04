"""Spec §11.2 item 6 — happy-path test for ``grace_search`` and
``grace_answer``.

Mocks ``httpx.AsyncClient`` and stubs ``socket.getaddrinfo`` to a
loopback tuple so the per-request airgap check passes. Asserts the
correct URL and JSON body were built for each upstream POST.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.mcp_server.conftest import build_async_client


@pytest.mark.asyncio
async def test_grace_search_and_grace_answer_happy_path(loopback_dns):
    # Import triggers tool registration.
    from src.mcp_server.tools_retrieval import grace_answer, grace_search

    # --- grace_search -----
    search_response = {
        "query": "ontology evolution",
        "results": [],
        "serialized_context": "",
        "serialization_format": "template",
        "total_candidates": 0,
        "strategy_contributions": {},
        "latency_ms": {},
    }
    client = build_async_client(status_code=200, json_body=search_response)
    with patch(
        "src.mcp_server.http_client.httpx.AsyncClient", return_value=client
    ):
        result = await grace_search(query="ontology evolution", limit=5)
    assert result == search_response
    client.request.assert_awaited_once()
    call_args = client.request.call_args
    assert call_args.args[0] == "POST"
    assert call_args.args[1] == "/api/retrieval/query"
    body = call_args.kwargs.get("json")
    assert body == {"query_text": "ontology evolution", "top_k": 5}

    # --- grace_answer -----
    answer_response = {
        "query": "what is graceful?",
        "response_text": "graceful is...",
        "claim_spans": [],
        "phase_state": "structure",
        "contributing_grace_ids": [],
        "strategy_contributions": {},
        "latency_ms": {},
        "token_usage": {},
        "model": "",
        "provider": "",
        "retrieval_mode": "single_round",
        "response_metadata": {
            "context_truncated": False,
            "span_detector_mode": "sentence_fallback",
            "phase_style_applied": "structure",
            "span_detection_note": None,
            "model_override_applied": False,
        },
    }
    client2 = build_async_client(status_code=200, json_body=answer_response)
    with patch(
        "src.mcp_server.http_client.httpx.AsyncClient", return_value=client2
    ):
        result = await grace_answer(
            query="what is graceful?", phase_state="structure"
        )
    assert result == answer_response
    body = client2.request.call_args.kwargs.get("json")
    assert body == {
        "query_text": "what is graceful?",
        "phase_state": "structure",
    }
