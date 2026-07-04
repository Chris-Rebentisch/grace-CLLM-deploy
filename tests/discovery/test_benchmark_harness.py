"""Tests for A/B benchmark harness (Chunk 62, CP4, D443)."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Import the harness functions directly
from scripts.benchmark.run_docling_benchmark import (
    run_benchmark,
    generate_markdown_summary,
    _compare_table_fidelity,
)


class TestBenchmarkHarness:
    """Tests for the A/B benchmark harness."""

    def test_empty_corpus_does_not_crash(self, tmp_path):
        """Empty corpus produces a valid result without crashing."""
        result = run_benchmark(tmp_path)
        assert result["pdf_count"] == 0
        assert result["results"] == []
        assert "note" in result["summary"]

    def test_benchmark_output_has_expected_keys(self, tmp_path):
        """Benchmark result has all expected top-level keys."""
        result = run_benchmark(tmp_path)
        assert "timestamp" in result
        assert "corpus_dir" in result
        assert "pdf_count" in result
        assert "results" in result
        assert "summary" in result

    def test_benchmark_json_is_valid(self, tmp_path):
        """Benchmark result serializes to valid JSON."""
        result = run_benchmark(tmp_path)
        json_str = json.dumps(result)
        parsed = json.loads(json_str)
        assert parsed["pdf_count"] == 0

    def test_markdown_summary_nonempty(self, tmp_path):
        """Markdown summary is non-empty for any corpus (including empty)."""
        result = run_benchmark(tmp_path)
        md = generate_markdown_summary(result)
        assert len(md) > 0
        assert "# Docling Pipeline Benchmark Results" in md

    def test_compare_table_fidelity_returns_diffs(self):
        """_compare_table_fidelity returns expected diff fields."""
        std = {"table_count": 3, "total_table_cells": 30, "text_elements": 20, "word_count": 500}
        vlm = {"table_count": 4, "total_table_cells": 40, "text_elements": 22, "word_count": 510}
        comparison = _compare_table_fidelity(std, vlm)
        assert comparison["table_count_diff"] == 1
        assert comparison["cell_count_diff"] == 10
        assert comparison["text_element_diff"] == 2
        assert comparison["word_count_diff"] == 10

    def test_benchmark_with_pdf_runs_both_modes(self, tmp_path):
        """When PDFs exist, both modes are represented in results."""
        # Create a minimal PDF
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 minimal test content")

        # Mock both converters to avoid real Docling processing
        mock_doc = MagicMock()
        mock_doc.export_to_markdown.return_value = "test content"
        mock_doc.model_dump_json.return_value = json.dumps({
            "texts": [{"label": "text", "text": "test"}],
            "tables": [],
        })
        mock_result = MagicMock()
        mock_result.document = mock_doc

        with patch("scripts.benchmark.run_docling_benchmark.DocumentConverter") as mock_std, \
             patch("scripts.benchmark.run_docling_benchmark._build_vlm_converter") as mock_vlm:
            mock_std.return_value.convert.return_value = mock_result
            mock_vlm.return_value.convert.return_value = mock_result

            result = run_benchmark(tmp_path)

        assert result["pdf_count"] == 1
        assert len(result["results"]) == 1
        entry = result["results"][0]
        assert "standard" in entry
        assert "vlm" in entry
        assert "comparison" in entry
