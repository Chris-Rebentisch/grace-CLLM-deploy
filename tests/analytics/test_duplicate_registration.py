"""Guard test: reloading the metrics module must not raise.

If anyone ever replaces OTel Meter instruments with direct
`prometheus_client.Histogram(...)` / `Counter(...)` registrations,
`importlib.reload(src.analytics.metrics)` will raise
`ValueError: Duplicated timeseries in CollectorRegistry`. The OTel
Meter API has no such collision — this test is the canary.
"""

from __future__ import annotations

import importlib


def test_metrics_module_reload_does_not_raise():
    from src.analytics import metrics as metrics_mod

    reloaded = importlib.reload(metrics_mod)

    reloaded.llm_call_duration.record(1.0)
    reloaded.pipeline_stage_errors.add(1, attributes={"pipeline": "x", "stage": "y"})
