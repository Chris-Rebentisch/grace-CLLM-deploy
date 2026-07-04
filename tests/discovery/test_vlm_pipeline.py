"""Tests for VLM pipeline mode in document_processor.py (Chunk 62, CP1, D443)."""

from unittest.mock import MagicMock, patch

import pytest

from src.discovery.document_processor import _build_converter, _VALID_PIPELINE_MODES, _VALID_MODEL_SPECS


class TestBuildConverter:
    """Tests for _build_converter() config-driven converter construction."""

    def test_standard_mode_returns_bare_converter(self):
        """Under pipeline_mode: standard, _build_converter() returns a bare DocumentConverter()."""
        config = {"document_processing": {"pipeline_mode": "standard"}}
        converter = _build_converter(config)
        from docling.document_converter import DocumentConverter
        from docling.datamodel.base_models import InputFormat
        from docling.pipeline.vlm_pipeline import VlmPipeline
        assert isinstance(converter, DocumentConverter)
        # Standard mode: PDF option should NOT use VlmPipeline
        pdf_opt = converter.format_to_options.get(InputFormat.PDF)
        if pdf_opt is not None:
            assert pdf_opt.pipeline_cls is not VlmPipeline

    def test_missing_section_defaults_to_standard(self):
        """Absent document_processing section defaults to standard (backward compatibility)."""
        config = {}
        converter = _build_converter(config)
        from docling.document_converter import DocumentConverter
        assert isinstance(converter, DocumentConverter)

    def test_vlm_mode_produces_vlm_converter(self):
        """Under pipeline_mode: vlm, _build_converter() constructs a VLM-enabled converter."""
        config = {
            "document_processing": {
                "pipeline_mode": "vlm",
                "vlm": {"model_spec": "GRANITEDOCLING_TRANSFORMERS"},
            }
        }
        converter = _build_converter(config)
        from docling.document_converter import DocumentConverter
        from docling.datamodel.base_models import InputFormat
        assert isinstance(converter, DocumentConverter)
        # VLM mode should have custom format_options for PDF
        assert InputFormat.PDF in converter.format_to_options

    def test_vlm_mode_pdf_uses_vlm_pipeline(self):
        """Under VLM mode, PDF routes through VlmPipeline."""
        config = {
            "document_processing": {
                "pipeline_mode": "vlm",
                "vlm": {"model_spec": "GRANITEDOCLING_TRANSFORMERS"},
            }
        }
        converter = _build_converter(config)
        from docling.datamodel.base_models import InputFormat
        from docling.pipeline.vlm_pipeline import VlmPipeline
        pdf_option = converter.format_to_options[InputFormat.PDF]
        assert pdf_option.pipeline_cls is VlmPipeline

    def test_invalid_pipeline_mode_raises_value_error(self):
        """Invalid pipeline_mode raises ValueError."""
        config = {"document_processing": {"pipeline_mode": "hybrid"}}
        with pytest.raises(ValueError, match="Invalid pipeline_mode"):
            _build_converter(config)

    def test_invalid_model_spec_raises_value_error(self):
        """Invalid vlm.model_spec raises ValueError."""
        config = {
            "document_processing": {
                "pipeline_mode": "vlm",
                "vlm": {"model_spec": "NONEXISTENT_SPEC"},
            }
        }
        with pytest.raises(ValueError, match="Invalid vlm.model_spec"):
            _build_converter(config)

    def test_vlm_default_model_spec_when_missing(self):
        """When vlm.model_spec is absent, defaults to GRANITEDOCLING_TRANSFORMERS."""
        config = {
            "document_processing": {
                "pipeline_mode": "vlm",
                "vlm": {},
            }
        }
        converter = _build_converter(config)
        from docling.datamodel.base_models import InputFormat
        from docling.pipeline.vlm_pipeline import VlmPipeline
        assert InputFormat.PDF in converter.format_to_options
        pdf_option = converter.format_to_options[InputFormat.PDF]
        assert pdf_option.pipeline_cls is VlmPipeline


class TestProcessDocumentVlmMode:
    """Tests that process_document() honors pipeline_mode config when no converter is passed."""

    def test_process_document_uses_build_converter_when_no_converter(self, tmp_path):
        """process_document() calls _build_converter when converter is None."""
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"%PDF-1.4 minimal")

        with patch("src.discovery.document_processor._build_converter") as mock_build:
            mock_converter = MagicMock()
            mock_converter.convert.side_effect = Exception("mock conversion")
            mock_build.return_value = mock_converter

            from src.discovery.document_processor import process_document
            result = process_document(test_file)

            mock_build.assert_called_once()
            mock_converter.convert.assert_called_once()
            from src.discovery.models import ProcessingStatus
            assert result.status == ProcessingStatus.FAILED
