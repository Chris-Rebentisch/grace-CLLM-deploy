"""Tests for PstAdapter and PstPreconverter (CP3)."""

from __future__ import annotations

import asyncio
import mailbox
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.ingestion.pst_preconverter import PstPreconverter
from src.ingestion.adapters.pst_adapter import PstAdapter
from src.ingestion.models import PstSourceConfig


class TestPstPreconverter:
    def test_argument_list_shape(self, tmp_path):
        pst_file = tmp_path / "test.pst"
        pst_file.write_bytes(b"dummy-pst")

        preconverter = PstPreconverter(
            pst_path=str(pst_file),
            source_id="00000000-0000-0000-0000-000000000001",
            converted_output_dir=str(tmp_path / "converted"),
        )

        with patch("src.ingestion.pst_preconverter.shutil.which", return_value="/usr/bin/readpst"):
            with patch("src.ingestion.pst_preconverter.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stderr="")
                preconverter.convert()

                args = mock_run.call_args
                cmd = args[0][0]
                assert cmd[0] == "readpst"
                assert "-r" in cmd
                assert "-o" in cmd
                assert args[1].get("shell", False) is False

    def test_missing_readpst_raises(self, tmp_path):
        pst_file = tmp_path / "test.pst"
        pst_file.write_bytes(b"dummy-pst")

        preconverter = PstPreconverter(
            pst_path=str(pst_file),
            source_id="00000000-0000-0000-0000-000000000002",
            converted_output_dir=str(tmp_path / "converted"),
        )

        with patch("src.ingestion.pst_preconverter.shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="readpst not found"):
                preconverter.convert()

    def test_persistent_directory_creation(self, tmp_path):
        pst_file = tmp_path / "test.pst"
        pst_file.write_bytes(b"dummy-pst")
        out_dir = tmp_path / "converted"

        preconverter = PstPreconverter(
            pst_path=str(pst_file),
            source_id="00000000-0000-0000-0000-000000000003",
            converted_output_dir=str(out_dir),
        )

        with patch("src.ingestion.pst_preconverter.shutil.which", return_value="/usr/bin/readpst"):
            with patch("src.ingestion.pst_preconverter.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stderr="")
                result_dir = preconverter.convert()
                assert Path(result_dir).exists()

    def test_disk_space_precheck(self, tmp_path):
        pst_file = tmp_path / "test.pst"
        pst_file.write_bytes(b"x" * 1000)

        preconverter = PstPreconverter(
            pst_path=str(pst_file),
            source_id="00000000-0000-0000-0000-000000000004",
            converted_output_dir=str(tmp_path / "converted"),
        )

        mock_usage = MagicMock()
        mock_usage.free = 100
        mock_usage.total = 1000

        with patch("src.ingestion.pst_preconverter.shutil.which", return_value="/usr/bin/readpst"):
            with patch("src.ingestion.pst_preconverter.shutil.disk_usage", return_value=mock_usage):
                with patch("src.ingestion.pst_preconverter.subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(returncode=0, stderr="")
                    preconverter.convert()


class TestPstAdapter:
    def test_pst_adapter_delegates_to_mbox(self, tmp_path):
        pst_file = tmp_path / "test.pst"
        pst_file.write_bytes(b"dummy-pst")

        out_dir = tmp_path / "converted" / "fake-source-id"
        out_dir.mkdir(parents=True)
        mbox_path = out_dir / "output.mbox"

        mbox_obj = mailbox.mbox(str(mbox_path))
        mbox_obj.lock()
        msg = mailbox.mboxMessage()
        msg["From"] = "test@example.com"
        msg["To"] = "dest@example.com"
        msg["Subject"] = "PST test"
        msg["Message-ID"] = "<pst-test@example.com>"
        msg["Date"] = "Thu, 04 Jan 2024 08:00:00 +0000"
        msg.set_payload("From PST")
        mbox_obj.add(msg)
        mbox_obj.unlock()
        mbox_obj.close()

        async def _run():
            adapter = PstAdapter()
            config = PstSourceConfig(
                file_path=str(pst_file),
                converted_output_dir=str(tmp_path / "converted"),
            )

            with patch("src.ingestion.adapters.pst_adapter.PstPreconverter") as mock_pre:
                mock_instance = MagicMock()
                mock_instance.convert.return_value = out_dir
                mock_pre.return_value = mock_instance

                await adapter.connect(config)
                keys = [k async for k in adapter.list_messages()]
                assert len(keys) == 1
                result = await adapter.parse_message(keys[0])
                return result

        result = asyncio.run(_run())
        assert result.event.source_type == "mbox"
