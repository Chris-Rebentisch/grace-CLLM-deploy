"""Tests for IMAP live adapter (Chunk 57, CP6)."""

from __future__ import annotations

import email.message
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.ingestion.adapter_base import AdapterAuthError, AdapterResult
from src.ingestion.models import ImapSourceConfig, IngestionCheckpoint


def _make_imap_config(**overrides) -> ImapSourceConfig:
    defaults = {
        "source_type": "imap",
        "host": "mail.example.com",
        "port": 993,
        "username": "alice",
        "password": "secret",
        "use_ssl": True,
    }
    defaults.update(overrides)
    return ImapSourceConfig(**defaults)


def _make_mock_client(uidvalidity: int = 12345, uids: list[str] | None = None):
    """Build a mock aioimaplib IMAP4_SSL client."""
    client = AsyncMock()
    client.wait_hello_from_server = AsyncMock()

    login_resp = MagicMock()
    login_resp.result = "OK"
    client.login = AsyncMock(return_value=login_resp)

    select_resp = MagicMock()
    select_resp.result = "OK"
    select_resp.lines = [f"[UIDVALIDITY {uidvalidity}]"]
    client.select = AsyncMock(return_value=select_resp)

    search_resp = MagicMock()
    search_resp.result = "OK"
    search_resp.lines = [" ".join(uids or ["1", "2", "3"])]
    client.uid_search = AsyncMock(return_value=search_resp)

    # Build a minimal RFC822 message
    msg = email.message.EmailMessage()
    msg["From"] = "sender@example.com"
    msg["To"] = "recipient@example.com"
    msg["Subject"] = "Test message"
    msg["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
    msg["Message-ID"] = "<test-123@example.com>"
    msg.set_content("Hello, world!")
    raw_bytes = msg.as_bytes()

    fetch_resp = MagicMock()
    fetch_resp.result = "OK"
    fetch_resp.lines = [raw_bytes]
    client.uid = AsyncMock(return_value=fetch_resp)

    client.logout = AsyncMock()

    return client


@pytest.mark.asyncio
async def test_uid_tracking():
    """UID advances after parse_message and is reflected in checkpoint."""
    from src.ingestion.communications.adapters.imap_adapter import ImapAdapter

    adapter = ImapAdapter()
    mock_client = _make_mock_client(uidvalidity=42, uids=["10", "11"])

    config = _make_imap_config()
    with patch("src.ingestion.communications.adapters.imap_adapter.aioimaplib") as mock_lib:
        mock_lib.IMAP4_SSL.return_value = mock_client
        mock_lib.IMAP4.return_value = mock_client
        await adapter.connect(config)

    result = await adapter.parse_message("10")
    assert isinstance(result, AdapterResult)
    assert result.event.message_id == "<test-123@example.com>"

    cp = adapter.checkpoint()
    assert cp.checkpoint_type == "uid_validity"
    assert cp.value == "42:10"

    # Parse another
    await adapter.parse_message("11")
    cp2 = adapter.checkpoint()
    assert cp2.value == "42:11"


@pytest.mark.asyncio
async def test_uidvalidity_resync():
    """UIDVALIDITY change triggers last_uid reset to 0."""
    from src.ingestion.communications.adapters.imap_adapter import ImapAdapter

    adapter = ImapAdapter()
    adapter._uidvalidity = 100
    adapter._last_uid = 50

    mock_client = _make_mock_client(uidvalidity=200)
    config = _make_imap_config()

    with patch("src.ingestion.communications.adapters.imap_adapter.aioimaplib") as mock_lib:
        mock_lib.IMAP4_SSL.return_value = mock_client
        mock_lib.IMAP4.return_value = mock_client
        await adapter.connect(config)

    # UIDVALIDITY changed from 100 to 200 -> last_uid should be 0
    assert adapter._uidvalidity == 200
    assert adapter._last_uid == 0


@pytest.mark.asyncio
async def test_auth_precedence_env_var():
    """app_password_env takes precedence over inline password."""
    from src.ingestion.communications.adapters.imap_adapter import ImapAdapter

    adapter = ImapAdapter()
    config = _make_imap_config(
        password="inline_pass",
        app_password_env="TEST_IMAP_APP_PASSWORD",
    )

    with patch.dict("os.environ", {"TEST_IMAP_APP_PASSWORD": "env_pass"}):
        password = adapter._resolve_credentials(config)
    assert password == "env_pass"


@pytest.mark.asyncio
async def test_auth_fallback_inline():
    """Falls back to inline password when env var not set."""
    from src.ingestion.communications.adapters.imap_adapter import ImapAdapter

    adapter = ImapAdapter()
    config = _make_imap_config(password="inline_pass", app_password_env=None)
    password = adapter._resolve_credentials(config)
    assert password == "inline_pass"


@pytest.mark.asyncio
async def test_both_missing_raises_auth_error():
    """Missing both credentials raises AdapterAuthError."""
    from src.ingestion.communications.adapters.imap_adapter import ImapAdapter

    adapter = ImapAdapter()
    config = _make_imap_config(password="", app_password_env=None)

    with pytest.raises(AdapterAuthError) as exc_info:
        adapter._resolve_credentials(config)
    assert exc_info.value.error_class == "auth_invalid"


@pytest.mark.asyncio
async def test_parse_message_returns_adapter_result():
    """parse_message returns AdapterResult with populated .event."""
    from src.ingestion.communications.adapters.imap_adapter import ImapAdapter

    adapter = ImapAdapter()
    mock_client = _make_mock_client(uidvalidity=99)
    config = _make_imap_config()

    with patch("src.ingestion.communications.adapters.imap_adapter.aioimaplib") as mock_lib:
        mock_lib.IMAP4_SSL.return_value = mock_client
        mock_lib.IMAP4.return_value = mock_client
        await adapter.connect(config)

    result = await adapter.parse_message("5")
    assert isinstance(result, AdapterResult)
    assert result.event is not None
    assert result.event.sender_email == "sender@example.com"
    assert result.event.source_type == "imap"
    assert result.event.subject == "Test message"
    assert result.event.body_plain is not None


@pytest.mark.asyncio
async def test_compound_checkpoint_value_shape():
    """Checkpoint value is '{uidvalidity}:{last_uid}' compound format."""
    from src.ingestion.communications.adapters.imap_adapter import ImapAdapter

    adapter = ImapAdapter()
    adapter._uidvalidity = 777
    adapter._last_uid = 42

    cp = adapter.checkpoint()
    assert isinstance(cp, IngestionCheckpoint)
    assert cp.checkpoint_type == "uid_validity"
    assert cp.value == "777:42"
    # Verify compound shape is parseable
    parts = cp.value.split(":")
    assert len(parts) == 2
    assert int(parts[0]) == 777
    assert int(parts[1]) == 42
