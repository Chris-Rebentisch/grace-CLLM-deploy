"""Integration tests for image-OCR ingestion (Chunk 77a, D499).

All tests require a working OCR backend (OcrMac on macOS, RapidOCR elsewhere).
Marked with @pytest.mark.requires_ocr — auto-skipped when backend unavailable.
"""

from pathlib import Path

import pytest

from src.discovery.document_processor import process_document
from src.discovery.models import ProcessingStatus

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "test_ocr_sample.png"


@pytest.mark.requires_ocr
def test_ocr_fixture_produces_text():
    """OCR of fixture PNG produces non-empty extracted_text (Markdown)."""
    assert _FIXTURE_PATH.exists(), f"Fixture missing: {_FIXTURE_PATH}"
    result = process_document(_FIXTURE_PATH)
    assert result.status == ProcessingStatus.COMPLETE, (
        f"Expected COMPLETE, got {result.status}: {result.error_message}"
    )
    assert result.extracted_text is not None
    assert len(result.extracted_text.strip()) > 0, "extracted_text is empty"


@pytest.mark.requires_ocr
def test_ocr_fixture_produces_json():
    """OCR of fixture PNG produces parseable docling_document_json (D26 two-output contract)."""
    assert _FIXTURE_PATH.exists(), f"Fixture missing: {_FIXTURE_PATH}"
    result = process_document(_FIXTURE_PATH)
    assert result.status == ProcessingStatus.COMPLETE, (
        f"Expected COMPLETE, got {result.status}: {result.error_message}"
    )
    assert result.docling_document_json is not None
    assert isinstance(result.docling_document_json, dict)


@pytest.mark.requires_ocr
def test_ocr_fixture_complete_status():
    """OCR of fixture PNG returns ProcessingStatus.COMPLETE."""
    assert _FIXTURE_PATH.exists(), f"Fixture missing: {_FIXTURE_PATH}"
    result = process_document(_FIXTURE_PATH)
    assert result.status == ProcessingStatus.COMPLETE
