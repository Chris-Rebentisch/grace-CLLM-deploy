"""Tests for BaseConnector ABC and Pydantic models (CP1)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.connectors.base import BaseConnector
from src.connectors.models import (
    ConnectorConfig,
    ConnectorHealthStatus,
    ConnectorRecord,
    ConnectorRelationship,
    ResolvedEntity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config() -> ConnectorConfig:
    return ConnectorConfig(
        connector_type="test",
        namespace_id=uuid4(),
    )


class _CompleteConnector(BaseConnector):
    connector_type = "test"

    def discover_schema(self) -> dict:
        return {}

    def check_connectivity(self) -> bool:
        return True

    async def initial_load(self) -> AsyncIterator[ConnectorRecord]:
        return
        yield  # pragma: no cover — makes this an async generator

    async def incremental_sync(self, since: datetime) -> AsyncIterator[ConnectorRecord]:
        return
        yield  # pragma: no cover

    def health_check(self) -> ConnectorHealthStatus:
        return ConnectorHealthStatus(status="healthy", checked_at=datetime.now(UTC))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_base_connector_cannot_be_instantiated() -> None:
    """BaseConnector is abstract — direct instantiation must raise TypeError."""
    with pytest.raises(TypeError):
        BaseConnector(_make_config())  # type: ignore[abstract]


def test_subclass_missing_methods_raises() -> None:
    """A subclass that omits abstract methods raises TypeError on instantiation."""

    class _Incomplete(BaseConnector):
        connector_type = "incomplete"

    with pytest.raises(TypeError):
        _Incomplete(_make_config())  # type: ignore[abstract]


def test_complete_subclass_instantiates() -> None:
    """A complete subclass can be instantiated."""
    conn = _CompleteConnector(_make_config())
    assert conn.connector_type == "test"
    assert conn.check_connectivity() is True


def test_connector_type_required() -> None:
    """Omitting connector_type on a subclass raises TypeError at class definition."""
    with pytest.raises(TypeError, match="connector_type"):

        class _NoType(BaseConnector):
            connector_type = ""  # falsy → rejected by __init_subclass__

            def discover_schema(self) -> dict:
                return {}

            def check_connectivity(self) -> bool:
                return True

            async def initial_load(self):
                return
                yield

            async def incremental_sync(self, since):
                return
                yield

            def health_check(self):
                return ConnectorHealthStatus(status="healthy", checked_at=datetime.now(UTC))


def test_pydantic_models_roundtrip() -> None:
    """Pydantic models round-trip via model_validate/model_dump/model_json_schema."""
    now = datetime.now(UTC)
    record = ConnectorRecord(
        source_record_id="r1",
        entity_type="Legal_Entity",
        name="Acme Corp",
        source_system="SyntheticA",
        source_updated_at=now,
        relationships=[
            ConnectorRelationship(
                target_record_id="r2",
                relationship_type="Has_Subsidiary",
            )
        ],
    )
    dumped = record.model_dump()
    restored = ConnectorRecord.model_validate(dumped)
    assert restored.source_record_id == "r1"
    assert len(restored.relationships) == 1

    schema = ConnectorRecord.model_json_schema()
    assert "properties" in schema

    resolved = ResolvedEntity(outcome="bridged", grace_id=str(uuid4()))
    assert resolved.model_dump()["outcome"] == "bridged"
    ResolvedEntity.model_json_schema()


def test_health_status_rejects_invalid() -> None:
    """ConnectorHealthStatus rejects status values outside the ternary set."""
    with pytest.raises(Exception):
        ConnectorHealthStatus(status="unknown", checked_at=datetime.now(UTC))
