"""Tests for ArcadeDB client, config, and API routes (mocked HTTP, no live ArcadeDB)."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.graph.arcade_client import ArcadeClient, ArcadeDBError
from src.graph.config import ArcadeConfig


# ---------------------------------------------------------------------------
# Helper — mock httpx response
# ---------------------------------------------------------------------------

def _make_mock_resp(status_code, json_data=None, text=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text or str(json_data)
    resp.headers = {}
    return resp


def _mock_client_ctx(mock_instance, MockClient):
    """Wire up async-context-manager plumbing for httpx.AsyncClient."""
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock(return_value=False)
    MockClient.return_value = mock_instance


# ===========================================================================
# Connection tests (4)
# ===========================================================================

@patch("src.graph.arcade_client.httpx.AsyncClient")
@pytest.mark.asyncio
async def test_health_check_success(MockClient):
    mock_instance = AsyncMock()
    mock_instance.get.return_value = _make_mock_resp(
        200, {"user": "root", "version": "26.3.2"}
    )
    _mock_client_ctx(mock_instance, MockClient)

    client = ArcadeClient(config=ArcadeConfig())
    result = await client.health_check()
    assert result["version"] == "26.3.2"
    mock_instance.get.assert_called_once_with("/api/v1/server")


@patch("src.graph.arcade_client.httpx.AsyncClient")
@pytest.mark.asyncio
async def test_health_check_connection_error(MockClient):
    mock_instance = AsyncMock()
    mock_instance.get.side_effect = httpx.ConnectError("refused")
    _mock_client_ctx(mock_instance, MockClient)

    client = ArcadeClient(config=ArcadeConfig())
    with pytest.raises(ConnectionError, match="not reachable"):
        await client.health_check()


@patch("src.graph.arcade_client.httpx.AsyncClient")
@pytest.mark.asyncio
async def test_server_ready_true(MockClient):
    mock_instance = AsyncMock()
    mock_instance.get.return_value = _make_mock_resp(
        200, {"user": "root", "version": "26.3.2"}
    )
    _mock_client_ctx(mock_instance, MockClient)

    client = ArcadeClient(config=ArcadeConfig())
    assert await client.server_ready() is True


@patch("src.graph.arcade_client.httpx.AsyncClient")
@pytest.mark.asyncio
async def test_server_ready_false(MockClient):
    mock_instance = AsyncMock()
    mock_instance.get.side_effect = httpx.ConnectError("refused")
    _mock_client_ctx(mock_instance, MockClient)

    client = ArcadeClient(config=ArcadeConfig())
    assert await client.server_ready() is False


# ===========================================================================
# Database management tests (3)
# ===========================================================================

@patch("src.graph.arcade_client.httpx.AsyncClient")
@pytest.mark.asyncio
async def test_create_database(MockClient):
    mock_instance = AsyncMock()
    mock_instance.post.return_value = _make_mock_resp(
        200, {"result": "ok"}
    )
    _mock_client_ctx(mock_instance, MockClient)

    client = ArcadeClient(config=ArcadeConfig())
    result = await client.create_database("testdb")
    assert result["result"] == "ok"
    call_args = mock_instance.post.call_args
    assert call_args[0][0] == "/api/v1/server"
    assert "create database testdb" in str(call_args)


@patch("src.graph.arcade_client.httpx.AsyncClient")
@pytest.mark.asyncio
async def test_database_exists_true(MockClient):
    mock_instance = AsyncMock()
    mock_instance.post.return_value = _make_mock_resp(
        200, {"result": ["grace", "other"]}
    )
    _mock_client_ctx(mock_instance, MockClient)

    client = ArcadeClient(config=ArcadeConfig())
    assert await client.database_exists("grace") is True


@patch("src.graph.arcade_client.httpx.AsyncClient")
@pytest.mark.asyncio
async def test_database_exists_false(MockClient):
    mock_instance = AsyncMock()
    mock_instance.post.return_value = _make_mock_resp(
        200, {"result": ["other"]}
    )
    _mock_client_ctx(mock_instance, MockClient)

    client = ArcadeClient(config=ArcadeConfig())
    assert await client.database_exists("grace") is False


# ===========================================================================
# SQL execution tests (3)
# ===========================================================================

@patch("src.graph.arcade_client.httpx.AsyncClient")
@pytest.mark.asyncio
async def test_execute_sql_create_vertex_type(MockClient):
    mock_instance = AsyncMock()
    mock_instance.post.return_value = _make_mock_resp(200, {"result": []})
    _mock_client_ctx(mock_instance, MockClient)

    client = ArcadeClient(config=ArcadeConfig())
    await client.execute_sql("CREATE VERTEX TYPE Person")
    call_args = mock_instance.post.call_args
    body = call_args[1]["json"]
    assert body["language"] == "sql"
    assert body["command"] == "CREATE VERTEX TYPE Person"
    assert body["serializer"] == "record"


@patch("src.graph.arcade_client.httpx.AsyncClient")
@pytest.mark.asyncio
async def test_execute_sql_create_property(MockClient):
    mock_instance = AsyncMock()
    mock_instance.post.return_value = _make_mock_resp(200, {"result": []})
    _mock_client_ctx(mock_instance, MockClient)

    client = ArcadeClient(config=ArcadeConfig())
    await client.execute_sql("CREATE PROPERTY Person.name STRING")
    call_args = mock_instance.post.call_args
    body = call_args[1]["json"]
    assert body["language"] == "sql"
    assert body["command"] == "CREATE PROPERTY Person.name STRING"


@patch("src.graph.arcade_client.httpx.AsyncClient")
@pytest.mark.asyncio
async def test_execute_sql_error_handling(MockClient):
    mock_instance = AsyncMock()
    mock_instance.post.return_value = _make_mock_resp(
        400, text="Type already exists"
    )
    _mock_client_ctx(mock_instance, MockClient)

    client = ArcadeClient(config=ArcadeConfig())
    with pytest.raises(ArcadeDBError) as exc_info:
        await client.execute_sql("CREATE VERTEX TYPE Person")
    assert exc_info.value.status_code == 400


# ===========================================================================
# OpenCypher execution tests (4)
# ===========================================================================

@patch("src.graph.arcade_client.httpx.AsyncClient")
@pytest.mark.asyncio
async def test_execute_cypher_create_node(MockClient):
    mock_instance = AsyncMock()
    mock_instance.post.return_value = _make_mock_resp(
        200, {"result": [{"@rid": "#1:0"}]}
    )
    _mock_client_ctx(mock_instance, MockClient)

    client = ArcadeClient(config=ArcadeConfig())
    result = await client.execute_cypher("CREATE (p:Person {name: 'Alice'}) RETURN p")
    call_args = mock_instance.post.call_args
    body = call_args[1]["json"]
    assert body["language"] == "opencypher"
    assert result["result"][0]["@rid"] == "#1:0"


@patch("src.graph.arcade_client.httpx.AsyncClient")
@pytest.mark.asyncio
async def test_execute_cypher_match_query(MockClient):
    mock_instance = AsyncMock()
    mock_instance.post.return_value = _make_mock_resp(
        200, {"result": [{"name": "Alice"}, {"name": "Bob"}]}
    )
    _mock_client_ctx(mock_instance, MockClient)

    client = ArcadeClient(config=ArcadeConfig())
    result = await client.execute_cypher("MATCH (p:Person) RETURN p.name")
    assert len(result["result"]) == 2


@patch("src.graph.arcade_client.httpx.AsyncClient")
@pytest.mark.asyncio
async def test_execute_cypher_with_params(MockClient):
    mock_instance = AsyncMock()
    mock_instance.post.return_value = _make_mock_resp(
        200, {"result": [{"name": "Alice"}]}
    )
    _mock_client_ctx(mock_instance, MockClient)

    client = ArcadeClient(config=ArcadeConfig())
    result = await client.execute_cypher(
        "MATCH (p:Person {name: $name}) RETURN p",
        params={"name": "Alice"},
    )
    assert len(result["result"]) == 1
    # Verify language is opencypher
    body = mock_instance.post.call_args[1]["json"]
    assert body["language"] == "opencypher"


@patch("src.graph.arcade_client.httpx.AsyncClient")
@pytest.mark.asyncio
async def test_execute_cypher_uses_opencypher_not_cypher(MockClient):
    """CRITICAL: language field must always be 'opencypher', never 'cypher'."""
    mock_instance = AsyncMock()
    mock_instance.post.return_value = _make_mock_resp(200, {"result": []})
    _mock_client_ctx(mock_instance, MockClient)

    client = ArcadeClient(config=ArcadeConfig())
    await client.execute_cypher("MATCH (n) RETURN n LIMIT 1")
    body = mock_instance.post.call_args[1]["json"]
    assert body["language"] == "opencypher"
    assert body["language"] != "cypher"


# ===========================================================================
# Error handling tests (3)
# ===========================================================================

@patch("src.graph.arcade_client.httpx.AsyncClient")
@pytest.mark.asyncio
async def test_error_400_raises_arcade_error(MockClient):
    mock_instance = AsyncMock()
    mock_instance.post.return_value = _make_mock_resp(
        400, text="Invalid query"
    )
    _mock_client_ctx(mock_instance, MockClient)

    client = ArcadeClient(config=ArcadeConfig())
    with pytest.raises(ArcadeDBError) as exc_info:
        await client.execute_sql("INVALID SQL")
    assert exc_info.value.status_code == 400
    assert "Invalid query" in exc_info.value.detail


@patch("src.graph.arcade_client.httpx.AsyncClient")
@pytest.mark.asyncio
async def test_error_500_raises_arcade_error(MockClient):
    mock_instance = AsyncMock()
    mock_instance.post.return_value = _make_mock_resp(
        500, text="Internal server error"
    )
    _mock_client_ctx(mock_instance, MockClient)

    client = ArcadeClient(config=ArcadeConfig())
    with pytest.raises(ArcadeDBError) as exc_info:
        await client.execute_cypher("MATCH (n) RETURN n")
    assert exc_info.value.status_code == 500


@patch("src.graph.arcade_client.httpx.AsyncClient")
@pytest.mark.asyncio
async def test_timeout_raises_timeout_error(MockClient):
    mock_instance = AsyncMock()
    mock_instance.post.side_effect = httpx.ReadTimeout("timed out")
    _mock_client_ctx(mock_instance, MockClient)

    client = ArcadeClient(config=ArcadeConfig())
    with pytest.raises(TimeoutError):
        await client.execute_sql("SELECT 1")


# ===========================================================================
# Config tests (3)
# ===========================================================================

def test_arcade_config_defaults(monkeypatch):
    # F-022: ``database`` now defaults from ARCADE_DATABASE; clear it so this
    # asserts the unset-env default (conftest sets it for sandbox isolation).
    monkeypatch.delenv("ARCADE_DATABASE", raising=False)
    config = ArcadeConfig()
    assert config.host == "localhost"
    assert config.port == 2480
    assert config.username == "root"
    assert config.password == "gracedev"
    assert config.database == "grace"
    assert config.timeout == 30


def test_arcade_config_database_honors_env(monkeypatch):
    """F-022: a bare ArcadeConfig() must honor ARCADE_DATABASE — the hardcoded
    'grace' default let sandboxed CLIs (retriage.py, eval_checkpoint.py) read
    the LIVE graph from inside a grace_test run."""
    monkeypatch.setenv("ARCADE_DATABASE", "grace_test")
    assert ArcadeConfig().database == "grace_test"
    monkeypatch.setenv("ARCADE_DATABASE", "  ")  # blank value falls back
    assert ArcadeConfig().database == "grace"


def test_arcade_config_base_url():
    config = ArcadeConfig(host="db.example.com", port=9999)
    assert config.base_url == "http://db.example.com:9999"


def test_arcade_config_from_settings():
    """Verify ArcadeConfig.from_settings maps all fields correctly."""
    # Use a mock to avoid needing a real .env
    settings = MagicMock()
    settings.arcade_host = "arcade.local"
    settings.arcade_port = 3000
    settings.arcade_username = "admin"
    settings.arcade_password = "secret"
    settings.arcade_database = "testdb"
    settings.arcade_timeout = 60

    config = ArcadeConfig.from_settings(settings)
    assert config.host == "arcade.local"
    assert config.port == 3000
    assert config.username == "admin"
    assert config.password == "secret"
    assert config.database == "testdb"
    assert config.timeout == 60


def test_bare_constructor_honors_arcade_database_env(monkeypatch):
    """C1 defect #7: ArcadeClient() with no config must resolve the database from
    settings/env (ARCADE_DATABASE), not the hardcoded 'grace' literal — otherwise
    harnesses pointed at a grace_test sandbox silently hit the live GOLD graph."""
    from src.shared.config import get_settings

    monkeypatch.setenv("ARCADE_DATABASE", "grace_test")
    # GraceSettings requires database_url; supply one when the test env lacks it
    # (nothing connects — the client is never used).
    import os

    if not os.environ.get("DATABASE_URL"):
        monkeypatch.setenv(
            "DATABASE_URL", "postgresql+psycopg2://user@localhost:5432/grace_test"
        )
    get_settings.cache_clear()
    try:
        client = ArcadeClient()
        assert client.config.database == "grace_test"
    finally:
        get_settings.cache_clear()  # don't leak the env override to other tests


def test_bare_constructor_defaults_to_grace_when_settings_unavailable(monkeypatch):
    """Behavior preserved when settings cannot load: default database stays 'grace'."""
    def _boom():
        raise RuntimeError("settings unavailable")

    # F-022: the fallback default also honors ARCADE_DATABASE now; clear it so
    # this asserts the unset-env fallback (conftest sets it for isolation).
    monkeypatch.delenv("ARCADE_DATABASE", raising=False)
    monkeypatch.setattr("src.shared.config.get_settings", _boom)
    client = ArcadeClient()
    assert client.config.database == "grace"


# ===========================================================================
# API route tests (2)
# ===========================================================================

@pytest.mark.asyncio
async def test_graph_health_endpoint():
    """Mock ArcadeClient, verify 200 response from /api/graph/health."""
    from httpx import ASGITransport, AsyncClient as HttpxTestClient
    from src.api.main import app

    with patch("src.api.graph_routes._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.health_check.return_value = {
            "user": "root",
            "version": "26.3.2",
        }
        mock_get_client.return_value = mock_client

        transport = ASGITransport(app=app)
        async with HttpxTestClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/graph/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["server"]["version"] == "26.3.2"


@pytest.mark.asyncio
async def test_graph_health_endpoint_unavailable():
    """Mock connection error, verify 503 from /api/graph/health."""
    from httpx import ASGITransport, AsyncClient as HttpxTestClient
    from src.api.main import app

    with patch("src.api.graph_routes._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.health_check.side_effect = ConnectionError("unreachable")
        mock_get_client.return_value = mock_client

        transport = ASGITransport(app=app)
        async with HttpxTestClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/graph/health")
        assert resp.status_code == 503
