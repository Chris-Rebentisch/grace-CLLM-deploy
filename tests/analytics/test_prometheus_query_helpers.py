"""CP2 — Unit tests for query_with_coldstart_hint (D459)."""

import pytest
import structlog

from src.analytics._prometheus_query_helpers import query_with_coldstart_hint


def test_hint_fires_on_14d_empty_result():
    """Empty result + [14d] query -> INFO log emitted."""
    cap = structlog.testing.CapturingLogger()
    old_config = structlog.get_config()
    structlog.configure(
        processors=[],
        wrapper_class=structlog.BoundLogger,
        logger_factory=lambda *a, **kw: cap,
        cache_logger_on_first_use=False,
    )
    try:
        result = query_with_coldstart_hint(
            'sum(rate(some_metric_total[14d]))', []
        )
        assert result == []
        assert any(
            "cold-start" in str(entry)
            for entry in cap.calls
        ), f"Expected cold-start log, got {cap.calls}"
    finally:
        structlog.configure(**old_config)


@pytest.mark.parametrize("window", ["30d", "60d", "90d"])
def test_hint_fires_on_30d_60d_90d_empty_result(window):
    """Empty result + long window -> INFO log emitted."""
    cap = structlog.testing.CapturingLogger()
    old_config = structlog.get_config()
    structlog.configure(
        processors=[],
        wrapper_class=structlog.BoundLogger,
        logger_factory=lambda *a, **kw: cap,
        cache_logger_on_first_use=False,
    )
    try:
        query_with_coldstart_hint(
            f'sum(rate(some_metric_total[{window}]))', []
        )
        assert any(
            "cold-start" in str(entry)
            for entry in cap.calls
        ), f"Expected cold-start log for [{window}], got {cap.calls}"
    finally:
        structlog.configure(**old_config)


def test_hint_fires_on_2w_empty_result():
    """Empty result + [2w] query -> INFO log emitted (2 weeks = 14 days, threshold boundary)."""
    cap = structlog.testing.CapturingLogger()
    old_config = structlog.get_config()
    structlog.configure(
        processors=[],
        wrapper_class=structlog.BoundLogger,
        logger_factory=lambda *a, **kw: cap,
        cache_logger_on_first_use=False,
    )
    try:
        query_with_coldstart_hint(
            'sum(rate(some_metric_total[2w]))', []
        )
        assert any(
            "cold-start" in str(entry)
            for entry in cap.calls
        ), f"Expected cold-start log for [2w], got {cap.calls}"
    finally:
        structlog.configure(**old_config)


def test_no_hint_on_7d_or_shorter():
    """Empty result + short windows -> no log."""
    for window in ["7d", "1h", "5m"]:
        cap = structlog.testing.CapturingLogger()
        old_config = structlog.get_config()
        structlog.configure(
            processors=[],
            wrapper_class=structlog.BoundLogger,
            logger_factory=lambda *a, **kw: cap,
            cache_logger_on_first_use=False,
        )
        try:
            query_with_coldstart_hint(
                f'sum(rate(some_metric_total[{window}]))', []
            )
            assert not any(
                "cold-start" in str(entry)
                for entry in cap.calls
            ), f"Unexpected cold-start log for [{window}]: {cap.calls}"
        finally:
            structlog.configure(**old_config)


def test_no_hint_on_nonempty_result():
    """Non-empty result + [14d] -> no log (passthrough)."""
    cap = structlog.testing.CapturingLogger()
    old_config = structlog.get_config()
    structlog.configure(
        processors=[],
        wrapper_class=structlog.BoundLogger,
        logger_factory=lambda *a, **kw: cap,
        cache_logger_on_first_use=False,
    )
    try:
        sentinel = [{"metric": {}, "value": 42}]
        result = query_with_coldstart_hint(
            'sum(rate(some_metric_total[14d]))', sentinel
        )
        assert result is sentinel
        assert not any(
            "cold-start" in str(entry)
            for entry in cap.calls
        ), f"Unexpected cold-start log on non-empty result: {cap.calls}"
    finally:
        structlog.configure(**old_config)
