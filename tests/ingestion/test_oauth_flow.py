"""Tests for OAuth2 initiation + callback flow (Chunk 57, CP9)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.ingestion.models import IngestionSource


@pytest.fixture
def test_app():
    from fastapi import FastAPI
    from src.api.ingestion_routes import ingestion_router

    app = FastAPI()
    app.include_router(ingestion_router)
    app.state.scheduler = None
    return app


@pytest.fixture
def mock_db():
    session = MagicMock()
    return session


@pytest.fixture
def client(test_app, mock_db):
    from src.shared.database import get_db

    def _override_db():
        yield mock_db

    test_app.dependency_overrides[get_db] = _override_db

    with TestClient(test_app) as c:
        yield c

    test_app.dependency_overrides.clear()


def _make_source(source_id=None, source_type="exchange"):
    src = MagicMock(spec=IngestionSource)
    src.id = source_id or uuid4()
    src.name = "test-exchange"
    src.source_type = source_type
    src.config_json = {"source_type": source_type, "tenant_id": "test-tenant"}
    src.segment = "insurance"
    src.enabled = True
    src.status = "pending"
    src.deleted_at = None
    return src


def test_oauth_init_generates_state(client, mock_db):
    """OAuth init returns authorize_url with embedded state."""
    source = _make_source()
    mock_db.query.return_value.filter_by.return_value.first.return_value = source

    with patch.dict("os.environ", {"INGESTION_PROVIDER_microsoft_CLIENT_ID": "test-client"}):
        resp = client.get(f"/api/ingestion/oauth/init/exchange?source_id={source.id}")

    assert resp.status_code == 200
    data = resp.json()
    assert "authorize_url" in data
    assert "state" in data
    assert data["state"] in data["authorize_url"]


def test_oauth_callback_csrf_missing_state_400(client, mock_db):
    """Callback with unknown state returns 400."""
    resp = client.post(
        "/api/ingestion/oauth/callback",
        json={
            "provider": "exchange",
            "code": "auth-code",
            "state": "nonexistent-state",
            "source_id": str(uuid4()),
        },
    )
    assert resp.status_code == 400
    assert "Invalid or expired" in resp.json()["detail"]


def test_oauth_callback_csrf_mismatched_source_id_400(client, mock_db):
    """Callback with mismatched source_id returns 400."""
    from src.api.ingestion_routes import _OAUTH_STATE

    state = str(uuid4())
    real_source_id = uuid4()
    _OAUTH_STATE[state] = (real_source_id, 9999999999.0)

    try:
        resp = client.post(
            "/api/ingestion/oauth/callback",
            json={
                "provider": "exchange",
                "code": "auth-code",
                "state": state,
                "source_id": str(uuid4()),  # Different source_id
            },
        )
        assert resp.status_code == 400
        assert "mismatch" in resp.json()["detail"]
    finally:
        _OAUTH_STATE.pop(state, None)


def test_oauth_callback_persists_refresh_token(client, mock_db):
    """Successful callback persists refresh token and flips status to ready."""
    from src.api.ingestion_routes import _OAUTH_STATE

    source_id = uuid4()
    source = _make_source(source_id, "exchange")
    state = str(uuid4())
    _OAUTH_STATE[state] = (source_id, 9999999999.0)

    mock_db.query.return_value.filter_by.return_value.first.return_value = source
    mock_db.commit = MagicMock()

    # Mock httpx token exchange
    mock_token_resp = MagicMock()
    mock_token_resp.status_code = 200
    mock_token_resp.json.return_value = {
        "access_token": "test-access",
        "refresh_token": "test-refresh-token",
    }

    with patch("src.api.ingestion_routes.httpx") as mock_httpx:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_token_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock()
        mock_httpx.AsyncClient.return_value = mock_client

        with patch("src.api.ingestion_routes._persist_env_key") as mock_persist:
            with patch.dict("os.environ", {
                "INGESTION_PROVIDER_microsoft_CLIENT_ID": "id",
                "INGESTION_PROVIDER_microsoft_CLIENT_SECRET": "secret",
            }):
                resp = client.post(
                    "/api/ingestion/oauth/callback",
                    json={
                        "provider": "exchange",
                        "code": "auth-code",
                        "state": state,
                        "source_id": str(source_id),
                    },
                )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    assert "REFRESH_TOKEN" in data["refresh_token_env"]


def test_oauth_callback_stamps_config_json(client, mock_db):
    """Callback stamps refresh_token_env onto source.config_json."""
    from src.api.ingestion_routes import _OAUTH_STATE

    source_id = uuid4()
    source = _make_source(source_id, "gmail")
    state = str(uuid4())
    _OAUTH_STATE[state] = (source_id, 9999999999.0)

    mock_db.query.return_value.filter_by.return_value.first.return_value = source

    mock_token_resp = MagicMock()
    mock_token_resp.status_code = 200
    mock_token_resp.json.return_value = {
        "access_token": "test-access",
        "refresh_token": "test-refresh",
    }

    with patch("src.api.ingestion_routes.httpx") as mock_httpx:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_token_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock()
        mock_httpx.AsyncClient.return_value = mock_client

        with patch("src.api.ingestion_routes._persist_env_key"):
            with patch.dict("os.environ", {
                "INGESTION_PROVIDER_google_CLIENT_ID": "id",
                "INGESTION_PROVIDER_google_CLIENT_SECRET": "secret",
            }):
                resp = client.post(
                    "/api/ingestion/oauth/callback",
                    json={
                        "provider": "gmail",
                        "code": "auth-code",
                        "state": state,
                        "source_id": str(source_id),
                    },
                )

    assert resp.status_code == 200
    # config_json should have been updated with refresh_token_env
    assert "refresh_token_env" in source.config_json


def test_oauth_init_unsupported_provider_422(client, mock_db):
    """OAuth init with unsupported provider returns 422."""
    resp = client.get(f"/api/ingestion/oauth/init/slack?source_id={uuid4()}")
    assert resp.status_code == 422


def test_oauth_callback_admin_key_enforcement(client, mock_db):
    """OAuth callback is a mutating route — admin-key gated when GRACE_ADMIN_KEY set.

    Note: This test verifies the callback route exists and accepts requests.
    Actual admin-key enforcement is handled by AuthMiddleware (not inline checks).
    """
    # With no state set, callback should return 400 (before reaching auth checks)
    resp = client.post(
        "/api/ingestion/oauth/callback",
        json={
            "provider": "exchange",
            "code": "code",
            "state": "bad",
            "source_id": str(uuid4()),
        },
    )
    assert resp.status_code == 400
