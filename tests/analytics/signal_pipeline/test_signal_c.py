"""Signal C detector tests (D241/D242/D245)."""

from __future__ import annotations

import httpx
import pytest

from src.analytics.signal_pipeline.config import SignalCConfig, SignalPipelineConfig
from src.analytics.signal_pipeline.signals.signal_c import SignalCDetector
from tests.analytics.signal_pipeline.conftest import make_prom_vector


@pytest.mark.asyncio
async def test_signal_c_fires_on_invalid_entity_type_spike(signal_run_context):
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params["query"]
        captured.append(q)
        if "[1d]" in q:
            payload = make_prom_vector([
                {
                    "metric": {
                        "ontology_module": "finance",
                        "entity_type": "BadType",
                    },
                    "value": 0.5,
                }
            ])
        else:
            payload = make_prom_vector([
                {
                    "metric": {
                        "ontology_module": "finance",
                        "entity_type": "BadType",
                    },
                    "value": 0.05,
                }
            ])
        return httpx.Response(200, json=payload)

    cfg = SignalPipelineConfig(
        signal_c=SignalCConfig(
            kind_filter=["invalid_entity_type", "schema_version_mismatch"]
        )
    )
    ctx = signal_run_context(prom_handler=handler, config=cfg)
    detector = SignalCDetector()
    records = await detector.detect(ctx)
    assert records, "expected a record"
    # PromQL must reference grace_extraction_validation_failures_total + kind regex.
    assert all("grace_extraction_validation_failures_total" in q for q in captured)
    assert all('kind=~"invalid_entity_type|schema_version_mismatch"' in q for q in captured)
    rec = records[0]
    assert rec.strength > 0.0
    assert rec.evidence_snapshot["kind_filter"] == [
        "invalid_entity_type",
        "schema_version_mismatch",
    ]


@pytest.mark.asyncio
async def test_signal_c_zero_below_sigma(signal_run_context):
    def handler(request: httpx.Request) -> httpx.Response:
        # Current barely above baseline; baseline_rate * sigma=3 dominates.
        if "[1d]" in request.url.params["query"]:
            payload = make_prom_vector([
                {
                    "metric": {"ontology_module": "finance", "entity_type": "T"},
                    "value": 0.06,
                }
            ])
        else:
            payload = make_prom_vector([
                {
                    "metric": {"ontology_module": "finance", "entity_type": "T"},
                    "value": 0.05,
                }
            ])
        return httpx.Response(200, json=payload)

    cfg = SignalPipelineConfig()
    ctx = signal_run_context(prom_handler=handler, config=cfg)
    detector = SignalCDetector()
    records = await detector.detect(ctx)
    assert records[0].strength == 0.0
