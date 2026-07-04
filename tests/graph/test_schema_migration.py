"""Tests for diff-based incremental schema migration."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.graph.schema_migration import migrate_schema


def _make_version(version_number, schema_json):
    """Create a mock ontology version."""
    v = MagicMock()
    v.id = uuid4()
    v.version_number = version_number
    v.schema_json = schema_json
    return v


SCHEMA_V1 = {
    "entity_types": {
        "Person": {"properties": {"name": {"data_type": "string"}}},
    },
    "relationships": {
        "knows": {"source_type": "Person", "target_type": "Person", "properties": {}},
    },
}

SCHEMA_V2 = {
    "entity_types": {
        "Person": {"properties": {"name": {"data_type": "string"}, "age": {"data_type": "integer"}}},
        "Company": {"properties": {"name": {"data_type": "string"}}},
    },
    "relationships": {
        "knows": {"source_type": "Person", "target_type": "Person", "properties": {}},
        "employs": {"source_type": "Company", "target_type": "Person", "properties": {}},
    },
}

SCHEMA_V3_REMOVE = {
    "entity_types": {
        "Company": {"properties": {"name": {"data_type": "string"}}},
    },
    "relationships": {
        "employs": {"source_type": "Company", "target_type": "Person", "properties": {}},
    },
}


@patch("src.graph.schema_migration.create_sync_record")
@patch("src.graph.schema_migration.get_version_by_number")
@pytest.mark.asyncio
async def test_migrate_adds_new_types(mock_get_version, mock_create_record):
    """Added types produce CREATE VERTEX TYPE DDL."""
    v1 = _make_version(1, SCHEMA_V1)
    v2 = _make_version(2, SCHEMA_V2)
    mock_get_version.side_effect = lambda db, n: v1 if n == 1 else v2
    mock_create_record.side_effect = lambda db, record: record

    client = AsyncMock()
    client.config = MagicMock()
    client.config.database = "grace"
    client.execute_sql = AsyncMock(return_value={"result": []})

    db = MagicMock()
    result = await migrate_schema(db, client, from_version=1, to_version=2)

    assert result.status == "success"
    assert result.succeeded > 0
    executed_stmts = [call.args[0] for call in client.execute_sql.call_args_list]
    assert any("CREATE VERTEX TYPE Company" in s for s in executed_stmts)


@patch("src.graph.schema_migration.create_sync_record")
@patch("src.graph.schema_migration.get_version_by_number")
@pytest.mark.asyncio
async def test_migrate_deprecates_removed_types(mock_get_version, mock_create_record):
    """Removed types get _deprecated=true, not DROP TYPE."""
    v2 = _make_version(2, SCHEMA_V2)
    v3 = _make_version(3, SCHEMA_V3_REMOVE)
    mock_get_version.side_effect = lambda db, n: v2 if n == 2 else v3
    mock_create_record.side_effect = lambda db, record: record

    client = AsyncMock()
    client.config = MagicMock()
    client.config.database = "grace"
    client.execute_sql = AsyncMock(return_value={"result": []})

    db = MagicMock()
    result = await migrate_schema(db, client, from_version=2, to_version=3)

    executed_stmts = [call.args[0] for call in client.execute_sql.call_args_list]
    # Never DROP TYPE
    assert not any("DROP TYPE" in s for s in executed_stmts)
    # Deprecation properties added
    assert any("Person._deprecated" in s for s in executed_stmts)
    assert any("_deprecated_at" in s for s in executed_stmts)


@patch("src.graph.schema_migration.create_sync_record")
@patch("src.graph.schema_migration.get_version_by_number")
@pytest.mark.asyncio
async def test_migrate_adds_new_properties(mock_get_version, mock_create_record):
    """Added properties on existing types produce CREATE PROPERTY DDL."""
    v1 = _make_version(1, SCHEMA_V1)
    v2 = _make_version(2, SCHEMA_V2)
    mock_get_version.side_effect = lambda db, n: v1 if n == 1 else v2
    mock_create_record.side_effect = lambda db, record: record

    client = AsyncMock()
    client.config = MagicMock()
    client.config.database = "grace"
    client.execute_sql = AsyncMock(return_value={"result": []})

    db = MagicMock()
    result = await migrate_schema(db, client, from_version=1, to_version=2)

    executed_stmts = [call.args[0] for call in client.execute_sql.call_args_list]
    assert any("Person.age" in s and "INTEGER" in s for s in executed_stmts)


@patch("src.graph.schema_migration.create_sync_record")
@patch("src.graph.schema_migration.get_version_by_number")
@pytest.mark.asyncio
async def test_migrate_creates_migration_event(mock_get_version, mock_create_record):
    """Migration_Event vertex is created in ArcadeDB."""
    v1 = _make_version(1, SCHEMA_V1)
    v2 = _make_version(2, SCHEMA_V2)
    mock_get_version.side_effect = lambda db, n: v1 if n == 1 else v2
    mock_create_record.side_effect = lambda db, record: record

    client = AsyncMock()
    client.config = MagicMock()
    client.config.database = "grace"
    client.execute_sql = AsyncMock(return_value={"result": []})
    client.execute_query = AsyncMock(return_value={"result": []})

    db = MagicMock()
    await migrate_schema(db, client, from_version=1, to_version=2)

    # F-026 / ISS-0011: the Migration_Event INSERT is now parameterized and
    # goes through execute_query("sql", ..., params=...).
    executed_stmts = [call.args[1] for call in client.execute_query.call_args_list]
    assert any("INSERT INTO Migration_Event" in s for s in executed_stmts)


@patch("src.graph.schema_migration.create_sync_record")
@patch("src.graph.schema_migration.get_version_by_number")
@pytest.mark.asyncio
async def test_migrate_records_kgcl(mock_get_version, mock_create_record):
    """KGCL commands are generated during migration."""
    v1 = _make_version(1, SCHEMA_V1)
    v2 = _make_version(2, SCHEMA_V2)
    mock_get_version.side_effect = lambda db, n: v1 if n == 1 else v2
    mock_create_record.side_effect = lambda db, record: record

    client = AsyncMock()
    client.config = MagicMock()
    client.config.database = "grace"
    client.execute_sql = AsyncMock(return_value={"result": []})
    client.execute_query = AsyncMock(return_value={"result": []})

    db = MagicMock()
    await migrate_schema(db, client, from_version=1, to_version=2)

    # F-026 / ISS-0011: the Migration_Event INSERT is now parameterized —
    # the statement carries :kgcl_commands and the KGCL text rides in params.
    migration_calls = [
        call for call in client.execute_query.call_args_list
        if "INSERT INTO Migration_Event" in call.args[1]
    ]
    assert len(migration_calls) == 1
    assert ":kgcl_commands" in migration_calls[0].args[1]
    assert "create class" in migration_calls[0].kwargs["params"]["kgcl_commands"]


@patch("src.graph.schema_migration.get_version_by_number")
@pytest.mark.asyncio
async def test_migrate_same_version_noop(mock_get_version):
    """Identical schemas produce no migration (noop)."""
    v1 = _make_version(1, SCHEMA_V1)
    v1_copy = _make_version(2, SCHEMA_V1)
    mock_get_version.side_effect = lambda db, n: v1 if n == 1 else v1_copy

    client = AsyncMock()
    db = MagicMock()
    result = await migrate_schema(db, client, from_version=1, to_version=2)

    assert result.status == "success"
    assert result.total_statements == 0
    client.execute_sql.assert_not_called()
