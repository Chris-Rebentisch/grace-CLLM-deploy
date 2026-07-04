"""Tests for --reprocess flag on batch_runner.py (Chunk 62, CP5, D443)."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from src.discovery.database import update_document
from src.discovery.models import ProcessedDocument, ProcessingStatus, FileType


class TestUpdateDocument:
    """Tests for update_document() in database.py."""

    def test_update_document_refreshes_fields(self):
        """update_document refreshes extracted_text, docling_document_json, word_count,
        status, and processed_at on the matching file_path row."""
        mock_db = MagicMock()
        mock_row = MagicMock()
        mock_row.file_path = "/test/doc.pdf"
        mock_row.extracted_text = "old text"
        mock_row.docling_document_json = {"old": True}
        mock_row.word_count = 2
        mock_row.status = "COMPLETE"
        mock_row.processed_at = datetime(2025, 1, 1, tzinfo=UTC)
        mock_row.id = uuid4()
        mock_row.file_name = "doc.pdf"
        mock_row.file_type = "PDF"
        mock_row.file_size_bytes = 1000
        mock_row.created_at = None
        mock_row.modified_at = None
        mock_row.project = ""
        mock_row.domain = "other"
        mock_row.error_message = None
        mock_row.metadata_extra = {}
        mock_row.origin = None
        mock_row.source_type = None

        mock_db.query.return_value.filter.return_value.first.return_value = mock_row

        new_doc = ProcessedDocument(
            file_path="/test/doc.pdf",
            file_name="doc.pdf",
            file_type=FileType.PDF,
            file_size_bytes=1000,
            status=ProcessingStatus.COMPLETE,
            extracted_text="new text from VLM",
            docling_document_json={"vlm": True},
            word_count=4,
            processed_at=datetime(2026, 5, 21, tzinfo=UTC),
        )

        result = update_document(mock_db, "/test/doc.pdf", new_doc)

        # Verify fields were updated on the row
        assert mock_row.extracted_text == "new text from VLM"
        assert mock_row.docling_document_json == {"vlm": True}
        assert mock_row.word_count == 4
        assert mock_row.status == "COMPLETE"
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once_with(mock_row)

    def test_update_document_raises_on_missing_path(self):
        """update_document raises ValueError when file_path not found."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        doc = ProcessedDocument(
            file_path="/nonexistent/doc.pdf",
            file_name="doc.pdf",
            file_type=FileType.PDF,
            file_size_bytes=0,
            status=ProcessingStatus.FAILED,
        )

        with pytest.raises(ValueError, match="No document found"):
            update_document(mock_db, "/nonexistent/doc.pdf", doc)


class TestReprocessFlag:
    """Tests for --reprocess flag behavior in batch_runner."""

    def test_without_reprocess_duplicates_skip(self):
        """Without --reprocess, existing documents are skipped."""
        from src.discovery.batch_runner import run_batch

        with patch("src.discovery.batch_runner._collect_files_from_dir") as mock_collect, \
             patch("src.discovery.batch_runner.get_db") as mock_get_db, \
             patch("src.discovery.batch_runner.get_document_by_path") as mock_get_doc, \
             patch("src.discovery.batch_runner.get_processing_summary") as mock_summary, \
             patch("src.discovery.batch_runner._build_converter") as mock_build, \
             patch("src.discovery.batch_runner.load_discovery_config") as mock_config:

            from pathlib import Path
            mock_collect.return_value = [Path("/test/doc.pdf")]
            mock_db = MagicMock()
            mock_get_db.return_value = iter([mock_db])
            mock_get_doc.return_value = MagicMock()  # existing doc
            mock_summary.return_value = {"by_status": {}}
            mock_build.return_value = MagicMock()
            mock_config.return_value = {}

            result = run_batch(source_dir=Path("/test"), reprocess=False)

            # Should not have called process_document since doc exists and reprocess=False
            mock_get_doc.assert_called_once()

    def test_with_reprocess_updates_existing(self):
        """With --reprocess, existing documents are re-processed and updated."""
        from src.discovery.batch_runner import run_batch

        with patch("src.discovery.batch_runner._collect_files_from_dir") as mock_collect, \
             patch("src.discovery.batch_runner.get_db") as mock_get_db, \
             patch("src.discovery.batch_runner.get_document_by_path") as mock_get_doc, \
             patch("src.discovery.batch_runner.process_document") as mock_process, \
             patch("src.discovery.batch_runner.tag_document") as mock_tag, \
             patch("src.discovery.batch_runner.update_document") as mock_update, \
             patch("src.discovery.batch_runner.get_processing_summary") as mock_summary, \
             patch("src.discovery.batch_runner._build_converter") as mock_build, \
             patch("src.discovery.batch_runner.load_discovery_config") as mock_config:

            from pathlib import Path
            mock_collect.return_value = [Path("/test/doc.pdf")]
            mock_db = MagicMock()
            mock_get_db.return_value = iter([mock_db])
            mock_get_doc.return_value = MagicMock()  # existing doc

            mock_doc = MagicMock()
            mock_doc.file_name = "doc.pdf"
            mock_doc.status.value = "COMPLETE"
            mock_doc.word_count = 100
            mock_process.return_value = mock_doc
            mock_tag.return_value = mock_doc
            mock_summary.return_value = {"by_status": {}}
            mock_build.return_value = MagicMock()
            mock_config.return_value = {}

            result = run_batch(source_dir=Path("/test"), reprocess=True)

            # Should have called update_document instead of create_document
            mock_update.assert_called_once()
            mock_process.assert_called_once()

    def test_reprocess_inserts_new_documents(self):
        """With --reprocess, new documents (no existing row) are inserted normally."""
        from src.discovery.batch_runner import run_batch

        with patch("src.discovery.batch_runner._collect_files_from_dir") as mock_collect, \
             patch("src.discovery.batch_runner.get_db") as mock_get_db, \
             patch("src.discovery.batch_runner.get_document_by_path") as mock_get_doc, \
             patch("src.discovery.batch_runner.process_document") as mock_process, \
             patch("src.discovery.batch_runner.tag_document") as mock_tag, \
             patch("src.discovery.batch_runner.create_document") as mock_create, \
             patch("src.discovery.batch_runner.get_processing_summary") as mock_summary, \
             patch("src.discovery.batch_runner._build_converter") as mock_build, \
             patch("src.discovery.batch_runner.load_discovery_config") as mock_config:

            from pathlib import Path
            mock_collect.return_value = [Path("/test/new_doc.pdf")]
            mock_db = MagicMock()
            mock_get_db.return_value = iter([mock_db])
            mock_get_doc.return_value = None  # no existing doc

            mock_doc = MagicMock()
            mock_doc.file_name = "new_doc.pdf"
            mock_doc.status.value = "COMPLETE"
            mock_doc.word_count = 50
            mock_process.return_value = mock_doc
            mock_tag.return_value = mock_doc
            mock_summary.return_value = {"by_status": {}}
            mock_build.return_value = MagicMock()
            mock_config.return_value = {}

            result = run_batch(source_dir=Path("/test"), reprocess=True)

            # Should have called create_document for new docs
            mock_create.assert_called_once()
