"""Tests for src/extraction/extraction_bridge.py (CP4, D508/D510/D511/D512)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.extraction.email_composer import CommunicationEventRow
from src.extraction.extraction_bridge import (
    _build_argparser,
    _is_privileged,
    _get_temporal_fallback,
    _select_pending_emails,
    run_bridge,
)


class TestArgParser:
    def test_run_subcommand(self):
        parser = _build_argparser()
        args = parser.parse_args(["run", "--limit", "5", "--dry-run"])
        assert args.command == "run"
        assert args.limit == 5
        assert args.dry_run is True

    def test_skip_privileged_flag(self):
        parser = _build_argparser()
        args = parser.parse_args(["run", "--skip-privileged"])
        assert args.skip_privileged is True


class TestHelpers:
    def test_is_privileged_true(self):
        assert _is_privileged("|privileged|pii_dense|") is True

    def test_is_privileged_false(self):
        assert _is_privileged("|pii_dense|") is False

    def test_is_privileged_none(self):
        assert _is_privileged(None) is False

    def test_temporal_fallback_sent_at(self):
        ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
        row = {"sent_at": ts, "received_at": None, "ingested_at": None}
        assert _get_temporal_fallback(row) == ts

    def test_temporal_fallback_received_at(self):
        ts = datetime(2026, 1, 2, tzinfo=timezone.utc)
        row = {"sent_at": None, "received_at": ts, "ingested_at": None}
        assert _get_temporal_fallback(row) == ts

    def test_temporal_fallback_ingested_at(self):
        ts = datetime(2026, 1, 3, tzinfo=timezone.utc)
        row = {"sent_at": None, "received_at": None, "ingested_at": ts}
        assert _get_temporal_fallback(row) == ts


@pytest.fixture()
def mock_session():
    """Mocked SQLAlchemy session."""
    session = MagicMock()
    return session


@pytest.fixture()
def mock_pipeline():
    """Mocked ExtractionPipeline."""
    pipeline = MagicMock()
    pipeline.extract_document = AsyncMock()
    return pipeline


def _make_row(**overrides):
    """Build a mock communication_events row dict."""
    defaults = {
        "id": str(uuid.uuid4()),
        "message_id": f"test-{uuid.uuid4().hex[:8]}@example.com",
        "sender_display_name": "Test User",
        "sender_email": "test@example.com",
        "subject": "Test Subject",
        "sent_at": datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc),
        "received_at": datetime(2026, 3, 15, 10, 1, 0, tzinfo=timezone.utc),
        "ingested_at": datetime(2026, 3, 15, 10, 2, 0, tzinfo=timezone.utc),
        "body_plain": "Test email body content.",
        "sensitivity_tags": None,
    }
    defaults.update(overrides)
    return defaults


def _make_batch_mock(grace_ids: list[str] | None = None):
    """Create a mock ExtractionBatch."""
    batch = MagicMock()
    entities = []
    for gid in (grace_ids or []):
        entity = MagicMock()
        entity.resolved_grace_id = gid
        entities.append(entity)
    batch.entities = entities
    return batch


class TestDryRun:
    @pytest.mark.asyncio
    async def test_dry_run(self):
        """--dry-run logs count but writes nothing."""
        mock_rows = [_make_row() for _ in range(3)]
        with (
            patch("src.extraction.extraction_bridge.get_engine") as mock_engine,
            patch("src.extraction.extraction_bridge._select_pending_emails", return_value=mock_rows),
        ):
            mock_engine.return_value = MagicMock()
            result = await run_bridge(dry_run=True, limit=10)

        assert result["dry_run"] is True
        assert result["pending"] == 3
        assert result["processed"] == 0


class TestLimit:
    @pytest.mark.asyncio
    async def test_limit(self):
        """--limit 3 processes at most 3 rows."""
        mock_rows = [_make_row() for _ in range(3)]
        batch = _make_batch_mock(["gid-1"])

        with (
            patch("src.extraction.extraction_bridge.get_engine") as mock_engine,
            patch("src.extraction.extraction_bridge._select_pending_emails", return_value=mock_rows),
            patch("src.extraction.extraction_bridge.ExtractionPipeline") as mock_pipe_cls,
            patch("src.extraction.extraction_bridge.get_arcade_client") as mock_arcade,
            patch("src.extraction.extraction_bridge._load_skip_privileged_config", return_value=False),
        ):
            mock_engine.return_value = MagicMock()
            pipe_inst = mock_pipe_cls.return_value
            pipe_inst.extract_document = AsyncMock(return_value=batch)
            mock_arcade.return_value.command = AsyncMock()

            result = await run_bridge(limit=3)

        assert pipe_inst.extract_document.call_count == 3
        assert result["success"] == 3


class TestSkipPrivileged:
    @pytest.mark.asyncio
    async def test_skip_privileged(self):
        """Emails with |privileged| tag get extraction_status='skipped'."""
        priv_row = _make_row(sensitivity_tags="|privileged|pii_dense|")
        normal_row = _make_row(sensitivity_tags=None)
        batch = _make_batch_mock(["gid-1"])

        with (
            patch("src.extraction.extraction_bridge.get_engine") as mock_engine,
            patch("src.extraction.extraction_bridge._select_pending_emails", return_value=[priv_row, normal_row]),
            patch("src.extraction.extraction_bridge.ExtractionPipeline") as mock_pipe_cls,
            patch("src.extraction.extraction_bridge.get_arcade_client") as mock_arcade,
            patch("src.extraction.extraction_bridge._load_skip_privileged_config", return_value=True),
        ):
            mock_engine.return_value = MagicMock()
            pipe_inst = mock_pipe_cls.return_value
            pipe_inst.extract_document = AsyncMock(return_value=batch)
            mock_arcade.return_value.command = AsyncMock()

            result = await run_bridge(limit=10)

        assert result["skipped"] == 1
        assert result["success"] == 1
        # Only the non-privileged email should reach extraction
        assert pipe_inst.extract_document.call_count == 1


class TestErrorPerEmailContinues:
    @pytest.mark.asyncio
    async def test_error_per_email_continues(self):
        """One email's extraction failure doesn't abort; remaining emails process."""
        rows = [_make_row() for _ in range(3)]
        batch = _make_batch_mock(["gid-1"])

        with (
            patch("src.extraction.extraction_bridge.get_engine") as mock_engine,
            patch("src.extraction.extraction_bridge._select_pending_emails", return_value=rows),
            patch("src.extraction.extraction_bridge.ExtractionPipeline") as mock_pipe_cls,
            patch("src.extraction.extraction_bridge.get_arcade_client") as mock_arcade,
            patch("src.extraction.extraction_bridge._load_skip_privileged_config", return_value=False),
        ):
            mock_engine.return_value = MagicMock()
            pipe_inst = mock_pipe_cls.return_value
            # First call raises, next two succeed
            pipe_inst.extract_document = AsyncMock(
                side_effect=[RuntimeError("test error"), batch, batch]
            )
            mock_arcade.return_value.command = AsyncMock()

            result = await run_bridge(limit=10)

        assert result["error"] == 1
        assert result["success"] == 2


class TestEvidenceOriginOnVertex:
    @pytest.mark.asyncio
    async def test_evidence_origin_on_vertex(self):
        """Bridge calls extract_document with evidence_origin='communication'."""
        rows = [_make_row()]
        batch = _make_batch_mock(["gid-1"])

        with (
            patch("src.extraction.extraction_bridge.get_engine") as mock_engine,
            patch("src.extraction.extraction_bridge._select_pending_emails", return_value=rows),
            patch("src.extraction.extraction_bridge.ExtractionPipeline") as mock_pipe_cls,
            patch("src.extraction.extraction_bridge.get_arcade_client") as mock_arcade,
            patch("src.extraction.extraction_bridge._load_skip_privileged_config", return_value=False),
        ):
            mock_engine.return_value = MagicMock()
            pipe_inst = mock_pipe_cls.return_value
            pipe_inst.extract_document = AsyncMock(return_value=batch)
            mock_arcade.return_value.command = AsyncMock()

            await run_bridge(limit=1)

        call_kwargs = pipe_inst.extract_document.call_args[1]
        assert call_kwargs["evidence_origin"] == "communication"
        assert call_kwargs["document_id"].startswith("email:")


class TestIdempotentRerun:
    @pytest.mark.asyncio
    async def test_idempotent_rerun(self):
        """Running bridge again on extracted emails processes zero rows."""
        # Select returns empty (all already extracted)
        with (
            patch("src.extraction.extraction_bridge.get_engine") as mock_engine,
            patch("src.extraction.extraction_bridge._select_pending_emails", return_value=[]),
            patch("src.extraction.extraction_bridge._load_skip_privileged_config", return_value=False),
            patch("src.extraction.extraction_bridge.ExtractionPipeline"),
            patch("src.extraction.extraction_bridge.get_arcade_client"),
        ):
            mock_engine.return_value = MagicMock()
            result = await run_bridge(limit=10)

        assert result["success"] == 0
        assert result["error"] == 0


class TestFallbackStamp:
    @pytest.mark.asyncio
    async def test_fallback_stamp(self):
        """Vertices receive valid_from from sent_at via fallback chain."""
        ts = datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc)
        rows = [_make_row(sent_at=ts)]
        batch = _make_batch_mock(["gid-abc"])

        with (
            patch("src.extraction.extraction_bridge.get_engine") as mock_engine,
            patch("src.extraction.extraction_bridge._select_pending_emails", return_value=rows),
            patch("src.extraction.extraction_bridge.ExtractionPipeline") as mock_pipe_cls,
            patch("src.extraction.extraction_bridge.get_arcade_client") as mock_arcade_cls,
            patch("src.extraction.extraction_bridge._load_skip_privileged_config", return_value=False),
        ):
            mock_engine.return_value = MagicMock()
            pipe_inst = mock_pipe_cls.return_value
            pipe_inst.extract_document = AsyncMock(return_value=batch)
            arcade_inst = mock_arcade_cls.return_value
            # D550 — the stamp must go through execute_cypher (the real DML method);
            # the bridge previously called a non-existent .command() (Finding #16).
            arcade_inst.execute_cypher = AsyncMock()

            await run_bridge(limit=1)

        # Should have issued an ArcadeDB cypher for the temporal stamp
        arcade_inst.execute_cypher.assert_called_once()
        call_args = arcade_inst.execute_cypher.call_args
        cypher = call_args[0][0]
        assert "gid-abc" in cypher
        assert "valid_from IS NULL" in cypher


class TestD246Isolation:
    def test_no_fastapi_import(self):
        """extraction_bridge.py does not import fastapi or apscheduler."""
        import ast
        from pathlib import Path

        source = Path("src/extraction/extraction_bridge.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, "module", None) or ""
                names = [a.name for a in node.names]
                for name in [module] + names:
                    assert "fastapi" not in name.lower(), f"D246 violated: {name}"
                    assert "apscheduler" not in name.lower(), f"D246 violated: {name}"
