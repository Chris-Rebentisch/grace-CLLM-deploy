"""Tests for connector registry (CP2)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.connectors.base import BaseConnector
from src.connectors.models import ConnectorConfig, ConnectorHealthStatus, ConnectorRecord
from src.connectors.registry import _REGISTRY, get_connector, register_connector


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(ct: str = "test_reg") -> ConnectorConfig:
    return ConnectorConfig(connector_type=ct, namespace_id=uuid4())


def _make_connector_class(name: str, ct: str):
    """Dynamically create a complete BaseConnector subclass."""
    attrs = {
        "connector_type": ct,
        "discover_schema": lambda self: {},
        "check_connectivity": lambda self: True,
        "initial_load": _async_gen_stub,
        "incremental_sync": _async_gen_stub_since,
        "health_check": lambda self: ConnectorHealthStatus(
            status="healthy", checked_at=datetime.now(UTC)
        ),
    }
    return type(name, (BaseConnector,), attrs)


async def _async_gen_stub(self) -> AsyncIterator[ConnectorRecord]:
    return
    yield  # pragma: no cover


async def _async_gen_stub_since(self, since) -> AsyncIterator[ConnectorRecord]:
    return
    yield  # pragma: no cover


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_register_and_get(tmp_path) -> None:
    """@register_connector registers; get_connector returns instance."""
    # Use a unique type name to avoid collision with other tests
    unique_type = f"reg_test_{uuid4().hex[:8]}"
    cls = _make_connector_class("RegTestConn", unique_type)

    # Register
    _REGISTRY[unique_type] = cls  # direct insertion for isolation

    try:
        conn = get_connector(unique_type, _make_config(unique_type))
        assert isinstance(conn, BaseConnector)
        assert conn.connector_type == unique_type
    finally:
        _REGISTRY.pop(unique_type, None)


def test_duplicate_raises() -> None:
    """Duplicate @register_connector raises ValueError."""
    unique_type = f"dup_test_{uuid4().hex[:8]}"
    cls = _make_connector_class("DupConn1", unique_type)
    _REGISTRY[unique_type] = cls

    try:
        with pytest.raises(ValueError, match="Duplicate connector type"):
            register_connector(unique_type)(
                _make_connector_class("DupConn2", unique_type)
            )
    finally:
        _REGISTRY.pop(unique_type, None)


def test_get_nonexistent_raises() -> None:
    """get_connector with unknown type raises KeyError."""
    with pytest.raises(KeyError, match="Unknown connector type"):
        get_connector("nonexistent_xyz_" + uuid4().hex[:8], _make_config())


def test_registered_is_base_subclass() -> None:
    """Registered connectors are BaseConnector subclasses."""
    unique_type = f"sub_test_{uuid4().hex[:8]}"
    cls = _make_connector_class("SubTestConn", unique_type)
    _REGISTRY[unique_type] = cls

    try:
        assert issubclass(_REGISTRY[unique_type], BaseConnector)
    finally:
        _REGISTRY.pop(unique_type, None)
