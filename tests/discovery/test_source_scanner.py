"""Tests for Discovery source scanner."""

import json
from pathlib import Path

from src.discovery.source_scanner import configure_sources, scan_sources


def test_scan_sources_returns_dirs(tmp_path):
    """scan_sources() returns a list of dicts with required fields."""
    # Create some directories with files
    docs = tmp_path / "Documents"
    docs.mkdir()
    (docs / "file.pdf").write_bytes(b"%PDF-test")
    (docs / "file.txt").write_text("hello")

    other = tmp_path / "Projects"
    other.mkdir()
    (other / "code.py").write_text("print('hi')")

    results = scan_sources(root_dir=tmp_path)
    assert len(results) >= 2

    for entry in results:
        assert "name" in entry
        assert "path" in entry
        assert "total_files" in entry
        assert "document_files" in entry
        assert "total_size_bytes" in entry
        assert "document_size_bytes" in entry
        assert "suggested_include" in entry

    # Documents should be suggested
    docs_entry = next(e for e in results if e["name"] == "Documents")
    assert docs_entry["suggested_include"] is True
    assert docs_entry["document_files"] == 2  # .pdf and .txt


def test_scan_sources_skips_hidden(tmp_path):
    """Hidden directories (starting with .) are not returned."""
    visible = tmp_path / "Visible"
    visible.mkdir()
    hidden = tmp_path / ".hidden"
    hidden.mkdir()

    results = scan_sources(root_dir=tmp_path)
    names = [r["name"] for r in results]
    assert "Visible" in names
    assert ".hidden" not in names


def test_configure_sources_creates_manifest(tmp_path):
    """configure_sources() writes a valid manifest JSON.

    Passes an explicit `manifest_path` so the test does not mutate the
    tracked `config/discovery-manifest.json`.
    """
    # Create test directory with files
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "report.pdf").write_bytes(b"%PDF-test-content")
    (docs / "notes.txt").write_text("some notes here")
    (docs / "image.jpg").write_bytes(b"\xff\xd8\xff")

    manifest_target = tmp_path / "discovery-manifest.json"
    result = configure_sources(
        selected_paths=[str(docs)],
        manifest_path=manifest_target,
    )

    assert result["total_files"] == 3  # .pdf, .txt, and .jpg (D499 image-OCR)
    assert ".pdf" in result["by_extension"]
    assert ".txt" in result["by_extension"]
    assert ".jpg" in result["by_extension"]
    assert result["manifest_path"] == str(manifest_target)

    # Verify manifest file was written to the tmp_path target
    assert manifest_target.exists()
    manifest = json.loads(manifest_target.read_text())
    assert len(manifest["files"]) == 3
    assert "created_at" in manifest
