"""Signal A detector tests (D241/D245)."""

from __future__ import annotations

import httpx
import pytest

from src.analytics.signal_pipeline.config import SignalAConfig, SignalPipelineConfig
from src.analytics.signal_pipeline.signals.signal_a import SignalADetector
from tests.analytics.signal_pipeline.conftest import make_prom_vector


@pytest.mark.asyncio
async def test_signal_a_fires_on_rising_insufficient_rate(signal_run_context):
    captured_queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params["query"]
        captured_queries.append(q)
        # baseline window string is "14d" by default; current is "1d"
        if "[1d]" in q:
            payload = make_prom_vector(
                [{"metric": {"ontology_module": "finance"}, "value": 1.0}]
            )
        else:
            payload = make_prom_vector(
                [{"metric": {"ontology_module": "finance"}, "value": 0.001}]
            )
        return httpx.Response(200, json=payload)

    cfg = SignalPipelineConfig(
        signal_a=SignalAConfig(
            sigma_multiplier=3.0,
            baseline_window_days=14,
            current_window_days=1,
        )
    )
    ctx = signal_run_context(prom_handler=handler, config=cfg)
    detector = SignalADetector()
    records = await detector.detect(ctx)

    # Mandatory: every PromQL must include verdict="INSUFFICIENT" (FAIL gate #13).
    assert captured_queries, "no PromQL queries issued"
    for q in captured_queries:
        assert 'verdict="INSUFFICIENT"' in q, q

    assert len(records) == 1
    r = records[0]
    assert r.signal_type == "A"
    assert r.ontology_module == "finance"
    # baseline 0.001/s × 14d ≈ 1209.6 samples — > 100 → strength fires.
    assert r.strength > 0.0


@pytest.mark.asyncio
async def test_signal_a_emits_zero_when_baseline_insufficient(signal_run_context):
    """Baseline below 100 samples → emit 0 with insufficient-samples note."""

    def handler(request: httpx.Request) -> httpx.Response:
        # baseline 0/s → 0 samples; current is irrelevant.
        if "[1d]" in request.url.params["query"]:
            payload = make_prom_vector(
                [{"metric": {"ontology_module": "finance"}, "value": 0.5}]
            )
        else:
            payload = make_prom_vector(
                [{"metric": {"ontology_module": "finance"}, "value": 0.0}]
            )
        return httpx.Response(200, json=payload)

    cfg = SignalPipelineConfig()
    ctx = signal_run_context(prom_handler=handler, config=cfg)
    detector = SignalADetector()
    records = await detector.detect(ctx)

    assert len(records) == 1
    r = records[0]
    assert r.strength == 0.0
    assert r.evidence_snapshot.get("note") == "insufficient samples"
