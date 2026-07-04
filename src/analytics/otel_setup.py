"""OpenTelemetry bootstrap for the GrACE FastAPI app.

Configures a TracerProvider (ConsoleSpanExporter by default) and a
MeterProvider (PrometheusMetricReader) and installs FastAPI auto-
instrumentation. Idempotent: safe to call twice.

The `/metrics` endpoint mount is intentionally NOT done here (D155);
`src/api/main.py` owns that to keep a single source of truth.
"""

from __future__ import annotations

import os
import sys

import prometheus_client
import structlog
from fastapi import FastAPI
from opentelemetry import metrics as otel_metrics
from opentelemetry import trace
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

logger = structlog.get_logger(__name__)

_initialized: bool = False


def _unregister_default_collectors() -> None:
    """Remove prometheus_client's default process/platform/GC collectors (D154).

    Without this, the `/metrics` output contains platform-variant
    `python_*` and `process_*` names that the metric contract test
    would have to carry in its ignore-list. Guarded with KeyError so a
    second call (idempotency) does not raise.
    """
    for collector in (
        prometheus_client.PROCESS_COLLECTOR,
        prometheus_client.PLATFORM_COLLECTOR,
        prometheus_client.GC_COLLECTOR,
    ):
        try:
            prometheus_client.REGISTRY.unregister(collector)
        except KeyError:
            pass


def setup_otel(app: FastAPI) -> None:
    """Initialize OpenTelemetry providers and instrument the FastAPI app.

    Idempotent: second invocation logs and returns without re-registering.
    """
    global _initialized
    if _initialized:
        logger.info("otel.setup.skipped_already_initialized")
        return

    _unregister_default_collectors()

    service_name = os.environ.get("OTEL_SERVICE_NAME", "grace-api")
    service_version = os.environ.get(
        "OTEL_SERVICE_VERSION", "0.1.0-phase5-chunk24"
    )
    environment = os.environ.get("OTEL_ENVIRONMENT", "dev")
    traces_exporter = os.environ.get("OTEL_TRACES_EXPORTER", "console")

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": service_version,
            "deployment.environment": environment,
        }
    )

    tracer_provider = TracerProvider(resource=resource)
    if traces_exporter == "console":
        # D487 — GRACE_PYTEST_MODE guard (Chunk 75a).
        # Invariant: BatchSpanProcessor(ConsoleSpanExporter()) is the production
        # trace-export path. Carve-out: skip wiring under pytest to eliminate
        # ~8–10 `ValueError: I/O operation on closed file` teardown failures
        # (opentelemetry/sdk/trace/export/__init__.py:313) that persisted
        # through chunks 65–72b. Authorization: D487 / spec §6 Step 1.
        pytest_mode = os.environ.get("GRACE_PYTEST_MODE") == "1" or "pytest" in sys.modules
        if not pytest_mode:
            tracer_provider.add_span_processor(
                BatchSpanProcessor(ConsoleSpanExporter())
            )
    trace.set_tracer_provider(tracer_provider)

    reader = PrometheusMetricReader()
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    otel_metrics.set_meter_provider(meter_provider)

    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
    )

    # Function-local import: metrics.py imports the MeterProvider we
    # just installed, so it has to run after set_meter_provider.
    from src.analytics import metrics as grace_metrics

    grace_metrics.initialize_placeholder_metrics()

    _initialized = True
    logger.info(
        "otel.setup.complete",
        service_name=service_name,
        service_version=service_version,
        environment=environment,
        traces_exporter=traces_exporter,
    )
