"""Tests for EmlAdapter (CP3)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.ingestion.adapters.eml_adapter import EmlAdapter
from src.ingestion.models import EmlSourceConfig


def _create_test_eml(path: Path, *, msg_id: str = "<test@example.com>") -> None:
    content = f"""\
From: "Sender Name" <sender@example.com>
To: recipient@example.com
Subject: Test EML
Message-ID: {msg_id}
Date: Wed, 03 Jan 2024 10:00:00 +0000
Content-Type: text/plain

Hello from EML"""
    path.write_text(content)


@pytest.fixture
def eml_dir(tmp_path):
    _create_test_eml(tmp_path / "msg1.eml", msg_id="<eml1@example.com>")
    _create_test_eml(tmp_path / "msg2.eml", msg_id="<eml2@example.com>")
    return tmp_path


class TestEmlAdapter:
    def test_connect_and_list(self, eml_dir):
        async def _run():
            adapter = EmlAdapter()
            config = EmlSourceConfig(directory_path=str(eml_dir))
            await adapter.connect(config)
            return [k async for k in adapter.list_messages()]

        keys = asyncio.run(_run())
        assert len(keys) == 2

    def test_parse_message(self, eml_dir):
        async def _run():
            adapter = EmlAdapter()
            config = EmlSourceConfig(directory_path=str(eml_dir))
            await adapter.connect(config)
            keys = [k async for k in adapter.list_messages()]
            return await adapter.parse_message(keys[0])

        result = asyncio.run(_run())
        evt = result.event
        assert evt.sender_email == "sender@example.com"
        assert evt.sender_display_name == "Sender Name"
        assert "Hello from EML" in (evt.body_plain or "")

    def test_recipients_as_recipient_objects(self, eml_dir):
        async def _run():
            adapter = EmlAdapter()
            config = EmlSourceConfig(directory_path=str(eml_dir))
            await adapter.connect(config)
            keys = [k async for k in adapter.list_messages()]
            return await adapter.parse_message(keys[0])

        result = asyncio.run(_run())
        assert len(result.event.recipients) >= 1
        assert result.event.recipients[0].role == "to"

    def test_checkpoint(self, eml_dir):
        async def _run():
            adapter = EmlAdapter()
            config = EmlSourceConfig(directory_path=str(eml_dir))
            await adapter.connect(config)
            keys = [k async for k in adapter.list_messages()]
            await adapter.parse_message(keys[0])
            return adapter.checkpoint()

        cp = asyncio.run(_run())
        assert cp.checkpoint_type == "file_offset"
