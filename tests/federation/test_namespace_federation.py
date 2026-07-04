"""Tests for federation namespace registration and cleanup (Chunk 51 CP4)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.graph.management_models import GraphNamespace


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_db():
    """Mock SQLAlchemy session."""
    return MagicMock()


@pytest.fixture()
def mock_client():
    """Mock ArcadeDB client with async execute_sql."""
    client = AsyncMock()
    client.execute_sql = AsyncMock(return_value={})
    return client


def _make_namespace(**overrides) -> GraphNamespace:
    defaults = {
        "database_name": "test_child",
        "namespace_type": "child",
        "label_prefix": "Procore",
        "ontology_module": "construction",
    }
    defaults.update(overrides)
    return GraphNamespace(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRegisterFederationNamespace:
    """Tests for register_federation_namespace."""

    @pytest.mark.asyncio
    async def test_register_creates_prefixed_types(self, mock_db, mock_client):
        """register creates prefixed vertex/edge types in ArcadeDB."""
        ns = _make_namespace()

        with patch(
            "src.federation.namespace_federation.get_namespace_by_name",
            return_value=None,
        ), patch(
            "src.federation.namespace_federation.db_create",
            return_value=ns,
        ), patch(
            "src.federation.namespace_federation.db_list",
            return_value=[],
        ):
            from src.federation.namespace_federation import (
                register_federation_namespace,
            )

            result = await register_federation_namespace(mock_db, mock_client, ns)

        assert result.label_prefix == "Procore"
        # F-42: base V/E types are now created (idempotently) before the
        # prefixed types so `EXTENDS E` does not fail on a fresh ArcadeDB.
        assert mock_client.execute_sql.call_count == 4
        calls = [c.args[0] for c in mock_client.execute_sql.call_args_list]
        assert any("Procore_Entity" in c for c in calls)
        assert any("Procore_Relationship" in c for c in calls)
        assert any("CREATE EDGE TYPE E IF NOT EXISTS" in c for c in calls)
        assert any("CREATE VERTEX TYPE V IF NOT EXISTS" in c for c in calls)
        # Base types must be created BEFORE the prefixed edge that EXTENDS E.
        e_idx = next(i for i, c in enumerate(calls) if "CREATE EDGE TYPE E IF NOT EXISTS" in c)
        rel_idx = next(i for i, c in enumerate(calls) if "Procore_Relationship" in c)
        assert e_idx < rel_idx

    @pytest.mark.asyncio
    async def test_register_adds_row_with_new_columns(self, mock_db, mock_client):
        """register passes all federation columns through to db_create."""
        ns = _make_namespace(
            namespace_type="child",
            label_prefix="Procore",
            ontology_module="construction",
            parent_namespace_id="some-uuid",
        )

        with patch(
            "src.federation.namespace_federation.get_namespace_by_name",
            return_value=None,
        ), patch(
            "src.federation.namespace_federation.db_create",
            return_value=ns,
        ) as mock_create, patch(
            "src.federation.namespace_federation.db_list",
            return_value=[],
        ):
            from src.federation.namespace_federation import (
                register_federation_namespace,
            )

            result = await register_federation_namespace(mock_db, mock_client, ns)

        created_ns = mock_create.call_args[0][1]
        assert created_ns.namespace_type == "child"
        assert created_ns.label_prefix == "Procore"
        assert created_ns.ontology_module == "construction"
        assert result.parent_namespace_id == "some-uuid"

    @pytest.mark.asyncio
    async def test_duplicate_label_prefix_rejected(self, mock_db, mock_client):
        """Duplicate label_prefix raises ValueError."""
        existing = _make_namespace(label_prefix="Procore")
        ns = _make_namespace(database_name="another_child")

        with patch(
            "src.federation.namespace_federation.get_namespace_by_name",
            return_value=None,
        ), patch(
            "src.federation.namespace_federation.db_list",
            return_value=[existing],
        ):
            from src.federation.namespace_federation import (
                register_federation_namespace,
            )

            with pytest.raises(ValueError, match="Duplicate label_prefix"):
                await register_federation_namespace(mock_db, mock_client, ns)

    @pytest.mark.asyncio
    async def test_duplicate_database_name_rejected(self, mock_db, mock_client):
        """Duplicate database_name raises ValueError."""
        existing = _make_namespace()
        ns = _make_namespace()

        with patch(
            "src.federation.namespace_federation.get_namespace_by_name",
            return_value=existing,
        ):
            from src.federation.namespace_federation import (
                register_federation_namespace,
            )

            with pytest.raises(ValueError, match="already registered"):
                await register_federation_namespace(mock_db, mock_client, ns)

    @pytest.mark.asyncio
    async def test_register_without_prefix_skips_ddl(self, mock_db, mock_client):
        """register without label_prefix skips ArcadeDB DDL."""
        ns = _make_namespace(label_prefix=None)

        with patch(
            "src.federation.namespace_federation.get_namespace_by_name",
            return_value=None,
        ), patch(
            "src.federation.namespace_federation.db_create",
            return_value=ns,
        ):
            from src.federation.namespace_federation import (
                register_federation_namespace,
            )

            await register_federation_namespace(mock_db, mock_client, ns)

        mock_client.execute_sql.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_prefix_format_rejected(self, mock_db, mock_client):
        """Non-PascalCase prefix raises ValueError."""
        ns = _make_namespace(label_prefix="not_pascal")

        with patch(
            "src.federation.namespace_federation.db_list",
            return_value=[],
        ):
            from src.federation.namespace_federation import (
                register_federation_namespace,
            )

            with pytest.raises(ValueError, match="PascalCase"):
                await register_federation_namespace(mock_db, mock_client, ns)


class TestUnregisterFederationNamespace:
    """Tests for unregister_federation_namespace."""

    @pytest.mark.asyncio
    async def test_unregister_cleans_up_ddl(self, mock_db, mock_client):
        """unregister drops prefixed types from ArcadeDB."""
        ns = _make_namespace()

        with patch(
            "src.federation.namespace_federation.get_namespace_by_name",
            return_value=ns,
        ), patch(
            "src.federation.namespace_federation.db_delete",
            return_value=True,
        ):
            from src.federation.namespace_federation import (
                unregister_federation_namespace,
            )

            result = await unregister_federation_namespace(
                mock_db, mock_client, "test_child"
            )

        assert result is True
        assert mock_client.execute_sql.call_count == 2
        calls = [c.args[0] for c in mock_client.execute_sql.call_args_list]
        assert any("DROP" in c and "Procore_Relationship" in c for c in calls)
        assert any("DROP" in c and "Procore_Entity" in c for c in calls)

    @pytest.mark.asyncio
    async def test_unregister_not_found(self, mock_db, mock_client):
        """unregister returns False when namespace not found."""
        with patch(
            "src.federation.namespace_federation.get_namespace_by_name",
            return_value=None,
        ):
            from src.federation.namespace_federation import (
                unregister_federation_namespace,
            )

            result = await unregister_federation_namespace(
                mock_db, mock_client, "nonexistent"
            )

        assert result is False


class TestGraphNamespaceBackwardCompat:
    """Backward compatibility: existing namespace creation works."""

    def test_graphnamespace_minimal_construction(self):
        """GraphNamespace(database_name='test') still works without new fields."""
        ns = GraphNamespace(database_name="test")
        assert ns.database_name == "test"
        assert ns.namespace_type == "child"
        assert ns.label_prefix is None
        assert ns.ontology_module is None
        assert ns.parent_namespace_id is None
