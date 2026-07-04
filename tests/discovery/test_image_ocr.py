"""Unit tests for image-OCR ingestion (Chunk 77a, D499)."""

import sys
from unittest.mock import patch

import pytest
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    OcrMacOptions,
    PdfPipelineOptions,
    RapidOcrOptions,
)

from src.discovery.document_processor import _build_converter, EXTENSION_TO_FILETYPE
from src.discovery.models import FileType


def test_filetype_image_member():
    """FileType.IMAGE is a valid enum member with value 'IMAGE'."""
    assert FileType.IMAGE == "IMAGE"
    assert FileType.IMAGE.value == "IMAGE"
    assert isinstance(FileType.IMAGE, FileType)


def test_extension_to_filetype_image_mappings():
    """All 7 image extensions map to FileType.IMAGE in EXTENSION_TO_FILETYPE."""
    expected_extensions = [".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp"]
    for ext in expected_extensions:
        assert ext in EXTENSION_TO_FILETYPE, f"Missing extension: {ext}"
        assert EXTENSION_TO_FILETYPE[ext] == FileType.IMAGE, (
            f"Extension {ext} maps to {EXTENSION_TO_FILETYPE[ext]}, expected FileType.IMAGE"
        )


def test_auto_backend_darwin():
    """On macOS, auto backend resolves to OcrMacOptions via PdfPipelineOptions."""
    config = {
        "document_processing": {
            "pipeline_mode": "standard",
            "ocr": {"backend": "auto", "force_full_page_ocr": True},
        }
    }
    with patch.object(sys, "platform", "darwin"):
        converter = _build_converter(config)

    assert InputFormat.IMAGE in converter.format_to_options
    image_fmt = converter.format_to_options[InputFormat.IMAGE]
    assert isinstance(image_fmt.pipeline_options, PdfPipelineOptions)
    assert image_fmt.pipeline_options.do_ocr is True
    assert isinstance(image_fmt.pipeline_options.ocr_options, OcrMacOptions)


def test_ocrmac_off_darwin_raises():
    """Explicit ocrmac backend on non-Darwin raises ValueError."""
    config = {
        "document_processing": {
            "pipeline_mode": "standard",
            "ocr": {"backend": "ocrmac"},
        }
    }
    with patch.object(sys, "platform", "linux"):
        with pytest.raises(ValueError, match="OcrMac requires macOS"):
            _build_converter(config)
