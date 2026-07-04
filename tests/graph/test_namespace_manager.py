"""Tests for namespace registry operations (mocked ArcadeDB + PostgreSQL)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.graph.arcade_client import ArcadeClient, ArcadeConfig
from src.graph.management_models import GraphNamespace
from src.graph.namespace_manager import (
    get_namespace,
    list_namespaces,
    register_namespace,
    remove_namespace,
)


def _mock_client() -> ArcadeClient:
    """Create an ArcadeClient with mocked ensure_database."""
    client = ArcadeClient(config=ArcadeConfig())
    client.ensure_database = AsyncMock()
    return client


def _mock_db() -> MagicMock:
    """Create a mock SQLAlchemy session."""
    return MagicMock()


@pytest.mark.asyncio
@patch("src.graph.namespace_manager.get_namespace_by_name", return_value=None)
@patch("src.graph.namespace_manager.db_create")
async def test_register_namespace(mock_create, mock_get):
    """register_namespace saves to DB and calls ensure_database."""
    client = _mock_client()
    db = _mock_db()
    ns = GraphNamespace(database_name="child_graph_1")
    mock_create.return_value = ns

    result = await register_namespace(db, client, ns)
    assert result.database_name == "child_graph_1"
    client.ensure_database.assert_awaited_once_with("child_graph_1")
    mock_create.assert_called_once_with(db, ns)


@pytest.mark.asyncio
@patch("src.graph.namespace_manager.db_list")
async def test_list_namespaces(mock_list):
    """list_namespaces returns all registered namespaces."""
    db = _mock_db()
    ns1 = GraphNamespace(database_name="graph_a")
    ns2 = GraphNamespace(database_name="graph_b")
    mock_list.return_value = [ns1, ns2]

    result = await list_namespaces(db)
    assert len(result) == 2
    assert result[0].database_name == "graph_a"
    mock_list.assert_called_once_with(db)


@pytest.mark.asyncio
@patch("src.graph.namespace_manager.db_delete", return_value=True)
async def test_remove_namespace(mock_delete):
    """remove_namespace deletes from DB (does not drop ArcadeDB database)."""
    db = _mock_db()
    result = await remove_namespace(db, "old_graph")
    assert result is True
    mock_delete.assert_called_once_with(db, "old_graph")


@pytest.mark.asyncio
@patch("src.graph.namespace_manager.get_namespace_by_name")
async def test_duplicate_database_name_rejected(mock_get):
    """register_namespace raises ValueError for duplicate database_name."""
    client = _mock_client()
    db = _mock_db()
    existing = GraphNamespace(database_name="taken_name")
    mock_get.return_value = existing

    with pytest.raises(ValueError, match="already registered"):
        await register_namespace(db, client, GraphNamespace(database_name="taken_name"))

    client.ensure_database.assert_not_awaited()


@pytest.mark.asyncio
@patch("src.graph.namespace_manager.get_namespace_by_name")
async def test_get_namespace_by_name(mock_get):
    """get_namespace returns correct entry by database name."""
    db = _mock_db()
    ns = GraphNamespace(database_name="my_graph")
    mock_get.return_value = ns

    result = await get_namespace(db, "my_graph")
    assert result is not None
    assert result.database_name == "my_graph"
    mock_get.assert_called_once_with(db, "my_graph")
