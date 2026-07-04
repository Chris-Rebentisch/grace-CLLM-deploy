"""Tests for federation API routes (Chunk 51 CP7)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Fixtures / Helpers
# ---------------------------------------------------------------------------

def _admin_headers():
    return {"X-Admin-Key": ""}


def _mock_namespace(ns_id=None, **kwargs):
    from src.graph.management_models import GraphNamespace

    defaults = {
        "database_name": "child_test",
        "namespace_type": "child",
        "label_prefix": "ChildTest",
    }
    defaults.update(kwargs)
    ns = GraphNamespace(**defaults)
    if ns_id:
        ns.id = ns_id
    return ns


# ---------------------------------------------------------------------------
# POST /api/federation/namespaces
# ---------------------------------------------------------------------------


class TestRegisterNamespace:

    @patch("src.api.federation_routes._get_db")
    @patch("src.api.federation_routes.get_arcade_client")
    @patch("src.api.federation_routes._get_service")
    def test_register_returns_201(self, mock_svc_factory, mock_client, mock_db):
        ns = _mock_namespace()
        mock_service = MagicMock()
        mock_service.register_namespace = AsyncMock(return_value=ns)
        mock_svc_factory.return_value = mock_service
        mock_db.return_value = MagicMock()

        resp = client.post(
            "/api/federation/namespaces",
            json={"database_name": "child_test", "label_prefix": "ChildTest"},
        )
        assert resp.status_code == 201

    @patch("src.api.federation_routes._get_db")
    @patch("src.api.federation_routes.get_arcade_client")
    @patch("src.api.federation_routes._get_service")
    def test_register_duplicate_returns_409(self, mock_svc_factory, mock_client, mock_db):
        mock_service = MagicMock()
        mock_service.register_namespace = AsyncMock(
            side_effect=ValueError("Duplicate label_prefix")
        )
        mock_svc_factory.return_value = mock_service
        mock_db.return_value = MagicMock()

        resp = client.post(
            "/api/federation/namespaces",
            json={"database_name": "child2", "label_prefix": "Dup"},
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# GET /api/federation/namespaces
# ---------------------------------------------------------------------------


class TestListNamespaces:

    @patch("src.api.federation_routes._get_db")
    @patch("src.api.federation_routes.list_namespaces")
    def test_list_returns_200(self, mock_list, mock_db):
        mock_db.return_value = MagicMock()
        mock_list.return_value = [_mock_namespace()]

        resp = client.get("/api/federation/namespaces")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1


# ---------------------------------------------------------------------------
# GET /api/federation/namespaces/{namespace_id}
# ---------------------------------------------------------------------------


class TestGetNamespace:

    @patch("src.api.federation_routes._get_db")
    @patch("src.api.federation_routes.list_namespaces")
    def test_get_found(self, mock_list, mock_db):
        ns = _mock_namespace()
        ns_id = ns.id
        mock_db.return_value = MagicMock()
        mock_list.return_value = [ns]

        resp = client.get(f"/api/federation/namespaces/{ns_id}")
        assert resp.status_code == 200

    @patch("src.api.federation_routes._get_db")
    @patch("src.api.federation_routes.list_namespaces")
    def test_get_not_found(self, mock_list, mock_db):
        mock_db.return_value = MagicMock()
        mock_list.return_value = []

        resp = client.get(f"/api/federation/namespaces/{uuid4()}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/federation/namespaces/{namespace_id}
# ---------------------------------------------------------------------------


class TestDeleteNamespace:

    @patch("src.api.federation_routes._get_db")
    @patch("src.api.federation_routes.list_namespaces")
    @patch("src.api.federation_routes.get_arcade_client")
    @patch("src.api.federation_routes._get_service")
    def test_delete_not_found(self, mock_svc, mock_client, mock_list, mock_db):
        mock_db.return_value = MagicMock()
        mock_list.return_value = []

        resp = client.delete(f"/api/federation/namespaces/{uuid4()}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/federation/registry/resolve
# ---------------------------------------------------------------------------


class TestResolveEntity:

    @patch("src.api.federation_routes._get_db")
    @patch("src.api.federation_routes._get_service")
    def test_resolve_returns_200(self, mock_svc_factory, mock_db):
        mock_service = MagicMock()
        mock_service.resolve_entity = AsyncMock(return_value=(None, "unresolved"))
        mock_svc_factory.return_value = mock_service
        mock_db.return_value = MagicMock()

        resp = client.post(
            "/api/federation/registry/resolve",
            json={"name": "Acme", "entity_type": "Legal_Entity"},
        )
        assert resp.status_code == 200
        assert resp.json() is None


# ---------------------------------------------------------------------------
# GET /api/federation/registry
# ---------------------------------------------------------------------------


class TestListRegistry:

    @patch("src.api.federation_routes._get_db")
    @patch("src.api.federation_routes._get_service")
    def test_list_returns_200(self, mock_svc_factory, mock_db):
        mock_service = MagicMock()
        mock_service.list_canonical_entities = AsyncMock(return_value=[])
        mock_svc_factory.return_value = mock_service
        mock_db.return_value = MagicMock()

        resp = client.get("/api/federation/registry")
        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# POST /api/federation/validate-child-schema
# ---------------------------------------------------------------------------


class TestValidateChildSchema:

    @patch("src.api.federation_routes._get_db")
    @patch("src.ontology.database.get_active_version")
    @patch("src.api.federation_routes._get_service")
    def test_validate_no_active_mother(self, mock_svc, mock_active, mock_db):
        mock_db.return_value = MagicMock()
        mock_active.return_value = None

        resp = client.post(
            "/api/federation/validate-child-schema",
            json={"child_schema": {"entity_types": {}}},
        )
        assert resp.status_code == 404

    @patch("src.api.federation_routes._get_db")
    @patch("src.ontology.database.get_active_version")
    @patch("src.api.federation_routes._get_service")
    def test_validate_passes(self, mock_svc_factory, mock_active, mock_db):
        mock_db.return_value = MagicMock()

        class FakeVersion:
            schema_json = {"entity_types": {"T": {"properties": {"a": {"type": "string"}}}}}

        mock_active.return_value = FakeVersion()
        mock_service = MagicMock()

        from src.federation.scope_validator import ValidationResult
        mock_service.validate_child.return_value = ValidationResult(passed=True, type_results=[])
        mock_svc_factory.return_value = mock_service

        resp = client.post(
            "/api/federation/validate-child-schema",
            json={"child_schema": {"entity_types": {"T": {"properties": {"a": {"type": "string"}}}}}},
        )
        assert resp.status_code == 200
        assert resp.json()["valid"] is True
