"""Signal E detector tests (D241/D242/D245)."""

from __future__ import annotations

import httpx
import pytest

from src.analytics.signal_pipeline.config import SignalEConfig, SignalPipelineConfig
from src.analytics.signal_pipeline.signals.signal_e import SignalEDetector
from tests.analytics.signal_pipeline.conftest import make_prom_vector


def _spike_handler(spike_value: float = 0.5, baseline_value: float = 0.05):
    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params["query"]
        if "[1d]" in q:
            payload = make_prom_vector([
                {
                    "metric": {"ontology_module": "finance", "entity_type": "T"},
                    "value": spike_value,
                }
            ])
        else:
            payload = make_prom_vector([
                {
                    "metric": {"ontology_module": "finance", "entity_type": "T"},
                    "value": baseline_value,
                }
            ])
        return httpx.Response(200, json=payload)

    return handler


@pytest.mark.asyncio
async def test_signal_e_fires_on_domain_violation_spike(signal_run_context):
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.url.params["query"])
        return _spike_handler()(request)

    ctx = signal_run_context(prom_handler=handler, config=SignalPipelineConfig())
    detector = SignalEDetector()
    records = await detector.detect(ctx)
    assert records[0].strength > 0.0
    # Default kind_filter regex must include both rules.
    assert all('domain_violation' in q and 'range_violation' in q for q in captured)
    # Must NOT reference misplaced_property (FAIL gate #12 — operator-only).
    assert all('misplaced_property' not in q for q in captured)


@pytest.mark.asyncio
async def test_signal_e_zero_below_sigma(signal_run_context):
    ctx = signal_run_context(
        prom_handler=_spike_handler(spike_value=0.06, baseline_value=0.05),
        config=SignalPipelineConfig(),
    )
    detector = SignalEDetector()
    records = await detector.detect(ctx)
    assert records[0].strength == 0.0


@pytest.mark.asyncio
async def test_signal_e_kind_filter_override(signal_run_context):
    """Operator-narrowed kind_filter changes the PromQL regex."""
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params["query"]
        captured.append(q)
        return httpx.Response(200, json=make_prom_vector([]))

    cfg = SignalPipelineConfig(
        signal_e=SignalEConfig(kind_filter=["domain_violation"])
    )
    ctx = signal_run_context(prom_handler=handler, config=cfg)
    detector = SignalEDetector()
    await detector.detect(ctx)
    assert captured, "expected PromQL queries"
    for q in captured:
        assert 'kind=~"domain_violation"' in q
        assert 'range_violation' not in q
