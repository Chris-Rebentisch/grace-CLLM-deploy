"""Detector prerequisites-visibility tests (C1 follow-up).

Signals A, C, E previously no-op'd SILENTLY when Prometheus history/baseline
was absent — indistinguishable from "ran, found nothing". They must now emit a
``signal_detector_prerequisites_not_met`` structlog warning and record the
no-op in ``run_context.diagnostics`` (surfaced in the CLI summary). Detection
logic is unchanged.

No Postgres needed: the no-op path returns [] before any session use, so the
context is built directly with a mock session factory.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import httpx
import pytest
import structlog.testing

from src.analytics.prometheus_reader import PrometheusReader
from src.analytics.signal_pipeline.base import SignalRunContext
from src.analytics.signal_pipeline.config import SignalPipelineConfig
from src.analytics.signal_pipeline.signals.signal_a import SignalADetector
from src.analytics.signal_pipeline.signals.signal_c import SignalCDetector
from src.analytics.signal_pipeline.signals.signal_e import SignalEDetector
from tests.analytics.signal_pipeline.conftest import make_prom_vector


def _make_context(handler) -> SignalRunContext:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9090")
    return SignalRunContext(
        run_id=uuid4(),
        started_at=datetime.now(UTC),
        prometheus_reader=PrometheusReader(client=client),
        session_factory=MagicMock(),
        config=SignalPipelineConfig(),
        target_ontology_modules=None,
    )


def _empty_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=make_prom_vector([]))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "detector_cls, signal_type",
    [(SignalADetector, "A"), (SignalCDetector, "C"), (SignalEDetector, "E")],
)
async def test_detector_noop_on_missing_history_is_visible(detector_cls, signal_type):
    """Empty Prometheus (no current-window data, no baseline) → [] records, but the
    no-op is logged and recorded in diagnostics — NOT silent."""
    ctx = _make_context(_empty_handler)

    with structlog.testing.capture_logs() as logs:
        records = await detector_cls().detect(ctx)

    assert records == []
    events = [e for e in logs if e["event"] == "signal_detector_prerequisites_not_met"]
    assert events, f"detector {signal_type} must log its prerequisites no-op"
    assert events[0]["detector"] == signal_type
    assert "prometheus_current_window_data" in events[0]["missing"]
    assert "prometheus_baseline" in events[0]["missing"]

    noop = ctx.diagnostics.get("prerequisites_not_met", {})
    assert signal_type in noop
    assert "prometheus_current_window_data" in noop[signal_type]


@pytest.mark.asyncio
async def test_detector_with_data_does_not_flag_prerequisites():
    """When current-window data exists the detector runs normally (records emitted,
    no prerequisites_not_met entry) — 'ran, found nothing/something' stays distinct
    from 'prerequisites not met'."""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = make_prom_vector(
            [{"metric": {"ontology_module": "finance", "entity_type": "X"}, "value": 0.5}]
        )
        return httpx.Response(200, json=payload)

    ctx = _make_context(handler)
    records = await SignalCDetector().detect(ctx)
    assert len(records) == 1
    assert "prerequisites_not_met" not in ctx.diagnostics


@pytest.mark.asyncio
async def test_detector_baseline_absent_logs_but_still_runs():
    """Current data present, baseline cold → detector still emits records (logic
    unchanged) and logs the baseline absence at info level."""

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params["query"]
        if "[1d]" in q:  # current window (default current_window_days=1)
            payload = make_prom_vector(
                [{"metric": {"ontology_module": "finance", "entity_type": "X"}, "value": 0.5}]
            )
        else:
            payload = make_prom_vector([])
        return httpx.Response(200, json=payload)

    ctx = _make_context(handler)
    with structlog.testing.capture_logs() as logs:
        records = await SignalEDetector().detect(ctx)

    assert len(records) == 1
    assert any(e["event"] == "signal_detector_baseline_absent" for e in logs)
    assert "prerequisites_not_met" not in ctx.diagnostics
