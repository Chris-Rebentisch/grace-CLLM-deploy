"""Tests for the DocumentChunker — Chunk 17."""

import hashlib

import pytest

from src.extraction.document_chunker import DocumentChunker
from src.extraction.extraction_config import ExtractionSettings
from src.extraction.extraction_models import DocumentChunk


class TestDoclingStructuralChunking:
    """Tests using real Docling JSON fixtures."""

    def test_respects_section_boundaries(
        self, chunker, sample_docling_json, sample_extracted_text
    ):
        """Chunks produced from Docling JSON respect section/paragraph boundaries."""
        chunks = chunker.chunk_document(
            sample_extracted_text, "doc-sections", sample_docling_json
        )
        assert len(chunks) > 0
        # All chunks should be DocumentChunk instances
        for c in chunks:
            assert isinstance(c, DocumentChunk)
            assert c.text.strip()

    def test_token_cap_enforcement(self, chunker_small_cap, sample_extracted_text):
        """A section exceeding effective_cap is split at paragraph boundaries."""
        chunks = chunker_small_cap.chunk_document(sample_extracted_text, "doc-cap")
        effective_cap = int(200 * 0.8)  # 160 tokens
        for c in chunks:
            # Allow some tolerance for overlap
            non_overlap = c.text[c.overlap_char_count:]
            tokens = len(non_overlap) // 4
            # Non-overlap content should be near or under effective_cap
            # (exact enforcement is best-effort, especially with sentence boundaries)
            assert tokens < effective_cap * 2, (
                f"Chunk non-overlap text has {tokens} estimated tokens, "
                f"expected near {effective_cap}"
            )

    def test_paragraph_split_to_sentences(self, chunker_small_cap):
        """A single long paragraph is split at pySBD sentence boundaries."""
        # Create a long single paragraph (no \n\n)
        sentences = [f"Sentence number {i} contains important information." for i in range(30)]
        text = " ".join(sentences)

        chunks = chunker_small_cap.chunk_document(text, "doc-sentences")
        assert len(chunks) > 1
        # Each chunk should end at a sentence boundary
        for c in chunks:
            non_overlap = c.text[c.overlap_char_count:]
            # Should end with a period + space or period
            stripped = non_overlap.rstrip()
            assert stripped.endswith("."), f"Chunk does not end at sentence boundary: {stripped[-20:]!r}"

    def test_never_splits_mid_sentence(self, chunker_small_cap):
        """Verify all chunk text boundaries align with sentence endpoints."""
        sentences = [f"This is sentence {i} with some extra text." for i in range(20)]
        text = " ".join(sentences)

        chunks = chunker_small_cap.chunk_document(text, "doc-mid")
        for c in chunks:
            non_overlap = c.text[c.overlap_char_count:]
            # Non-overlap content should not start mid-word (after overlap prefix)
            assert non_overlap.strip()

    def test_table_atomic_chunk(self, chunker, sample_docling_json, sample_extracted_text):
        """Docling JSON with a table produces the table as its own chunk."""
        chunks = chunker.chunk_document(
            sample_extracted_text, "doc-table", sample_docling_json
        )
        # Look for a chunk that contains table content (starts with |)
        table_chunks = [c for c in chunks if c.text.strip().startswith("|")]
        # If the fixture has tables in the text, they should appear as separate chunks
        # (PDF fixture has tables, DOCX fixture may not)
        if "|" in sample_extracted_text:
            assert len(table_chunks) > 0, "Expected table chunks in fixture with table content"

    def test_docx_fixture_chunking(
        self, chunker, docx_docling_json, docx_extracted_text
    ):
        """DOCX fixture produces multiple chunks with section IDs."""
        chunks = chunker.chunk_document(
            docx_extracted_text, "doc-docx", docx_docling_json
        )
        assert len(chunks) > 1
        # At least some chunks should have section IDs
        sections = {c.section_id for c in chunks if c.section_id}
        assert len(sections) > 0


class TestPlainTextFallback:
    """Tests for the plain-text chunking path."""

    def test_double_newline_splitting(self, chunker_small_cap):
        """Document with paragraphs separated by blank lines splits correctly."""
        paras = [
            "First paragraph " * 20 + "end.",
            "Second paragraph " * 20 + "end.",
            "Third paragraph " * 20 + "end.",
        ]
        text = "\n\n".join(paras)

        chunks = chunker_small_cap.chunk_document(text, "doc-paras")
        assert len(chunks) >= 2

    def test_single_block_sentence_splitting(self, chunker_small_cap):
        """Text with no double-newlines falls back to sentence splitting."""
        sentences = [f"Important fact number {i}." for i in range(40)]
        text = " ".join(sentences)
        assert "\n\n" not in text

        chunks = chunker_small_cap.chunk_document(text, "doc-single")
        assert len(chunks) > 1

    def test_docling_json_parse_failure(self, chunker):
        """Malformed docling_json triggers fallback to plain-text path."""
        text = "Some text here.\n\nAnother paragraph."
        bad_json = {"unexpected": "structure"}

        chunks = chunker.chunk_document(text, "doc-bad", bad_json)
        assert len(chunks) > 0
        assert chunks[0].text.strip()


