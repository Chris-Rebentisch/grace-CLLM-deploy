"""Tests for Gmail live adapter (Chunk 57, CP8)."""

from __future__ import annotations

import base64
import email.message
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from src.ingestion.adapter_base import (
    AdapterAuthError,
    AdapterCursorExpiredError,
    AdapterRateLimitError,
    AdapterResult,
)
from src.ingestion.models import GmailSourceConfig, IngestionCheckpoint


def _make_gmail_config(**overrides) -> GmailSourceConfig:
    defaults = {
        "source_type": "gmail",
        "refresh_token_env": "TEST_GMAIL_REFRESH_TOKEN",
    }
    defaults.update(overrides)
    return GmailSourceConfig(**defaults)


def _make_raw_message() -> str:
    """Build a base64url-encoded raw email message."""
    msg = email.message.EmailMessage()
    msg["From"] = "sender@gmail.com"
    msg["To"] = "recipient@gmail.com"
    msg["Subject"] = "Gmail Test"
    msg["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
    msg["Message-ID"] = "<test-gmail-123@gmail.com>"
    msg.set_content("Hello from Gmail!")
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def _make_mock_service(messages=None, history_id="12345"):
    """Build a mock Gmail API service."""
    service = MagicMock()

    # users().messages().list()
    list_resp = {
        "messages": messages or [{"id": "msg-1"}],
        "resultSizeEstimate": len(messages or [{"id": "msg-1"}]),
    }
    service.users.return_value.messages.return_value.list.return_value.execute.return_value = list_resp

    # users().messages().get() — raw format
    get_resp = {
        "id": "msg-1",
        "threadId": "thread-1",
        "historyId": history_id,
        "raw": _make_raw_message(),
    }
    service.users.return_value.messages.return_value.get.return_value.execute.return_value = get_resp

    # users().getProfile()
    service.users.return_value.getProfile.return_value.execute.return_value = {
        "historyId": history_id,
    }

    # users().history().list()
    service.users.return_value.history.return_value.list.return_value.execute.return_value = {
        "historyId": str(int(history_id) + 1),
        "history": [
            {"messagesAdded": [{"message": {"id": "msg-delta-1"}}]},
        ],
    }

    return service


@pytest.fixture
def env_vars():
    with patch.dict("os.environ", {
        "TEST_GMAIL_REFRESH_TOKEN": "fake-refresh-token",
        "INGESTION_PROVIDER_google_CLIENT_ID": "fake-client-id",
        "INGESTION_PROVIDER_google_CLIENT_SECRET": "fake-client-secret",
    }):
        yield


@pytest.mark.asyncio
async def test_history_id_persist(env_vars):
    """historyId is tracked and reflected in checkpoint."""
    from src.ingestion.communications.adapters.gmail_adapter import GmailAdapter

    adapter = GmailAdapter()
    config = _make_gmail_config()
    mock_service = _make_mock_service(history_id="99999")

    adapter._service = mock_service
    messages = []
    async for msg_id in adapter.list_messages(limit=1):
        messages.append(msg_id)

    assert len(messages) == 1
    cp = adapter.checkpoint()
    assert cp.checkpoint_type == "history_id"
    assert cp.value == "99999"


@pytest.mark.asyncio
async def test_404_410_resync(env_vars):
    """404/410 from history.list raises AdapterCursorExpiredError."""
    from src.ingestion.communications.adapters.gmail_adapter import GmailAdapter

    adapter = GmailAdapter()
    adapter._history_id = "old-history-id"
    config = _make_gmail_config()
    mock_service = MagicMock()
    mock_service.users.return_value.history.return_value.list.return_value.execute.side_effect = (
        Exception("HttpError 404 Not Found")
    )
    adapter._service = mock_service

    with pytest.raises(AdapterCursorExpiredError):
        async for _ in adapter.list_messages():
            pass


@pytest.mark.asyncio
async def test_token_refresh_failure():
    """Missing refresh token raises AdapterAuthError."""
    from src.ingestion.communications.adapters.gmail_adapter import GmailAdapter

    adapter = GmailAdapter()
    config = _make_gmail_config(refresh_token_env="NONEXISTENT_VAR")

    with patch.dict("os.environ", {
        "INGESTION_PROVIDER_google_CLIENT_ID": "id",
        "INGESTION_PROVIDER_google_CLIENT_SECRET": "secret",
    }):
        with pytest.raises(AdapterAuthError) as exc_info:
            await adapter.connect(config)
        assert exc_info.value.error_class == "auth_invalid"


@pytest.mark.asyncio
async def test_429_rate_limit(env_vars):
    """429 from messages.list raises AdapterRateLimitError."""
    from src.ingestion.communications.adapters.gmail_adapter import GmailAdapter

    adapter = GmailAdapter()
    mock_service = MagicMock()
    mock_service.users.return_value.messages.return_value.list.return_value.execute.side_effect = (
        Exception("HttpError 429 Too Many Requests")
    )
    adapter._service = mock_service

    with pytest.raises(AdapterRateLimitError):
        async for _ in adapter.list_messages():
            pass


@pytest.mark.asyncio
async def test_mock_gmail_api(env_vars):
    """Adapter works with mocked Gmail API service."""
    from src.ingestion.communications.adapters.gmail_adapter import GmailAdapter

    adapter = GmailAdapter()
    mock_service = _make_mock_service()
    adapter._service = mock_service

    messages = []
    async for msg_id in adapter.list_messages(limit=1):
        messages.append(msg_id)

    assert len(messages) == 1
    assert messages[0] == "msg-1"


@pytest.mark.asyncio
async def test_yields_adapter_result(env_vars):
    """parse_message returns AdapterResult with CommunicationEvent."""
    from src.ingestion.communications.adapters.gmail_adapter import GmailAdapter

    adapter = GmailAdapter()
    mock_service = _make_mock_service(history_id="54321")
    adapter._service = mock_service

    result = await adapter.parse_message("msg-1")
    assert isinstance(result, AdapterResult)
    assert result.event is not None
    assert result.event.sender_email == "sender@gmail.com"
    assert result.event.subject == "Gmail Test"
    assert result.event.source_type == "gmail"
    assert result.event.body_plain is not None
    assert result.checkpoint_value == "54321"
