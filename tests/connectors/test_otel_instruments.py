"""OTel instrument registration contract tests for Chunk 53 connectors.

Verifies that the two Chunk 53 instruments are properly registered in
``src.analytics.metrics`` and that the helper functions are callable
without raising.
"""

from __future__ import annotations


def test_connector_sync_records_counter_registered() -> None:
    """grace_connector_sync_records_total is a registered Counter."""
    from src.analytics.metrics import grace_connector_sync_records_total

    assert grace_connector_sync_records_total is not None
    # Best-effort helper must not raise
    from src.analytics.metrics import record_connector_sync_record

    record_connector_sync_record(connector_type="synthetic", outcome="created")


def test_connector_sync_duration_histogram_registered() -> None:
    """grace_connector_sync_duration_seconds is a registered Histogram."""
    from src.analytics.metrics import grace_connector_sync_duration_seconds

    assert grace_connector_sync_duration_seconds is not None
    # Best-effort helper must not raise
    from src.analytics.metrics import record_connector_sync_duration

    record_connector_sync_duration(
        connector_type="synthetic", mode="initial", duration=1.23
    )
