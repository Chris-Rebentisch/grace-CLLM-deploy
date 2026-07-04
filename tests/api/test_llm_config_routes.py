"""Tests for LLM config API endpoints.

Repo-config isolation (ISS-0001): the global autouse fixture in
``tests/conftest.py`` redirects ``_DISCOVERY_YAML`` / ``_ENV_PATH`` to tmp
copies, so the POSTs below never touch the real ``config/discovery.yaml``.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app)


def test_get_config():
    """Returns current config with masked key."""
    resp = client.get("/api/llm/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "provider" in data
    assert "model" in data
    assert "api_key_set" in data
    assert "api_key_preview" in data
    # Full key should never be returned
    assert "api_key" not in data or data.get("api_key_preview", "") == "" or "..." in data.get("api_key_preview", "")


def test_update_config(tmp_path):
    """POST updates yaml, GET reflects change."""
    # Note: this writes to the real discovery.yaml. We test it works and restore.
    from src.shared.llm_provider import read_llm_config_from_yaml

    original = read_llm_config_from_yaml()

    try:
        resp = client.post("/api/llm/config", json={
            "provider": "ollama",
            "model": "test-model:7b",
            "base_url": "http://localhost:11434",
            "timeout": 120,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["model"] == "test-model:7b"
        assert data["timeout"] == 120
    finally:
        # Restore original config
        from src.shared.llm_provider import write_llm_config_to_yaml
        write_llm_config_to_yaml(original)


def test_update_config_with_api_key():
    """API key written to .env and reflected in display config."""
    from src.shared.llm_provider import _ENV_PATH, update_env_api_key, _mask_api_key

    if not _ENV_PATH.exists():
        pytest.skip(".env not present in this checkout (unprovisioned environment)")

    original_env = _ENV_PATH.read_text()

    try:
        update_env_api_key("sk-test-12345678-abcdefg")

        # Verify key was written to .env
        env_content = _ENV_PATH.read_text()
        assert "LLM_API_KEY=sk-test-12345678-abcdefg" in env_content

        # Verify masking works (Observation 3 ratification: 4-char preview)
        masked = _mask_api_key("sk-test-12345678-abcdefg")
        assert masked == "sk-t..."
    finally:
        _ENV_PATH.write_text(original_env)


def test_test_config_healthy():
    """Mock provider, test endpoint returns healthy."""
    mock_health = {"healthy": True, "model_available": True, "provider": "ollama", "model": "test", "details": ""}

    with patch("src.api.llm_config_routes.get_provider") as mock_get:
        mock_provider = AsyncMock()
        mock_provider.health_check.return_value = mock_health
        mock_provider.generate.return_value = AsyncMock(
            text="Hello!", duration_ms=100,
        )
        mock_get.return_value = mock_provider

        resp = client.post("/api/llm/config/test", json={
            "provider": "ollama",
            "model": "qwen2.5:7b",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["healthy"] is True


def test_test_config_bad_key():
    """Mock ValueError, test endpoint returns healthy=False."""
    with patch("src.api.llm_config_routes.get_provider") as mock_get:
        mock_get.side_effect = ValueError("requires LLM_API_KEY")

        resp = client.post("/api/llm/config/test", json={
            "provider": "anthropic",
            "model": "claude-haiku-4-5-20250414",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["healthy"] is False
        assert "LLM_API_KEY" in data["error"]


def test_airgap_mode_round_trips_via_get_and_post():
    """D232 (Chunk 30): airgap_mode is exposed by GET and writable by POST."""
    from src.shared.llm_provider import (
        read_llm_config_from_yaml,
        write_llm_config_to_yaml,
    )

    original = read_llm_config_from_yaml()

    try:
        # GET surfaces the field
        resp_get = client.get("/api/llm/config")
        assert resp_get.status_code == 200
        body_get = resp_get.json()
        assert "airgap_mode" in body_get
        assert isinstance(body_get["airgap_mode"], bool)

        # POST flips it (use the local provider so the 422 guard does not fire)
        resp_post = client.post(
            "/api/llm/config",
            json={
                "provider": "ollama",
                "model": original.get("model", "qwen2.5:7b"),
                "base_url": original.get("base_url", "http://localhost:11434"),
                "timeout": original.get("timeout", 300),
                "airgap_mode": False,
            },
        )
        assert resp_post.status_code == 200, resp_post.text
        assert resp_post.json()["airgap_mode"] is False

        # GET reflects the new value
        resp_get2 = client.get("/api/llm/config")
        assert resp_get2.status_code == 200
        assert resp_get2.json()["airgap_mode"] is False
    finally:
        write_llm_config_to_yaml(original)


def test_airgap_mode_blocks_cloud_provider_with_422():
    """D232: airgap_mode=True + requires_api_key=True provider must 422."""
    from src.shared.llm_provider import (
        read_llm_config_from_yaml,
        write_llm_config_to_yaml,
    )

    original = read_llm_config_from_yaml()

    try:
        resp = client.post(
            "/api/llm/config",
            json={
                "provider": "anthropic",
                "model": "claude-haiku-4-5-20251001",
                "base_url": "https://api.anthropic.com/v1/messages",
                "timeout": 300,
                "airgap_mode": True,
            },
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["detail"]["error_type"] == "airgap_provider_conflict"
    finally:
        write_llm_config_to_yaml(original)


def test_get_registry():
    """Returns 3 providers with required fields."""
    resp = client.get("/api/llm/registry")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    ids = {e["id"] for e in data}
    assert ids == {"ollama", "anthropic", "openai"}
    for entry in data:
        assert "label" in entry
        assert "requires_api_key" in entry
        assert "popular_models" in entry
