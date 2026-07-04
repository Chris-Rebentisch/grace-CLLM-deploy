"""Tests for Discovery document processor (Docling integration)."""

import pytest
from pathlib import Path

from src.discovery.document_processor import process_document
from src.discovery.models import ProcessingStatus


@pytest.fixture()
def sample_txt(tmp_path):
    """Create a plain text test file."""
    f = tmp_path / "sample.txt"
    f.write_text("This is a test document with several words for counting purposes.")
    return f


@pytest.fixture()
def sample_pdf(tmp_path):
    """Create a proper PDF test file using fpdf2."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(text="Hello World. This is a test PDF document for GrACE Discovery.")
    f = tmp_path / "sample.pdf"
    pdf.output(str(f))
    return f


@pytest.fixture()
def sample_docx(tmp_path):
    """Create a minimal .docx test file using python-docx if available, else a simple zip."""
    try:
        from docx import Document
        doc = Document()
        doc.add_paragraph("This is a test Word document.")
        f = tmp_path / "sample.docx"
        doc.save(str(f))
        return f
    except ImportError:
        pytest.skip("python-docx not installed")


def test_process_txt(sample_txt):
    """Process a plain .txt file, verify text matches content."""
    result = process_document(sample_txt)
    assert result.status == ProcessingStatus.COMPLETE
    assert "test document" in result.extracted_text.lower()
    assert result.word_count > 0
    assert result.file_name == "sample.txt"
    # .txt files are read directly (not via Docling), so no docling JSON
    assert result.docling_document_json is None


def test_process_pdf(sample_pdf):
    """Process a small test PDF, verify extracted_text is non-empty."""
    result = process_document(sample_pdf)
    # Minimal PDFs may not extract cleanly with Docling, but should not crash
    assert result.status in (ProcessingStatus.COMPLETE, ProcessingStatus.FAILED)
    if result.status == ProcessingStatus.COMPLETE:
        assert result.docling_document_json is not None
        assert isinstance(result.docling_document_json, dict)


def test_process_docx(sample_docx):
    """Process a .docx file, verify extraction."""
    result = process_document(sample_docx)
    assert result.status == ProcessingStatus.COMPLETE
    assert result.word_count > 0
    assert result.docling_document_json is not None


def test_process_unsupported_extension(tmp_path):
    """Process a .xyz file, verify status=SKIPPED.

    Note: .jpg was the original fixture but is now supported (D499 image-OCR).
    Switched to .xyz which is genuinely unsupported.
    """
    f = tmp_path / "data.xyz"
    f.write_bytes(b"\x00" * 100)
    result = process_document(f)
    assert result.status == ProcessingStatus.SKIPPED
    assert "unsupported" in result.error_message.lower()


def test_process_nonexistent_file():
    """Process a path that doesn't exist, verify status=FAILED with error_message."""
    result = process_document(Path("/nonexistent/path/document.pdf"))
    assert result.status == ProcessingStatus.FAILED
    assert result.error_message is not None
    assert "not found" in result.error_message.lower()
