"""Tests for `src.analytics.otel_setup`."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from opentelemetry import trace

from src.analytics import otel_setup


@pytest.fixture(autouse=True)
def _reset_initialized_flag():
    """Reset the module-level flag so each test calls setup fresh.

    The OTel global TracerProvider itself is locked after its first
    installation process-wide; that's fine because these tests assert
    behavioral properties (idempotency, resource attributes), not
    provider identity across tests.
    """
    otel_setup._initialized = False
    yield
    otel_setup._initialized = False


def test_setup_otel_runs_without_error():
    app = FastAPI()
    otel_setup.setup_otel(app)
    assert otel_setup._initialized is True


def test_setup_otel_is_idempotent():
    app = FastAPI()
    otel_setup.setup_otel(app)
    first_provider = trace.get_tracer_provider()

    otel_setup.setup_otel(app)
    second_provider = trace.get_tracer_provider()

    assert first_provider is second_provider


def test_setup_otel_resource_attributes_populated():
    app = FastAPI()
    otel_setup.setup_otel(app)

    tp = trace.get_tracer_provider()
    resource = getattr(tp, "resource", None)
    assert resource is not None, "expected SDK TracerProvider with a Resource"

    attrs = resource.attributes
    assert attrs.get("service.name") == "grace-api"
    version = attrs.get("service.version")
    assert isinstance(version, str) and version, "service.version must be non-empty"