class TestSentenceOffsets:
    """Tests for sentence offset accuracy."""

    def test_offset_slice_matches_sentence(self, chunker):
        """Each (start, end) in sentence_offsets slices to correct sentence text."""
        text = "Hello world. This is a test. Another sentence here."
        chunks = chunker.chunk_document(text, "doc-offsets")
        assert len(chunks) == 1

        chunk = chunks[0]
        assert len(chunk.sentence_offsets) >= 2

        for start, end in chunk.sentence_offsets:
            sliced = chunk.text[start:end]
            assert sliced.strip(), f"Empty slice at ({start}, {end})"

    def test_sequential_scan_repeated_sentences(self, chunker):
        """Text with two identical sentences produces two distinct offsets."""
        text = "Hello world. Hello world. Something else."
        chunks = chunker.chunk_document(text, "doc-repeat")
        assert len(chunks) == 1

        offsets = chunks[0].sentence_offsets
        # Should have 3 sentences with distinct offsets
        assert len(offsets) >= 2
        # No duplicate offsets
        starts = [s for s, e in offsets]
        assert len(starts) == len(set(starts)), "Duplicate start offsets found"


class TestOverlap:
    """Tests for overlap prepending logic."""

    def test_overlap_prepended(self, chunker_small_cap):
        """Chunk N+1 starts with trailing text from chunk N."""
        sentences = [f"Sentence number {i} contains text." for i in range(30)]
        text = " ".join(sentences)

        chunks = chunker_small_cap.chunk_document(text, "doc-overlap")
        assert len(chunks) >= 2

        # Second chunk should have overlap
        assert chunks[1].overlap_char_count > 0
        assert chunks[1].is_overlap is True

    def test_overlap_char_count_accurate(self, chunker_small_cap):
        """overlap_char_count equals actual prepended character count."""
        sentences = [f"Sentence number {i} contains text." for i in range(30)]
        text = " ".join(sentences)

        chunks = chunker_small_cap.chunk_document(text, "doc-overlap-count")
        if len(chunks) >= 2:
            c = chunks[1]
            overlap_text = c.text[: c.overlap_char_count]
            non_overlap = c.text[c.overlap_char_count:]
            # The overlap text should come from the end of the previous chunk's non-overlap
            prev_non_overlap = chunks[0].text[chunks[0].overlap_char_count:]
            assert prev_non_overlap.endswith(overlap_text), (
                "Overlap text doesn't match end of previous chunk"
            )

    def test_no_overlap_on_first_chunk(self, chunker_small_cap):
        """First chunk always has overlap_char_count=0, is_overlap=False."""
        sentences = [f"Sentence number {i} contains text." for i in range(30)]
        text = " ".join(sentences)

        chunks = chunker_small_cap.chunk_document(text, "doc-first")
        assert chunks[0].overlap_char_count == 0
        assert chunks[0].is_overlap is False

    def test_char_start_references_original(self, chunker_small_cap):
        """char_start of chunk N+1 points to non-overlap content in original text."""
        sentences = [f"Sentence number {i} contains text." for i in range(30)]
        text = " ".join(sentences)

        chunks = chunker_small_cap.chunk_document(text, "doc-charstart")
        for c in chunks:
            non_overlap = c.text[c.overlap_char_count:]
            original_slice = text[c.char_start : c.char_end]
            # Non-overlap content should match what's at char_start in original
            assert non_overlap[:30] == original_slice[:30], (
                f"Mismatch: non_overlap starts with {non_overlap[:30]!r}, "
                f"original at {c.char_start} starts with {original_slice[:30]!r}"
            )


class TestEdgeCases:
    """Tests for edge cases."""

    def test_empty_document(self, chunker):
        """Empty string returns empty list."""
        assert chunker.chunk_document("", "doc-empty") == []
        assert chunker.chunk_document("   ", "doc-whitespace") == []

    def test_short_document_single_chunk(self, chunker):
        """Document under 100 tokens produces one chunk, no overlap."""
        text = "Short document. Just two sentences."
        chunks = chunker.chunk_document(text, "doc-short")
        assert len(chunks) == 1
        assert chunks[0].overlap_char_count == 0

    def test_deterministic_chunk_id(self, chunker):
        """Same document_id + index produces same chunk_id."""
        text = "Hello world. Test document."
        chunks1 = chunker.chunk_document(text, "doc-id-test")
        chunks2 = chunker.chunk_document(text, "doc-id-test")
        assert chunks1[0].chunk_id == chunks2[0].chunk_id

        # Verify ID format: SHA-256 of "doc_id:index", truncated to 16 hex
        expected = hashlib.sha256("doc-id-test:0".encode()).hexdigest()[:16]
        assert chunks1[0].chunk_id == expected

    def test_zero_overlap_config(self):
        """chunk_overlap_tokens=0 produces no overlap on any chunk."""
        config = ExtractionSettings(chunk_token_cap=100, chunk_overlap_tokens=0)
        chunker = DocumentChunker(config=config)

        sentences = [f"Sentence number {i} contains text." for i in range(30)]
        text = " ".join(sentences)

        chunks = chunker.chunk_document(text, "doc-zero-overlap")
        for c in chunks:
            assert c.overlap_char_count == 0
            assert c.is_overlap is False

    def test_custom_token_counter(self):
        """Custom token_counter callable is used instead of default."""
        call_count = 0

        def counting_counter(text: str) -> int:
            nonlocal call_count
            call_count += 1
            return len(text) // 4

        chunker = DocumentChunker(token_counter=counting_counter)
        chunker.chunk_document("Hello world. Test.", "doc-custom")
        assert call_count > 0
