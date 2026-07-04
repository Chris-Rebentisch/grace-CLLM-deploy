"""D26 downstream contract: Chunk 17 _parse_docling_sections / DocumentChunker
against VLM-produced fixtures (Chunk 62, CP3, D443).

The VLM pipeline produces the same DoclingDocument representation as the standard
ensemble pipeline. This test suite verifies that Chunk 17's frozen consumer
(src/extraction/document_chunker.py — D26/D48) works unmodified against VLM output.

No modifications to extraction code. Read-only consumer tests only.
"""

import json
from pathlib import Path

import pytest

from src.extraction.document_chunker import DocumentChunker

FIXTURES_DIR = Path(__file__).parent / "fixtures"
VLM_JSON_PATH = FIXTURES_DIR / "vlm_docling_document.json"
VLM_TEXT_PATH = FIXTURES_DIR / "vlm_docling_document_text.txt"


@pytest.fixture
def vlm_fixture() -> dict:
    """Load VLM-produced DoclingDocument JSON fixture."""
    with open(VLM_JSON_PATH) as f:
        return json.load(f)


@pytest.fixture
def vlm_text() -> str:
    """Load VLM-produced Markdown export text fixture."""
    return VLM_TEXT_PATH.read_text(encoding="utf-8")


@pytest.fixture
def chunker() -> DocumentChunker:
    """Instantiate DocumentChunker (frozen consumer — D26/D48)."""
    return DocumentChunker()


class TestVlmEquivalence:
    """D26 contract tests: VLM fixtures work with frozen DocumentChunker."""

    def test_parse_docling_sections_returns_non_none(self, chunker, vlm_fixture):
        """_parse_docling_sections returns non-None for VLM fixture (no silent fallback
        to plain-text chunking — document_chunker.py:183)."""
        result = chunker._parse_docling_sections(vlm_fixture)
        assert result is not None, (
            "_parse_docling_sections returned None for VLM fixture — "
            "D26 contract violation: would trigger fallback to plain-text chunking"
        )

    def test_section_header_present(self, chunker, vlm_fixture):
        """VLM fixture includes at least one section_header-labeled entry
        (matching document_chunker.py:264 `label == 'section_header'` check)."""
        elements = chunker._parse_docling_sections(vlm_fixture)
        assert elements is not None
        section_headers = [e for e in elements if e["element_type"] == "section-header"]
        assert len(section_headers) >= 1, "No section-header elements found in VLM fixture"

    def test_table_cross_references_resolve(self, vlm_fixture):
        """Table self_ref (L216) and body.children cref (L225) resolve correctly."""
        tables = vlm_fixture.get("tables", [])
        assert len(tables) >= 1, "VLM fixture must include at least one table"

        body_children = vlm_fixture.get("body", {}).get("children", [])
        body_crefs = {child.get("cref") for child in body_children}

        for table in tables:
            self_ref = table.get("self_ref", "")
            assert self_ref, "Table missing self_ref"
            assert self_ref in body_crefs, (
                f"Table self_ref '{self_ref}' not found in body.children crefs"
            )

    def test_chunker_produces_nonempty_chunks(self, chunker, vlm_fixture, vlm_text):
        """DocumentChunker produces non-empty chunks from VLM fixtures
        (via the public chunk_document() API)."""
        chunks = chunker.chunk_document(
            text=vlm_text,
            document_id="vlm-fixture-test",
            docling_json=vlm_fixture,
        )
        assert len(chunks) > 0, "DocumentChunker produced zero chunks from VLM fixture"
        # Each chunk should have non-empty text
        for chunk in chunks:
            assert chunk.text, "Chunk has no text"
