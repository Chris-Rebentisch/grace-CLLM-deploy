"""D487 — OTel GRACE_PYTEST_MODE guard tests (Chunk 75a).

Verifies that the BatchSpanProcessor(ConsoleSpanExporter()) wiring in
src/analytics/otel_setup.py is correctly gated by GRACE_PYTEST_MODE
and the presence of 'pytest' in sys.modules.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest


def _reset_otel_setup():
    """Reset the otel_setup module's _initialized flag for re-entrant testing."""
    from src.analytics import otel_setup
    otel_setup._initialized = False


def _has_console_batch_processor(tracer_provider) -> bool:
    """Check if a TracerProvider has a BatchSpanProcessor wrapping ConsoleSpanExporter."""
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    processor = tracer_provider._active_span_processor
    # The TracerProvider uses a _MultiSpanProcessor that wraps child processors.
    children = getattr(processor, "_span_processors", [])
    for child in children:
        if isinstance(child, BatchSpanProcessor):
            exporter = getattr(child, "span_exporter", None)
            if isinstance(exporter, ConsoleSpanExporter):
                return True
    return False


class TestOtelPytestMode:
    """Tests for D487 GRACE_PYTEST_MODE guard in otel_setup.py."""

    def setup_method(self):
        _reset_otel_setup()

    def teardown_method(self):
        _reset_otel_setup()

    def test_pytest_mode_set_skips_exporter(self):
        """With GRACE_PYTEST_MODE=1, ConsoleSpanExporter is NOT wired."""
        from opentelemetry.sdk.trace import TracerProvider

        env = {
            "GRACE_PYTEST_MODE": "1",
            "OTEL_TRACES_EXPORTER": "console",
        }
        app = MagicMock()

        with patch.dict(os.environ, env, clear=False):
            with patch("src.analytics.otel_setup.FastAPIInstrumentor") as mock_instr:
                mock_instr.instrument_app = MagicMock()
                with patch("src.analytics.otel_setup.trace") as mock_trace:
                    providers = []

                    def capture_provider(tp):
                        providers.append(tp)

                    mock_trace.set_tracer_provider = capture_provider
                    with patch("src.analytics.otel_setup.otel_metrics"):
                        with patch("src.analytics.otel_setup.PrometheusMetricReader"):
                            with patch("src.analytics.otel_setup.MeterProvider"):
                                from src.analytics.otel_setup import setup_otel
                                setup_otel(app)

                    assert len(providers) == 1
                    tp = providers[0]
                    assert not _has_console_batch_processor(tp)

    def test_pytest_in_sys_modules_skips_exporter(self):
        """With GRACE_PYTEST_MODE unset but 'pytest' in sys.modules, exporter is skipped."""
        env = {
            "OTEL_TRACES_EXPORTER": "console",
        }
        # Ensure GRACE_PYTEST_MODE is not set
        env_clear = {k: v for k, v in os.environ.items() if k != "GRACE_PYTEST_MODE"}
        env_clear.update(env)

        app = MagicMock()
        # pytest is always in sys.modules during test execution
        assert "pytest" in sys.modules

        with patch.dict(os.environ, env_clear, clear=True):
            with patch("src.analytics.otel_setup.FastAPIInstrumentor") as mock_instr:
                mock_instr.instrument_app = MagicMock()
                with patch("src.analytics.otel_setup.trace") as mock_trace:
                    providers = []

                    def capture_provider(tp):
                        providers.append(tp)

                    mock_trace.set_tracer_provider = capture_provider
                    with patch("src.analytics.otel_setup.otel_metrics"):
                        with patch("src.analytics.otel_setup.PrometheusMetricReader"):
                            with patch("src.analytics.otel_setup.MeterProvider"):
                                from src.analytics.otel_setup import setup_otel
                                setup_otel(app)

                    assert len(providers) == 1
                    tp = providers[0]
                    assert not _has_console_batch_processor(tp)

    def test_production_mode_wires_exporter(self):
        """With GRACE_PYTEST_MODE unset AND 'pytest' NOT in sys.modules, exporter IS wired."""
        env = {
            "OTEL_TRACES_EXPORTER": "console",
        }
        env_clear = {k: v for k, v in os.environ.items() if k != "GRACE_PYTEST_MODE"}
        env_clear.update(env)

        app = MagicMock()

        # Temporarily remove pytest from sys.modules
        saved_pytest = sys.modules.pop("pytest", None)
        # Also remove _pytest to be thorough
        saved_modules = {}
        for key in list(sys.modules):
            if key == "pytest" or key.startswith("_pytest"):
                saved_modules[key] = sys.modules.pop(key)

        try:
            with patch.dict(os.environ, env_clear, clear=True):
                with patch("src.analytics.otel_setup.FastAPIInstrumentor") as mock_instr:
                    mock_instr.instrument_app = MagicMock()
                    with patch("src.analytics.otel_setup.trace") as mock_trace:
                        providers = []

                        def capture_provider(tp):
                            providers.append(tp)

                        mock_trace.set_tracer_provider = capture_provider
                        with patch("src.analytics.otel_setup.otel_metrics"):
                            with patch("src.analytics.otel_setup.PrometheusMetricReader"):
                                with patch("src.analytics.otel_setup.MeterProvider"):
                                    from src.analytics.otel_setup import setup_otel
                                    setup_otel(app)

                        assert len(providers) == 1
                        tp = providers[0]
                        assert _has_console_batch_processor(tp)
        finally:
            # Restore pytest modules
            if saved_pytest is not None:
                sys.modules["pytest"] = saved_pytest
            sys.modules.update(saved_modules)
