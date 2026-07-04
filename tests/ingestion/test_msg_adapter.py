"""Tests for MsgAdapter (CP3)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from src.ingestion.adapters.msg_adapter import MsgAdapter
from src.ingestion.models import MsgSourceConfig


class TestMsgAdapter:
    def test_registration(self):
        from src.ingestion.adapter_registry import _REGISTRY
        assert "msg" in _REGISTRY

    def test_connect_raises_without_oxmsg(self, tmp_path):
        async def _run():
            adapter = MsgAdapter()
            config = MsgSourceConfig(directory_path=str(tmp_path))
            with patch("src.ingestion.adapters.msg_adapter._OXMSG_AVAILABLE", False):
                await adapter.connect(config)

        with pytest.raises(RuntimeError, match="python-oxmsg not installed"):
            asyncio.run(_run())

    def test_parse_message_with_mocked_oxmsg(self, tmp_path):
        msg_file = tmp_path / "test.msg"
        msg_file.write_bytes(b"dummy")

        mock_msg = MagicMock()
        mock_msg.recipients = []
        mock_msg.attachments = []
        mock_msg.sender_email = "sender@example.com"
        mock_msg.sender_name = "Sender"
        mock_msg.message_id = "<msg-test@example.com>"
        mock_msg.subject = "Test MSG"
        mock_msg.body = "Hello from MSG"
        mock_msg.sent_date = None

        async def _run():
            adapter = MsgAdapter()
            config = MsgSourceConfig(directory_path=str(tmp_path))
            with patch("src.ingestion.adapters.msg_adapter.oxmsg") as mock_oxmsg_mod:
                mock_oxmsg_mod.Message.return_value = mock_msg
                with patch("src.ingestion.adapters.msg_adapter._OXMSG_AVAILABLE", True):
                    await adapter.connect(config)
                    keys = [k async for k in adapter.list_messages()]
                    return await adapter.parse_message(keys[0])

        result = asyncio.run(_run())
        assert result.event.sender_email == "sender@example.com"
        assert result.event.body_plain == "Hello from MSG"

    def test_attachments_as_attachment_ref(self, tmp_path):
        msg_file = tmp_path / "test.msg"
        msg_file.write_bytes(b"dummy")

        mock_att = MagicMock()
        mock_att.filename = "report.pdf"
        mock_att.data = b"pdf-content"
        mock_att.mime_type = "application/pdf"

        mock_msg = MagicMock()
        mock_msg.recipients = []
        mock_msg.attachments = [mock_att]
        mock_msg.sender_email = "sender@example.com"
        mock_msg.sender_name = None
        mock_msg.message_id = "<att-test@example.com>"
        mock_msg.subject = "With attachment"
        mock_msg.body = ""
        mock_msg.sent_date = None

        async def _run():
            adapter = MsgAdapter()
            config = MsgSourceConfig(directory_path=str(tmp_path))
            with patch("src.ingestion.adapters.msg_adapter.oxmsg") as mock_oxmsg_mod:
                mock_oxmsg_mod.Message.return_value = mock_msg
                with patch("src.ingestion.adapters.msg_adapter._OXMSG_AVAILABLE", True):
                    await adapter.connect(config)
                    keys = [k async for k in adapter.list_messages()]
                    return await adapter.parse_message(keys[0])

        result = asyncio.run(_run())
        assert len(result.event.attachments) == 1
        assert result.event.attachments[0].filename == "report.pdf"
        assert result.event.attachments[0].docling_document_id is None
