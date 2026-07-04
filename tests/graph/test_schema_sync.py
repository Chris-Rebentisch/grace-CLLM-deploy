"""Tests for schema sync orchestration (mocked ArcadeDB + mocked PostgreSQL)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.graph.schema_sync import get_sync_status, preview_sync, sync_schema_to_graph
from src.graph.schema_sync_models import GraphSchemaSyncRecord


def _make_active_version(schema_json=None):
    """Create a mock active ontology version."""
    version = MagicMock()
    version.id = uuid4()
    version.version_number = 3
    version.schema_json = schema_json or {
        "entity_types": {
            "Person": {"properties": {"name": {"data_type": "string"}}},
        },
        "relationships": {
            "knows": {
                "source_type": "Person",
                "target_type": "Person",
                "properties": {},
            },
        },
    }
    return version


@patch("src.graph.schema_sync.create_sync_record")
@patch("src.graph.schema_sync.get_sync_by_version", return_value=None)
@patch("src.graph.schema_sync.get_active_version")
@pytest.mark.asyncio
async def test_sync_success(mock_get_active, mock_get_sync, mock_create_record):
    """All statements execute successfully, record created with status=success."""
    active = _make_active_version()
    mock_get_active.return_value = active
    mock_create_record.side_effect = lambda db, record: record

    client = AsyncMock()
    client.config = MagicMock()
    client.config.database = "grace"
    client.execute_sql = AsyncMock(return_value={"result": []})

    db = MagicMock()
    result = await sync_schema_to_graph(db, client)

    assert result.status == "success"
    assert result.failed == 0
    assert result.succeeded > 0
    assert result.total_statements == result.succeeded
    assert result.ontology_version_number == 3


@patch("src.graph.schema_sync.create_sync_record")
@patch("src.graph.schema_sync.get_sync_by_version", return_value=None)
@patch("src.graph.schema_sync.get_active_version")
@pytest.mark.asyncio
async def test_sync_partial_failure(mock_get_active, mock_get_sync, mock_create_record):
    """Some DDL fails, status=partial."""
    active = _make_active_version()
    mock_get_active.return_value = active
    mock_create_record.side_effect = lambda db, record: record

    call_count = 0

    async def fail_third_call(sql):
        nonlocal call_count
        call_count += 1
        if call_count == 3:
            from src.graph.arcade_client import ArcadeDBError
            raise ArcadeDBError(400, "Simulated failure")
        return {"result": []}

    client = AsyncMock()
    client.config = MagicMock()
    client.config.database = "grace"
    client.execute_sql = fail_third_call

    db = MagicMock()
    result = await sync_schema_to_graph(db, client)

    assert result.status == "partial"
    assert result.failed == 1
    assert result.succeeded == result.total_statements - 1


@patch("src.graph.schema_sync.get_sync_by_version")
@patch("src.graph.schema_sync.get_active_version")
@pytest.mark.asyncio
async def test_sync_already_synced(mock_get_active, mock_get_sync):
    """Same version already synced — returns existing record (idempotent)."""
    active = _make_active_version()
    mock_get_active.return_value = active

    existing = GraphSchemaSyncRecord(
        ontology_version_id=str(active.id),
        ontology_version_number=3,
        status="success",
        total_statements=10,
        succeeded=10,
        failed=0,
    )
    mock_get_sync.return_value = existing

    client = AsyncMock()
    db = MagicMock()
    result = await sync_schema_to_graph(db, client)

    assert result.status == "success"
    assert result.id == existing.id
    # execute_sql should NOT have been called
    client.execute_sql.assert_not_called()


@patch("src.graph.schema_sync.get_active_version", return_value=None)
@pytest.mark.asyncio
async def test_sync_no_active_version(mock_get_active):
    """Returns error when no ontology exists."""
    client = AsyncMock()
    db = MagicMock()

    with pytest.raises(ValueError, match="No active ontology"):
        await sync_schema_to_graph(db, client)


@patch("src.graph.schema_sync.get_active_version")
@pytest.mark.asyncio
async def test_preview_returns_ddl_without_executing(mock_get_active):
    """Dry run returns DDL statements without executing anything."""
    active = _make_active_version()
    mock_get_active.return_value = active

    db = MagicMock()
    result = await preview_sync(db)

    assert result["version_number"] == 3
    assert result["statement_count"] > 0
    assert len(result["ddl_statements"]) == result["statement_count"]
    # All statements should be strings
    for stmt in result["ddl_statements"]:
        assert isinstance(stmt, str)
        assert "IF NOT EXISTS" in stmt


@patch("src.graph.schema_sync.get_latest_sync")
@patch("src.graph.schema_sync.get_active_version")
@pytest.mark.asyncio
async def test_get_sync_status(mock_get_active, mock_get_latest):
    """Returns version comparison showing sync state."""
    active = _make_active_version()
    mock_get_active.return_value = active

    latest = GraphSchemaSyncRecord(
        ontology_version_id=str(active.id),
        ontology_version_number=3,
        status="success",
        total_statements=10,
        succeeded=10,
        failed=0,
        completed_at=datetime.now(UTC),
    )
    mock_get_latest.return_value = latest

    db = MagicMock()
    result = await get_sync_status(db)

    assert result["ontology_version"] == 3
    assert result["graph_version"] == 3
    assert result["in_sync"] is True
    assert result["last_sync_status"] == "success"
    assert result["last_sync_at"] is not None
