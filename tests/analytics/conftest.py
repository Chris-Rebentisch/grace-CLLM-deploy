"""Shared fixtures for analytics tests.

The OTel global TracerProvider is set process-wide on the first
`setup_otel` call. We add an `InMemorySpanExporter` once and clear it
between tests so assertions remain local to each test without
re-installing providers.
"""

from __future__ import annotations

# Must be set before any opentelemetry-instrumentation-* import so the
# library picks the stable HTTP semconv metric names (spec §3.1, D152).
import os

os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "http")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from opentelemetry import trace  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)

from src.analytics import otel_setup  # noqa: E402

_memory_exporter: InMemorySpanExporter = InMemorySpanExporter()
_exporter_installed: bool = False


@pytest.fixture
def span_exporter() -> InMemorySpanExporter:
    """Yield a clean InMemorySpanExporter attached to the global TracerProvider."""
    global _exporter_installed

    if not otel_setup._initialized:
        otel_setup.setup_otel(FastAPI())

    if not _exporter_installed:
        tp = trace.get_tracer_provider()
        tp.add_span_processor(SimpleSpanProcessor(_memory_exporter))
        _exporter_installed = True

    _memory_exporter.clear()
    yield _memory_exporter
    _memory_exporter.clear()
