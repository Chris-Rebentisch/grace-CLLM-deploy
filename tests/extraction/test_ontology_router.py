"""Tests for the OntologyRouter — Chunk 17."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.extraction.ontology_router import OntologyRouter, OntologyRouterError


class TestRouteExplicit:
    """Tests for explicit module routing."""

    @pytest.mark.asyncio
    async def test_success(self):
        """Mocked API returns module schema dict. Router returns it."""
        module_schema = {"entity_types": {"Legal_Entity": {}}}
        mock_response = httpx.Response(
            200,
            json=module_schema,
            request=httpx.Request("GET", "http://test/api/ontology/modules/core"),
        )

        router = OntologyRouter(base_url="http://test")
        with patch.object(router, "_get", new_callable=AsyncMock, return_value=mock_response):
            result = await router.route_explicit("core")

        assert result == module_schema

    @pytest.mark.asyncio
    async def test_not_found(self):
        """Mocked API returns 404. Router returns None."""
        mock_response = httpx.Response(
            404,
            json={"detail": "Not found"},
            request=httpx.Request("GET", "http://test/api/ontology/modules/missing"),
        )

        router = OntologyRouter(base_url="http://test")
        with patch.object(router, "_get", new_callable=AsyncMock, return_value=mock_response):
            result = await router.route_explicit("missing")

        assert result is None


class TestGetActiveSchema:
    """Tests for active schema retrieval."""

    @pytest.mark.asyncio
    async def test_success(self):
        """Mocked API returns active version dict."""
        version_dict = {
            "version_number": 3,
            "schema_json": {"entity_types": {}},
            "schema_modules": {"core": {}, "finance": {}},
        }
        mock_response = httpx.Response(
            200,
            json=version_dict,
            request=httpx.Request("GET", "http://test/api/ontology/active"),
        )

        router = OntologyRouter(base_url="http://test")
        with patch.object(router, "_get", new_callable=AsyncMock, return_value=mock_response):
            result = await router.get_active_schema()

        assert result == version_dict
        assert result["version_number"] == 3

    @pytest.mark.asyncio
    async def test_no_version(self):
        """Mocked API returns 404. Router returns None."""
        mock_response = httpx.Response(
            404,
            json={"detail": "No active version"},
            request=httpx.Request("GET", "http://test/api/ontology/active"),
        )

        router = OntologyRouter(base_url="http://test")
        with patch.object(router, "_get", new_callable=AsyncMock, return_value=mock_response):
            result = await router.get_active_schema()

        assert result is None


class TestGetAvailableModules:
    """Tests for module listing."""

    @pytest.mark.asyncio
    async def test_with_modules(self):
        """Active schema has 3 modules. Returns list of 3 names."""
        version_dict = {
            "schema_modules": {"core": {}, "finance": {}, "legal": {}},
            "schema_json": {},
        }
        mock_response = httpx.Response(
            200,
            json=version_dict,
            request=httpx.Request("GET", "http://test/api/ontology/active"),
        )

        router = OntologyRouter(base_url="http://test")
        with patch.object(router, "_get", new_callable=AsyncMock, return_value=mock_response):
            modules = await router.get_available_modules()

        assert sorted(modules) == ["core", "finance", "legal"]

    @pytest.mark.asyncio
    async def test_no_modules(self):
        """Schema has empty/missing schema_modules. Returns empty list."""
        version_dict = {"schema_json": {}}
        mock_response = httpx.Response(
            200,
            json=version_dict,
            request=httpx.Request("GET", "http://test/api/ontology/active"),
        )

        router = OntologyRouter(base_url="http://test")
        with patch.object(router, "_get", new_callable=AsyncMock, return_value=mock_response):
            modules = await router.get_available_modules()

        assert modules == []


class TestResolveSchema:
    """Tests for one-call schema resolution (returns tuple)."""

    @pytest.mark.asyncio
    async def test_with_module_name(self):
        """Calls get_active_schema, returns module schema from schema_modules + version."""
        module_schema = {"entity_types": {"Legal_Entity": {}}}
        version_dict = {
            "version_number": 5,
            "schema_json": {"entity_types": {"Legal_Entity": {}, "Contract": {}}},
            "schema_modules": {"core": module_schema},
        }
        mock_response = httpx.Response(
            200,
            json=version_dict,
            request=httpx.Request("GET", "http://test/api/ontology/active"),
        )

        router = OntologyRouter(base_url="http://test")
        with patch.object(router, "_get", new_callable=AsyncMock, return_value=mock_response):
            schema, version = await router.resolve_schema(module_name="core")

        assert schema == module_schema
        assert version == 5

    @pytest.mark.asyncio
    async def test_without_module_name(self):
        """Returns schema_json + version_number from active version dict."""
        schema_json = {"entity_types": {"Legal_Entity": {}, "Contract": {}}}
        version_dict = {
            "version_number": 3,
            "schema_json": schema_json,
            "schema_modules": {"core": {}},
        }
        mock_response = httpx.Response(
            200,
            json=version_dict,
            request=httpx.Request("GET", "http://test/api/ontology/active"),
        )

        router = OntologyRouter(base_url="http://test")
        with patch.object(router, "_get", new_callable=AsyncMock, return_value=mock_response):
            schema, version = await router.resolve_schema(module_name=None)

        assert schema == schema_json
        assert version == 3

    @pytest.mark.asyncio
    async def test_no_active_version(self):
        """No active version returns (None, None)."""
        mock_response = httpx.Response(
            404,
            json={"detail": "No active version"},
            request=httpx.Request("GET", "http://test/api/ontology/active"),
        )

        router = OntologyRouter(base_url="http://test")
        with patch.object(router, "_get", new_callable=AsyncMock, return_value=mock_response):
            schema, version = await router.resolve_schema(module_name=None)

        assert schema is None
        assert version is None

    @pytest.mark.asyncio
    async def test_module_not_in_schema_modules(self):
        """Module name not in schema_modules returns (None, None)."""
        version_dict = {
            "version_number": 2,
            "schema_json": {},
            "schema_modules": {"core": {}},
        }
        mock_response = httpx.Response(
            200,
            json=version_dict,
            request=httpx.Request("GET", "http://test/api/ontology/active"),
        )

        router = OntologyRouter(base_url="http://test")
        with patch.object(router, "_get", new_callable=AsyncMock, return_value=mock_response):
            schema, version = await router.resolve_schema(module_name="missing")

        assert schema is None
        assert version is None


class TestErrorHandling:
    """Tests for failure policy."""

    @pytest.mark.asyncio
    async def test_connection_failure(self):
        """httpx ConnectError raises OntologyRouterError."""
        router = OntologyRouter(base_url="http://localhost:99999")
        with patch.object(
            router,
            "_get",
            new_callable=AsyncMock,
            side_effect=OntologyRouterError("Connection failed"),
        ):
            with pytest.raises(OntologyRouterError, match="Connection failed"):
                await router.route_explicit("core")

    @pytest.mark.asyncio
    async def test_server_error(self):
        """500 response raises OntologyRouterError."""
        router = OntologyRouter(base_url="http://test")

        async def mock_get_500(path: str) -> httpx.Response:
            raise OntologyRouterError("Server error 500 from http://test/api/ontology/active")

        with patch.object(router, "_get", side_effect=mock_get_500):
            with pytest.raises(OntologyRouterError, match="Server error"):
                await router.get_active_schema()

    @pytest.mark.asyncio
    async def test_get_connection_error_wrapping(self):
        """_get wraps httpx.ConnectError into OntologyRouterError."""
        router = OntologyRouter(base_url="http://localhost:99999")

        # Patch httpx.AsyncClient to raise ConnectError
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.ConnectError("Connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(OntologyRouterError, match="Connection failed"):
                await router._get("/api/ontology/active")

    @pytest.mark.asyncio
    async def test_get_server_error_wrapping(self):
        """_get raises OntologyRouterError on 500+ status."""
        router = OntologyRouter(base_url="http://test")

        mock_response = httpx.Response(
            500,
            text="Internal Server Error",
            request=httpx.Request("GET", "http://test/api/ontology/active"),
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(OntologyRouterError, match="Server error 500"):
                await router._get("/api/ontology/active")
