"""Tests for the federation service layer (Chunk 51 CP6)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.federation.models import FederationConfig, NamespaceRegistration
from src.federation.service import FederationService


@pytest.fixture()
def config():
    return FederationConfig()


@pytest.fixture()
def service(config):
    return FederationService(config=config)


@pytest.fixture()
def mock_session():
    return MagicMock()


@pytest.fixture()
def mock_client():
    return AsyncMock()


class TestFederationService:

    @pytest.mark.asyncio
    async def test_resolve_entity_delegates_async(self, service, mock_session):
        with patch(
            "src.federation.service.CanonicalEntityRegistry"
        ) as MockReg:
            mock_reg = MockReg.return_value
            mock_reg.resolve = AsyncMock(return_value=(None, "unresolved"))

            entity, method = await service.resolve_entity(
                mock_session, "Test", "Legal_Entity"
            )

        assert method == "unresolved"
        mock_reg.resolve.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_register_namespace_delegates_sync(
        self, service, mock_session, mock_client
    ):
        reg = NamespaceRegistration(
            database_name="child1",
            label_prefix="Child",
        )

        with patch(
            "src.federation.service.register_federation_namespace",
            new_callable=AsyncMock,
        ) as mock_register:
            from src.graph.management_models import GraphNamespace

            mock_register.return_value = GraphNamespace(database_name="child1")

            result = await service.register_namespace(
                mock_session, mock_client, reg
            )

        assert result.database_name == "child1"
        mock_register.assert_awaited_once()

    def test_validate_child_delegates(self, service):
        child = {"entity_types": {"T": {"properties": {"a": {"type": "string"}}}}}
        mother = {"entity_types": {"T": {"properties": {"a": {"type": "string"}}}}}

        result = service.validate_child(child, mother)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_list_canonical_entities_delegates(self, service, mock_session):
        with patch(
            "src.federation.service.CanonicalEntityRegistry"
        ) as MockReg:
            mock_reg = MockReg.return_value
            mock_reg.list_canonicals = AsyncMock(return_value=[])

            result = await service.list_canonical_entities(mock_session)

        assert result == []
        mock_reg.list_canonicals.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unregister_namespace_delegates(
        self, service, mock_session, mock_client
    ):
        with patch(
            "src.federation.service.unregister_federation_namespace",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_unreg:
            result = await service.unregister_namespace(
                mock_session, mock_client, "child1"
            )

        assert result is True
        mock_unreg.assert_awaited_once()
