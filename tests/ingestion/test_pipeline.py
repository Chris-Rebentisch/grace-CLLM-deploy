"""Tests for ingestion pipeline (CP5)."""

from __future__ import annotations

import asyncio
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.ingestion.models import IngestionRun, IngestionRunStatus, IngestionSource
from src.ingestion.pipeline import IngestionPipeline


def _mock_source(source_id=None, source_type="mbox"):
    """Create a mock IngestionSource ORM row."""
    src = MagicMock(spec=IngestionSource)
    src.id = source_id or uuid4()
    src.source_type = source_type
    src.config_json = {"source_type": source_type, "file_path": "/test.mbox"}
    src.segment = "insurance"
    src.enabled = True
    return src


def _mock_adapter():
    """Create a mock adapter with async methods."""
    adapter = AsyncMock()
    adapter.connect = AsyncMock()
    adapter.close = AsyncMock()

    async def _list_messages(*, limit=None):
        msgs = ["msg1", "msg2", "msg3"]
        if limit is not None:
            msgs = msgs[:limit]
        for m in msgs:
            yield m

    adapter.list_messages = _list_messages

    from src.ingestion.adapter_base import AdapterResult
    from src.ingestion.models import CommunicationEvent, IngestionCheckpoint

    evt = CommunicationEvent(
        source_id=uuid4(),
        message_id="<test@example.com>",
        sender_email="test@example.com",
        source_type="mbox",
    )
    adapter.parse_message = AsyncMock(return_value=AdapterResult(event=evt))
    adapter.checkpoint = MagicMock(
        return_value=IngestionCheckpoint(checkpoint_type="file_offset", value="3")
    )
    return adapter


class TestIngestionPipeline:
    def test_end_to_end(self):
        """End-to-end with mocked adapter + DB."""
        source_id = uuid4()
        source = _mock_source(source_id)
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = source

        runs_added = []

        def _add(obj):
            runs_added.append(obj)

        db.add = _add

        pipeline = IngestionPipeline(db)
        adapter = _mock_adapter()

        with patch("src.ingestion.pipeline.get_adapter", return_value=adapter):
            run_id = asyncio.run(pipeline.run(source_id))

        assert run_id is not None
        # runs_added includes 1 IngestionRun + N CommunicationEventRow objects (Chunk 56 CP5)
        assert len(runs_added) >= 1
        run = runs_added[0]
        assert run.status == IngestionRunStatus.completed.value

    def test_dry_run_skips_persistence(self):
        """Dry-run skips parse_message calls."""
        source_id = uuid4()
        source = _mock_source(source_id)
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = source

        runs_added = []
        db.add = lambda obj: runs_added.append(obj)

        pipeline = IngestionPipeline(db)
        adapter = _mock_adapter()

        with patch("src.ingestion.pipeline.get_adapter", return_value=adapter):
            asyncio.run(pipeline.run(source_id, dry_run=True))

        adapter.parse_message.assert_not_called()

    def test_limit_caps_iteration(self):
        """Limit caps message iteration."""
        source_id = uuid4()
        source = _mock_source(source_id)
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = source

        db.add = MagicMock()
        pipeline = IngestionPipeline(db)
        adapter = _mock_adapter()

        with patch("src.ingestion.pipeline.get_adapter", return_value=adapter):
            asyncio.run(pipeline.run(source_id, limit=1))

        assert adapter.parse_message.call_count == 1

    def test_error_path(self):
        """Error writes error_text and sets failed status."""
        source_id = uuid4()
        source = _mock_source(source_id)
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = source

        runs_added = []
        db.add = lambda obj: runs_added.append(obj)

        pipeline = IngestionPipeline(db)
        adapter = _mock_adapter()
        adapter.connect = AsyncMock(side_effect=RuntimeError("Connection failed"))

        with patch("src.ingestion.pipeline.get_adapter", return_value=adapter):
            with pytest.raises(RuntimeError, match="Connection failed"):
                asyncio.run(pipeline.run(source_id))

        run = runs_added[0]
        assert run.status == IngestionRunStatus.failed.value
        assert "Connection failed" in (run.error_text or "")

    def test_status_transitions(self):
        """pending → running → completed state transitions observed."""
        source_id = uuid4()
        source = _mock_source(source_id)
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = source

        status_log = []
        runs_added = []

        def _add(obj):
            runs_added.append(obj)

        def _commit():
            if runs_added:
                status_log.append(runs_added[0].status)

        db.add = _add
        db.commit = _commit

        pipeline = IngestionPipeline(db)
        adapter = _mock_adapter()

        with patch("src.ingestion.pipeline.get_adapter", return_value=adapter):
            asyncio.run(pipeline.run(source_id))

        assert "pending" in status_log
        assert "running" in status_log
        assert "completed" in status_log

    def test_cli_help(self):
        """CLI --help produces valid output."""
        import os, sys
        repo_root = str(__import__("pathlib").Path(__file__).resolve().parents[2])
        result = subprocess.run(
            [sys.executable, "-m", "src.ingestion", "--help"],
            capture_output=True,
            text=True,
            cwd=repo_root,
            env={**os.environ, "PYTHONPATH": repo_root},
        )
        assert result.returncode == 0
        assert "ingestion" in result.stdout.lower() or "usage" in result.stdout.lower()

    def test_cli_dry_run_flag_parsed(self):
        """--dry-run flag parsed correctly."""
        import os, sys
        repo_root = str(__import__("pathlib").Path(__file__).resolve().parents[2])
        result = subprocess.run(
            [sys.executable, "-m", "src.ingestion", "run", "--help"],
            capture_output=True,
            text=True,
            cwd=repo_root,
            env={**os.environ, "PYTHONPATH": repo_root},
        )
        assert result.returncode == 0
        assert "--dry-run" in result.stdout
