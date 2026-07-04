"""Tests for document_processing config surface (Chunk 62, CP2, D443)."""

import pytest
import yaml

from src.discovery.document_processor import _build_converter, _VALID_PIPELINE_MODES, _VALID_MODEL_SPECS
from src.discovery.models import load_discovery_config


class TestConfigSection:
    """Tests for document_processing section in config/discovery.yaml."""

    def test_yaml_round_trip_parses_document_processing(self):
        """document_processing section parses correctly from discovery.yaml."""
        config = load_discovery_config()
        dp = config.get("document_processing")
        assert dp is not None, "document_processing section missing from discovery.yaml"
        assert "pipeline_mode" in dp
        assert dp["pipeline_mode"] in _VALID_PIPELINE_MODES

    def test_vlm_sub_block_parses(self):
        """vlm sub-block parses with model_spec."""
        config = load_discovery_config()
        dp = config["document_processing"]
        vlm = dp.get("vlm")
        assert vlm is not None, "vlm sub-block missing from document_processing"
        assert vlm.get("model_spec") in _VALID_MODEL_SPECS

    def test_pipeline_mode_validation_accepts_standard_and_vlm(self):
        """standard and vlm are accepted pipeline_mode values."""
        for mode in ("standard", "vlm"):
            config = {"document_processing": {"pipeline_mode": mode}}
            if mode == "vlm":
                config["document_processing"]["vlm"] = {"model_spec": "GRANITEDOCLING_TRANSFORMERS"}
            converter = _build_converter(config)
            assert converter is not None

    def test_pipeline_mode_validation_rejects_unknown(self):
        """Unknown pipeline_mode value is rejected."""
        config = {"document_processing": {"pipeline_mode": "turbo"}}
        with pytest.raises(ValueError, match="Invalid pipeline_mode"):
            _build_converter(config)

    def test_missing_document_processing_defaults_to_standard(self):
        """Absent document_processing section defaults to standard (backward compat)."""
        config = {}
        converter = _build_converter(config)
        from docling.document_converter import DocumentConverter
        assert isinstance(converter, DocumentConverter)

    def test_build_converter_respects_vlm_config(self):
        """VLM config produces VLM converter; standard config produces default converter."""
        from docling.datamodel.base_models import InputFormat
        from docling.pipeline.vlm_pipeline import VlmPipeline

        vlm_config = {
            "document_processing": {
                "pipeline_mode": "vlm",
                "vlm": {"model_spec": "GRANITEDOCLING_TRANSFORMERS"},
            }
        }
        vlm_converter = _build_converter(vlm_config)
        pdf_opt = vlm_converter.format_to_options[InputFormat.PDF]
        assert pdf_opt.pipeline_cls is VlmPipeline

        std_config = {"document_processing": {"pipeline_mode": "standard"}}
        std_converter = _build_converter(std_config)
        std_pdf_opt = std_converter.format_to_options.get(InputFormat.PDF)
        if std_pdf_opt is not None:
            assert std_pdf_opt.pipeline_cls is not VlmPipeline
