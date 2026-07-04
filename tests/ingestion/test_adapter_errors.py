"""Tests for AdapterError hierarchy and lifecycle enums (Chunk 57, CP5)."""

from __future__ import annotations

from src.ingestion.adapter_base import (
    AdapterAuthError,
    AdapterCursorExpiredError,
    AdapterError,
    AdapterFatalError,
    AdapterRateLimitError,
    AdapterTransientError,
)
from src.ingestion.models import IngestionRunStatus, IngestionSourceStatus


def test_adapter_error_base():
    """AdapterError base carries error_class."""
    err = AdapterError("test", error_class="custom")
    assert err.error_class == "custom"
    assert str(err) == "test"


def test_adapter_auth_error_default():
    """AdapterAuthError defaults to 'auth_invalid'."""
    err = AdapterAuthError("bad creds")
    assert err.error_class == "auth_invalid"


def test_adapter_auth_error_oauth_refresh():
    """AdapterAuthError with oauth_refresh_failed."""
    err = AdapterAuthError("refresh failed", error_class="oauth_refresh_failed")
    assert err.error_class == "oauth_refresh_failed"


def test_adapter_rate_limit_error():
    """AdapterRateLimitError has retry_after_seconds and fixed error_class."""
    err = AdapterRateLimitError("429", retry_after_seconds=30.0)
    assert err.error_class == "rate_limited"
    assert err.retry_after_seconds == 30.0


def test_adapter_cursor_expired_error():
    """AdapterCursorExpiredError has fixed error_class."""
    err = AdapterCursorExpiredError("410")
    assert err.error_class == "cursor_expired"


def test_adapter_transient_error():
    """AdapterTransientError with constructor-argument error_class."""
    err1 = AdapterTransientError("conn", error_class="connection_error")
    assert err1.error_class == "connection_error"
    err2 = AdapterTransientError("parse", error_class="parse_error")
    assert err2.error_class == "parse_error"


def test_adapter_fatal_error():
    """AdapterFatalError has fixed error_class 'unknown'."""
    err = AdapterFatalError("boom")
    assert err.error_class == "unknown"


def test_ingestion_source_status_members():
    """IngestionSourceStatus has 4 members."""
    assert len(IngestionSourceStatus) == 4
    expected = {"pending", "ready", "error", "disabled"}
    assert {s.value for s in IngestionSourceStatus} == expected


def test_ingestion_run_status_paused():
    """IngestionRunStatus.paused exists (widened 4→5)."""
    assert IngestionRunStatus.paused.value == "paused"
    assert len(IngestionRunStatus) == 5
