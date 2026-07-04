"""Tests for MboxAdapter (CP3)."""

from __future__ import annotations

import asyncio
import mailbox
from pathlib import Path

import pytest

from src.ingestion.adapters.mbox_adapter import MboxAdapter
from src.ingestion.models import MboxSourceConfig


def _create_test_mbox(path: Path) -> None:
    """Create a minimal mbox file with two messages."""
    mbox = mailbox.mbox(str(path))
    mbox.lock()

    msg1 = mailbox.mboxMessage()
    msg1["From"] = '"Alice Example" <alice@example.com>'
    msg1["To"] = "bob@example.com"
    msg1["Subject"] = "Test message 1"
    msg1["Message-ID"] = "<msg1@example.com>"
    msg1["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
    msg1.set_payload("Hello Bob")

    msg2 = mailbox.mboxMessage()
    msg2["From"] = "carol@example.com"
    msg2["To"] = "dave@example.com"
    msg2["Cc"] = "eve@example.com"
    msg2["Subject"] = "Test message 2"
    msg2["Message-ID"] = "<msg2@example.com>"
    msg2["Date"] = "Tue, 02 Jan 2024 14:00:00 +0000"
    msg2.set_payload("Hello Dave")

    mbox.add(msg1)
    mbox.add(msg2)
    mbox.unlock()
    mbox.close()


@pytest.fixture
def mbox_file(tmp_path):
    path = tmp_path / "test.mbox"
    _create_test_mbox(path)
    return path


class TestMboxAdapter:
    def test_connect_and_list(self, mbox_file):
        async def _run():
            adapter = MboxAdapter()
            config = MboxSourceConfig(file_path=str(mbox_file))
            await adapter.connect(config)
            keys = [k async for k in adapter.list_messages()]
            return keys

        keys = asyncio.run(_run())
        assert len(keys) == 2

    def test_parse_message_recipients(self, mbox_file):
        async def _run():
            adapter = MboxAdapter()
            config = MboxSourceConfig(file_path=str(mbox_file))
            await adapter.connect(config)
            return await adapter.parse_message("0")

        result = asyncio.run(_run())
        evt = result.event
        assert evt.sender_email == "alice@example.com"
        assert evt.sender_display_name == "Alice Example"
        assert len(evt.recipients) >= 1
        assert evt.recipients[0].email == "bob@example.com"
        assert evt.recipients[0].role == "to"

    def test_parse_message_body(self, mbox_file):
        async def _run():
            adapter = MboxAdapter()
            config = MboxSourceConfig(file_path=str(mbox_file))
            await adapter.connect(config)
            return await adapter.parse_message("0")

        result = asyncio.run(_run())
        assert "Hello Bob" in (result.event.body_plain or "")

    def test_checkpoint(self, mbox_file):
        async def _run():
            adapter = MboxAdapter()
            config = MboxSourceConfig(file_path=str(mbox_file))
            await adapter.connect(config)
            await adapter.parse_message("0")
            return adapter.checkpoint()

        cp = asyncio.run(_run())
        assert cp.checkpoint_type == "file_offset"
        assert cp.value == "1"
