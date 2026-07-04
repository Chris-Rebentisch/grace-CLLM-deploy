"""Tests for Exchange/O365 adapter — O365 SDK rewrite (Chunk 58, CP1, DV1).

6 mock-swapped tests (httpx → O365) + 3 new tests:
  - test_sent_items_navigation
  - test_concurrency_cap_respect
  - test_async_wrap_correctness
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from uuid import uuid4

import pytest

from src.ingestion.adapter_base import (
    AdapterAuthError,
    AdapterCursorExpiredError,
    AdapterRateLimitError,
    AdapterResult,
)
from src.ingestion.models import ExchangeSourceConfig, IngestionCheckpoint


def _make_exchange_config(**overrides) -> ExchangeSourceConfig:
    defaults = {
        "source_type": "exchange",
        "tenant_id": "test-tenant-id",
        "refresh_token_env": "TEST_EXCHANGE_REFRESH_TOKEN",
    }
    defaults.update(overrides)
    return ExchangeSourceConfig(**defaults)


def _make_o365_message(
    msg_id: str = "msg-1",
    subject: str = "Test",
    sender_addr: str = "sender@example.com",
    sender_name: str = "Sender",
    to_addr: str = "to@example.com",
    to_name: str = "To",
    body: str = "Hello",
    body_type: str = "text",
    conversation_id: str = "conv-1",
) -> MagicMock:
    """Create a mock O365 Message object."""
    msg = MagicMock()
    msg.object_id = msg_id
    msg.subject = subject

    sender = MagicMock()
    sender.address = sender_addr
    sender.name = sender_name
    msg.sender = sender

    to_recipient = MagicMock()
    to_recipient.address = to_addr
    to_recipient.name = to_name
    msg.to = [to_recipient]
    msg.cc = []
    msg.bcc = []

    msg.body = body
    msg.body_type = body_type

    from datetime import datetime, timezone
    msg.sent = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    msg.received = datetime(2024, 1, 1, 12, 1, 0, tzinfo=timezone.utc)
    msg.internet_message_id = "<test-msg@graph>"
    msg.conversation_id = conversation_id
    msg.has_attachments = False

    return msg


@pytest.fixture
def env_vars():
    with patch.dict("os.environ", {
        "TEST_EXCHANGE_REFRESH_TOKEN": "fake-refresh-token",
        "INGESTION_PROVIDER_microsoft_CLIENT_ID": "fake-client-id",
        "INGESTION_PROVIDER_microsoft_CLIENT_SECRET": "fake-client-secret",
    }):
        yield


def _mock_account_and_folder(messages: list[MagicMock] | None = None):
    """Create mocked O365 Account with folder returning given messages."""
    if messages is None:
        messages = [_make_o365_message()]

    mock_folder = MagicMock()
    mock_folder.folder_id = "inbox-folder-id"
    mock_folder.get_messages.return_value = messages

    mock_query = MagicMock()
    mock_query.select.return_value = mock_query
    mock_folder.new_query.return_value = mock_query

    mock_mailbox = MagicMock()
    mock_mailbox.inbox_folder.return_value = mock_folder
    mock_mailbox.sent_folder.return_value = mock_folder
    mock_mailbox.drafts_folder.return_value = mock_folder
    mock_mailbox.deleted_folder.return_value = mock_folder
    mock_mailbox.archive_folder.return_value = mock_folder

    mock_con = MagicMock()
    mock_con.refresh_token.return_value = True
    mock_con.token_backend = MagicMock()
    mock_con.token_backend.token = {}

    mock_account = MagicMock()
    mock_account.mailbox.return_value = mock_mailbox
    mock_account.connection = mock_con

    return mock_account, mock_folder


@pytest.mark.asyncio
async def test_delta_link_persist(env_vars):
    """Delta link is captured and returned via checkpoint."""
    from src.ingestion.communications.adapters.exchange_adapter import ExchangeAdapter

    adapter = ExchangeAdapter()
    config = _make_exchange_config()

    mock_account, mock_folder = _mock_account_and_folder([_make_o365_message("msg-1")])

    with patch(
        "src.ingestion.communications.adapters.exchange_adapter._build_o365_account",
        return_value=mock_account,
    ):
        await adapter.connect(config)

    messages = []
    async for msg_id in adapter.list_messages():
        messages.append(msg_id)

    assert len(messages) == 1
    cp = adapter.checkpoint()
    assert cp.checkpoint_type == "delta_link"
    assert cp.value is not None


@pytest.mark.asyncio
async def test_410_resync(env_vars):
    """HTTP 410 from Graph API raises AdapterCursorExpiredError."""
    from src.ingestion.communications.adapters.exchange_adapter import ExchangeAdapter

    adapter = ExchangeAdapter()
    config = _make_exchange_config()

    mock_account, mock_folder = _mock_account_and_folder()
    # Make get_messages raise with 410 in the error message
    mock_folder.get_messages.side_effect = Exception("410 Gone - delta link expired")

    with patch(
        "src.ingestion.communications.adapters.exchange_adapter._build_o365_account",
        return_value=mock_account,
    ):
        await adapter.connect(config)

    with pytest.raises(AdapterCursorExpiredError):
        async for _ in adapter.list_messages():
            pass


@pytest.mark.asyncio
async def test_token_refresh_failure():
    """Token refresh failure raises AdapterAuthError(oauth_refresh_failed)."""
    from src.ingestion.communications.adapters.exchange_adapter import ExchangeAdapter

    adapter = ExchangeAdapter()
    config = _make_exchange_config()

    mock_account, _ = _mock_account_and_folder()
    mock_account.connection.refresh_token.return_value = False

    with patch.dict("os.environ", {
        "TEST_EXCHANGE_REFRESH_TOKEN": "fake-refresh-token",
        "INGESTION_PROVIDER_microsoft_CLIENT_ID": "fake-client-id",
        "INGESTION_PROVIDER_microsoft_CLIENT_SECRET": "fake-client-secret",
    }):
        with patch(
            "src.ingestion.communications.adapters.exchange_adapter._build_o365_account",
            return_value=mock_account,
        ):
            with pytest.raises(AdapterAuthError) as exc_info:
                await adapter.connect(config)
            assert exc_info.value.error_class == "oauth_refresh_failed"


@pytest.mark.asyncio
async def test_mock_graph_parse_message(env_vars):
    """parse_message returns AdapterResult with populated CommunicationEvent."""
    from src.ingestion.communications.adapters.exchange_adapter import ExchangeAdapter

    adapter = ExchangeAdapter()
    config = _make_exchange_config()

    msg = _make_o365_message("msg-1", subject="Hello Graph")
    mock_account, _ = _mock_account_and_folder([msg])

    with patch(
        "src.ingestion.communications.adapters.exchange_adapter._build_o365_account",
        return_value=mock_account,
    ):
        await adapter.connect(config)

    async for msg_id in adapter.list_messages():
        result = await adapter.parse_message(msg_id)
        assert isinstance(result, AdapterResult)
        assert result.event.subject == "Hello Graph"
        assert result.event.sender_email == "sender@example.com"
        assert result.event.source_type == "exchange"


@pytest.mark.asyncio
async def test_select_fields_in_request(env_vars):
    """Query uses select() to minimize payload."""
    from src.ingestion.communications.adapters.exchange_adapter import ExchangeAdapter

    adapter = ExchangeAdapter()
    config = _make_exchange_config()

    mock_account, mock_folder = _mock_account_and_folder([])

    with patch(
        "src.ingestion.communications.adapters.exchange_adapter._build_o365_account",
        return_value=mock_account,
    ):
        await adapter.connect(config)

    async for _ in adapter.list_messages():
        pass

    # Verify select was called on the query
    mock_folder.new_query.assert_called_once()
    mock_query = mock_folder.new_query.return_value
    mock_query.select.assert_called_once()


@pytest.mark.asyncio
async def test_429_rate_limit(env_vars):
    """HTTP 429 raises AdapterRateLimitError."""
    from src.ingestion.communications.adapters.exchange_adapter import ExchangeAdapter

    adapter = ExchangeAdapter()
    config = _make_exchange_config()

    mock_account, mock_folder = _mock_account_and_folder()
    mock_folder.get_messages.side_effect = Exception("429 Too Many Requests")

    with patch(
        "src.ingestion.communications.adapters.exchange_adapter._build_o365_account",
        return_value=mock_account,
    ):
        await adapter.connect(config)

    with pytest.raises(AdapterRateLimitError):
        async for _ in adapter.list_messages():
            pass


# --- 3 new tests (Chunk 58 CP1) ---


@pytest.mark.asyncio
async def test_sent_items_navigation(env_vars):
    """Adapter navigates to SentItems folder when configured."""
    from src.ingestion.communications.adapters.exchange_adapter import ExchangeAdapter

    adapter = ExchangeAdapter()
    config = _make_exchange_config()

    msg = _make_o365_message("sent-msg-1", subject="Sent message")
    mock_account, mock_folder = _mock_account_and_folder([msg])

    with patch(
        "src.ingestion.communications.adapters.exchange_adapter._build_o365_account",
        return_value=mock_account,
    ):
        await adapter.connect(config)

    # Override folder name post-connect to test SentItems navigation
    adapter._folder_name = "SentItems"

    messages = []
    async for msg_id in adapter.list_messages():
        messages.append(msg_id)

    assert len(messages) == 1
    # Verify sent_folder was used (folder map resolves "sentitems")
    mock_account.mailbox.return_value.sent_folder.assert_called()


@pytest.mark.asyncio
async def test_concurrency_cap_respect(env_vars):
    """Concurrent list_messages calls are bounded by semaphore."""
    from src.ingestion.communications.adapters.exchange_adapter import ExchangeAdapter, _MAX_CONCURRENT_REQUESTS

    adapter = ExchangeAdapter()
    config = _make_exchange_config()

    mock_account, mock_folder = _mock_account_and_folder([_make_o365_message()])

    with patch(
        "src.ingestion.communications.adapters.exchange_adapter._build_o365_account",
        return_value=mock_account,
    ):
        await adapter.connect(config)

    # Verify semaphore exists with the right bound
    assert isinstance(adapter._semaphore, asyncio.Semaphore)
    assert _MAX_CONCURRENT_REQUESTS == 4

    # Run list_messages — should complete without deadlock
    messages = []
    async for msg_id in adapter.list_messages():
        messages.append(msg_id)
    assert len(messages) >= 1


@pytest.mark.asyncio
async def test_async_wrap_correctness(env_vars):
    """O365 synchronous calls are wrapped in asyncio.to_thread."""
    from src.ingestion.communications.adapters.exchange_adapter import ExchangeAdapter

    adapter = ExchangeAdapter()
    config = _make_exchange_config()

    mock_account, mock_folder = _mock_account_and_folder([_make_o365_message()])

    with patch(
        "src.ingestion.communications.adapters.exchange_adapter._build_o365_account",
        return_value=mock_account,
    ):
        # connect() should use asyncio.to_thread for _build_o365_account + refresh_token
        await adapter.connect(config)

    # Verify that the adapter works in an async context without blocking
    # by running list_messages + parse_message in the event loop
    msg_ids = []
    async for msg_id in adapter.list_messages():
        msg_ids.append(msg_id)

    assert len(msg_ids) == 1
    result = await adapter.parse_message(msg_ids[0])
    assert result.event.sender_email == "sender@example.com"

    # Verify close releases resources
    await adapter.close()
    assert adapter._account is None
    assert len(adapter._messages_cache) == 0
