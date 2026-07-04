"""Tests for backfill_document_chunks CLI.

CP5 (D466): column-name regression, idempotency, no derives_from edges.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from uuid import uuid4

import pytest


# ---------------------------------------------------------------------------
# Test 1: Backfill reads extracted_text column (not cleaned_text)
# ---------------------------------------------------------------------------


def test_backfill_reads_extracted_text_column():
    """Backfill must read extracted_text, never cleaned_text (spec §6 CP5)."""
    from src.extraction.backfill_document_chunks import backfill_document_chunks

    source = inspect.getsource(backfill_document_chunks)
    assert "extracted_text" in source, (
        "backfill_document_chunks must reference extracted_text column"
    )
    assert "cleaned_text" not in source, (
        "backfill_document_chunks must NOT reference cleaned_text column — "
        "use extracted_text per mine_sampler.py:393"
    )


# ---------------------------------------------------------------------------
# Test 2: Backfill idempotent re-run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_idempotent_rerun():
    """Re-running backfill produces no duplicate chunks."""
    from src.extraction.backfill_document_chunks import backfill_document_chunks

    # Mock session with one document row
    mock_row = MagicMock()
    mock_row.id = uuid4()
    mock_row.extracted_text = "Some contract text for testing."
    mock_row.file_path = "/data/test.txt"

    mock_session = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [mock_row]
    mock_execute = MagicMock()
    mock_execute.scalars.return_value = mock_scalars
    mock_session.execute.return_value = mock_execute

    mock_client = AsyncMock()

    # First run: lookup returns None (not yet inserted) -> create
    call_count = {"lookups": 0}

    async def mock_lookup(client, doc_id, idx):
        call_count["lookups"] += 1
        if call_count["lookups"] <= 1:
            return None  # First run: not found
        return str(uuid4())  # Second run: already exists

    with patch(
        "src.extraction.backfill_document_chunks._lookup_document_chunk",
        side_effect=mock_lookup,
    ), patch(
        "src.extraction.backfill_document_chunks.embed_texts",
        new_callable=AsyncMock,
        return_value=[[0.1] * 768],
    ), patch(
        "src.extraction.backfill_document_chunks._insert_document_chunk_vertex",
        new_callable=AsyncMock,
        return_value=str(uuid4()),
    ) as mock_insert, patch(
        "src.extraction.backfill_document_chunks.DocumentChunker",
    ) as mock_chunker_cls:
        # Configure chunker to return 1 chunk
        mock_chunk = MagicMock()
        mock_chunk.text = "Some contract text."
        mock_chunk.chunk_id = "abc123"
        mock_chunk.token_count_estimate = 10
        mock_chunker_cls.return_value.chunk_text.return_value = [mock_chunk]

        # First run
        stats1 = await backfill_document_chunks(
            session=mock_session, client=mock_client,
        )
        assert stats1["chunks_created"] == 1

        # Second run (same corpus) — should skip
        stats2 = await backfill_document_chunks(
            session=mock_session, client=mock_client,
        )
        assert stats2["chunks_skipped"] == 1
        assert stats2["chunks_created"] == 0


# ---------------------------------------------------------------------------
# Test 3: Backfill produces no derives_from edges
# ---------------------------------------------------------------------------


def test_backfill_no_derives_from_edges():
    """Backfilled chunks must NOT create derives_from edges."""
    from src.extraction.backfill_document_chunks import backfill_document_chunks

    source = inspect.getsource(backfill_document_chunks)
    assert "derives_from" not in source, (
        "backfill_document_chunks must NOT create derives_from edges — "
        "pre-Chunk-71 entities lack chunk reference"
    )
