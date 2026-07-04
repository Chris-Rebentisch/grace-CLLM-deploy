"""CP2 Prometheus reader tests using httpx.MockTransport."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from src.analytics.prometheus_reader import (
    PrometheusReader,
)
from tests.analytics.signal_pipeline.conftest import (
    make_prom_matrix,
    make_prom_vector,
)


@pytest.mark.asyncio
async def test_query_instant_parses_vector_envelope():
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["query"] = request.url.params.get("query") or ""
        return httpx.Response(
            200,
            json=make_prom_vector(
                [{"metric": {"ontology_module": "finance"}, "value": 0.42}]
            ),
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9090")
    reader = PrometheusReader(client=client)

    result = await reader.query_instant('rate(grace_signal_a_strength[5m])')
    assert captured["path"] == "/api/v1/query"
    assert "grace_signal_a_strength" in captured["query"]
    assert len(result.entries) == 1
    assert result.entries[0].metric["ontology_module"] == "finance"
    assert result.entries[0].value == pytest.approx(0.42)
    await client.aclose()


@pytest.mark.asyncio
async def test_query_range_parses_matrix_envelope():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=make_prom_matrix(
                [
                    {
                        "metric": {"signal": "A"},
                        "values": [
                            (1714_000_000.0, 0.1),
                            (1714_000_060.0, 0.2),
                        ],
                    }
                ]
            ),
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9090")
    reader = PrometheusReader(client=client)

    end = datetime.now(UTC)
    start = end - timedelta(minutes=10)
    result = await reader.query_range("up", start=start, end=end, step="60s")
    assert len(result.entries) == 1
    series = result.entries[0]
    assert series.metric["signal"] == "A"
    assert len(series.values) == 2
    assert series.values[0][1] == pytest.approx(0.1)
    await client.aclose()
