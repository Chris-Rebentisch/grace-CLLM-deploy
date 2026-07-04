"""CP1 tests: generate_vision payload shape for all three providers (D500).

Verifies the transport format each provider uses to send images to the LLM:
- Ollama: base64 `images` array in /api/chat messages
- Anthropic: `image` content blocks with `base64` source type
- OpenAI-compatible: `image_url` content blocks with data URI
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

def _make_tiny_jpeg() -> bytes:
    """Create a valid 1x1 JPEG image for testing."""
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (1, 1), color="red").save(buf, format="JPEG")
    return buf.getvalue()

_TINY_JPEG = _make_tiny_jpeg()


@pytest.fixture()
def _mock_record_llm_call():
    """Patch record_llm_call context manager to no-op."""
    mock_ctx = MagicMock()
    mock_ctx.set_input_tokens = MagicMock()
    mock_ctx.set_output_tokens = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    with patch("src.analytics.llm_instrumentation.record_llm_call", return_value=mock_ctx):
        yield


def test_ollama_vision_payload_shape(_mock_record_llm_call):
    """Verify Ollama generate_vision sends base64 images array in /api/chat."""
    import base64

    from src.discovery.ollama_client import OllamaProvider

    provider = OllamaProvider(model="qwen2.5:7b", base_url="http://localhost:11434")

    captured_payload = {}

    async def mock_post(url, json=None, **kwargs):
        captured_payload.update(json or {})
        captured_payload["_url"] = url
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={
            "message": {"content": "test response"},
            "model": "qwen2.5-vl:32b",
            "prompt_eval_count": 10,
            "eval_count": 5,
            "total_duration": 1_000_000_000,
        })
        return resp

    with patch("src.shared.llm_provider.read_vision_config_from_yaml", return_value={"model": "qwen2.5-vl:7b", "enabled": True}):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = asyncio.run(
                provider.generate_vision("Describe this image", [_TINY_JPEG])
            )

    # Verify the payload shape
    assert "/api/chat" in captured_payload.get("_url", ""), "Should POST to /api/chat"
    messages = captured_payload.get("messages", [])
    assert len(messages) == 1, "Should have one user message"
    assert "images" in messages[0], "Message should contain 'images' array"
    assert isinstance(messages[0]["images"], list), "images should be a list"
    assert len(messages[0]["images"]) == 1, "Should have one image"
    # Verify it's valid base64
    decoded = base64.b64decode(messages[0]["images"][0])
    assert len(decoded) > 0, "Decoded image should have content"


def test_anthropic_vision_payload_shape(_mock_record_llm_call):
    """Verify Anthropic generate_vision sends image content blocks with base64 source."""
    from src.shared.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider(api_key="test-key", model="claude-haiku-4-5-20251001")

    captured_payload = {}

    async def mock_post(url, json=None, headers=None, **kwargs):
        captured_payload.update(json or {})
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={
            "content": [{"text": "test response"}],
            "model": "claude-haiku-4-5-20251001",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        })
        return resp

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = asyncio.run(
            provider.generate_vision("Describe this image", [_TINY_JPEG])
        )

    # Verify content blocks
    messages = captured_payload.get("messages", [])
    assert len(messages) == 1
    content = messages[0].get("content", [])
    image_blocks = [b for b in content if b.get("type") == "image"]
    text_blocks = [b for b in content if b.get("type") == "text"]
    assert len(image_blocks) == 1, "Should have one image content block"
    assert image_blocks[0]["source"]["type"] == "base64", "Source type should be base64"
    assert "data" in image_blocks[0]["source"], "Should have base64 data"
    assert "media_type" in image_blocks[0]["source"], "Should have media_type"
    assert len(text_blocks) >= 1, "Should have at least one text block"


def test_openai_vision_payload_shape(_mock_record_llm_call):
    """Verify OpenAI generate_vision sends image_url content blocks with data URI."""
    from src.shared.openai_provider import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider(
        api_key="test-key", model="gpt-4o", base_url="https://api.openai.com/v1"
    )

    captured_payload = {}

    async def mock_post(url, json=None, headers=None, **kwargs):
        captured_payload.update(json or {})
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={
            "choices": [{"message": {"content": "test response"}}],
            "model": "gpt-4o",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        })
        return resp

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = asyncio.run(
            provider.generate_vision("Describe this image", [_TINY_JPEG])
        )

    # Verify content blocks
    messages = captured_payload.get("messages", [])
    assert len(messages) == 1
    content = messages[0].get("content", [])
    image_url_blocks = [b for b in content if b.get("type") == "image_url"]
    text_blocks = [b for b in content if b.get("type") == "text"]
    assert len(image_url_blocks) == 1, "Should have one image_url content block"
    url = image_url_blocks[0]["image_url"]["url"]
    assert url.startswith("data:image/"), "URL should be a data URI"
    assert ";base64," in url, "URL should contain base64 marker"
    assert len(text_blocks) >= 1, "Should have at least one text block"
