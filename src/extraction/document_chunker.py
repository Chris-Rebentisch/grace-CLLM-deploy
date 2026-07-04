"""Document chunker for the Extraction pipeline.

Takes processed document text (Docling markdown export or plain text) and
produces extraction-ready DocumentChunk segments with sentence offsets,
overlap tracking, and deterministic chunk IDs.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

import pysbd
import structlog

from src.extraction.extraction_config import ExtractionSettings
from src.extraction.extraction_models import DocumentChunk

log = structlog.get_logger()


class DocumentChunker:
    """Structural-first document chunker for extraction.

    Chunks documents using structural boundaries (sections, paragraphs)
    from Docling JSON when available, falling back to plain-text paragraph
    splitting for unstructured documents.
    """

    def __init__(
        self,
        config: ExtractionSettings | None = None,
        token_counter: Callable[[str], int] | None = None,
    ):
        """Initialize with extraction config and optional token counter.

        Args:
            config: Extraction settings. Uses defaults if None.
            token_counter: Optional callable returning token count for a string.
                           Default: len(text) // 4 (character heuristic).
        """
        self._config = config or ExtractionSettings()
        self._token_counter = token_counter or self._default_token_estimate
        self._segmenter = pysbd.Segmenter(language="en", clean=False)
        self._effective_cap = int(self._config.chunk_token_cap * 0.8)
        self._overlap_chars = self._config.chunk_overlap_tokens * 4

    def chunk_document(
        self,
        text: str,
        document_id: str,
        docling_json: dict | None = None,
    ) -> list[DocumentChunk]:
        """Chunk a document into extraction-ready segments.

        Args:
            text: Full document text (Docling markdown export or plain text).
                  This is the offset source of truth — all char_start/char_end
                  values reference positions in this string.
            document_id: Source document identifier for deterministic chunk IDs.
            docling_json: Docling lossless JSON if available. None for plain text.

        Returns:
            Ordered list of DocumentChunk objects.
            Empty list if text is empty or whitespace-only.
        """
        if not text or not text.strip():
            log.warning("empty_document", document_id=document_id)
            return []

        # Path A: Docling-structured chunking
        candidates = None
        if docling_json is not None:
            sections = self._parse_docling_sections(docling_json)
            if sections is not None:
                candidates = self._group_into_candidates(sections, text)

        # Path B: Plain text fallback
        if candidates is None:
            candidates = self._chunk_plain_text(text)

        # Split any oversized text candidates at sentence boundaries
        expanded: list[dict] = []
        for cand in candidates:
            if cand["element_type"] == "table":
                # Tables are atomic — warn if oversized but keep intact
                if cand["token_estimate"] > self._effective_cap:
                    log.warning(
                        "oversized_table_chunk",
                        tokens=cand["token_estimate"],
                        cap=self._effective_cap,
                    )
                expanded.append(cand)
            elif cand["token_estimate"] > self._effective_cap:
                sub_texts = self._split_oversized(cand["text"])
                search_from = cand["char_start"]
                for st in sub_texts:
                    pos = text.find(st, search_from)
                    if pos == -1:
                        pos = search_from
                    expanded.append({
                        "text": st,
                        "char_start": pos,
                        "char_end": pos + len(st),
                        "section_id": cand["section_id"],
                        "element_type": "text",
                        "token_estimate": self._estimate_tokens(st),
                    })
                    search_from = pos + len(st)
            else:
                expanded.append(cand)

        # Merge undersized adjacent candidates
        merged = self._merge_small_candidates(expanded)

        # Build DocumentChunk objects
        chunks: list[DocumentChunk] = []
        for i, cand in enumerate(merged):
            chunk = DocumentChunk(
                chunk_id=self._generate_chunk_id(document_id, i),
                text=cand["text"],
                char_start=cand["char_start"],
                char_end=cand["char_end"],
                section_id=cand["section_id"],
                sentence_offsets=self._compute_sentence_offsets(cand["text"]),
                token_count_estimate=self._estimate_tokens(cand["text"]),
            )
            chunks.append(chunk)

        # Apply overlap
        if self._config.chunk_overlap_tokens > 0 and len(chunks) > 1:
            chunks = self._apply_overlap(chunks)

        return chunks

    # --- Internal methods ---

    @staticmethod
    def _default_token_estimate(text: str) -> int:
        """Estimate token count via character heuristic. ~4 chars per token."""
        return len(text) // 4

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count using configured counter."""
        return self._token_counter(text)

    def _split_sentences(self, text: str) -> list[str]:
        """Split text into sentences using pySBD.

        Filters out empty strings from pySBD output.
        """
        raw = self._segmenter.segment(text)
        return [s for s in raw if s.strip()]

    def _compute_sentence_offsets(self, text: str) -> list[tuple[int, int]]:
        """Compute character-level (start, end) positions for each sentence.

        Uses sequential pointer scanning to handle repeated sentences:
        after finding sentence N at position P, the search for sentence N+1
        starts from P + len(sentence_N). This avoids returning the same
        offset for duplicate sentences.

        Returns list of (start, end) tuples. Positions are relative to
        the input text string.
        """
        sentences = self._split_sentences(text)
        offsets: list[tuple[int, int]] = []
        search_from = 0

        for sentence in sentences:
            pos = text.find(sentence, search_from)
            if pos == -1:
                # Fallback: try from beginning (shouldn't happen with sequential scan)
                pos = text.find(sentence)
                if pos == -1:
                    continue
            offsets.append((pos, pos + len(sentence)))
            search_from = pos + len(sentence)

        return offsets

    def _parse_docling_sections(self, docling_json: dict) -> list[dict] | None:
        """Extract ordered structural elements from Docling JSON.

        Walks the texts list in document order (texts are ordered by position
        in the source document). Uses label to detect section headers.
        Tables are matched separately from the tables list.

        Returns list of dicts with keys:
            text: str — the element text content
            section_id: str | None — parent section header text
            element_type: str — 'text', 'table', 'list', 'section-header'

        Returns None if JSON structure is unexpected or cannot be parsed.
        When None is returned, the caller falls back to plain-text chunking.
        """
        try:
            texts_list = docling_json.get("texts")
            if not texts_list:
                return None

            tables_list = docling_json.get("tables", [])

            # Build ordered elements from texts list (already in document order)
            elements: list[dict] = []
            current_section: str | None = None

            # Track table positions in body.children for interleaving
            body = docling_json.get("body", {})
            body_children = body.get("children", [])

            # Build table ref set for quick lookup
            table_map: dict[str, dict] = {}
            for item in tables_list:
                ref = item.get("self_ref", "")
                if ref:
                    table_map[ref] = item

            # Determine table insertion points from body ordering
            # Map: text_ref that comes just before a table -> table item
            table_insert_after: dict[int, list[dict]] = {}
            last_text_idx = -1
            for child_ref in body_children:
                cref = child_ref.get("cref", "")
                if cref.startswith("#/texts/"):
                    last_text_idx = int(cref.split("/")[-1])
                elif cref in table_map:
                    table_insert_after.setdefault(last_text_idx, []).append(
                        table_map[cref]
                    )

            for i, item in enumerate(texts_list):
                label = item.get("label", "text")
                text_content = item.get("text", "").strip()

                if not text_content:
                    # Check if tables should be inserted after this empty text
                    if i in table_insert_after:
                        for tbl in table_insert_after[i]:
                            table_text = self._table_to_text(tbl)
                            if table_text:
                                elements.append({
                                    "text": table_text,
                                    "section_id": current_section,
                                    "element_type": "table",
                                })
                    continue

                # Skip page headers/footers
                if label in ("page_header", "page_footer"):
                    # Still check for tables after this text
                    if i in table_insert_after:
                        for tbl in table_insert_after[i]:
                            table_text = self._table_to_text(tbl)
                            if table_text:
                                elements.append({
                                    "text": table_text,
                                    "section_id": current_section,
                                    "element_type": "table",
                                })
                    continue

                if label == "section_header":
                    current_section = text_content
                    elements.append({
                        "text": text_content,
                        "section_id": current_section,
                        "element_type": "section-header",
                    })
                else:
                    elements.append({
                        "text": text_content,
                        "section_id": current_section,
                        "element_type": "text",
                    })

                # Insert any tables that follow this text in body order
                if i in table_insert_after:
                    for tbl in table_insert_after[i]:
                        table_text = self._table_to_text(tbl)
                        if table_text:
                            elements.append({
                                "text": table_text,
                                "section_id": current_section,
                                "element_type": "table",
                            })

            if not elements:
                return None

            return elements

        except (KeyError, TypeError, AttributeError) as e:
            log.warning("docling_parse_failed", error=str(e))
            return None

    @staticmethod
    def _table_to_text(table_item: dict) -> str:
        """Extract the first cell texts from a Docling table for matching.

        Returns a simplified pipe-delimited string for the header row only,
        used to locate the table in the extracted text. The actual table text
        is sliced from the extracted text at match time.
        """
        data = table_item.get("data", {})
        grid = data.get("grid")
        if not grid or not grid[0]:
            return ""

        # Return first row cell texts joined by pipe — used as search key
        cells = [cell.get("text", "").strip() for cell in grid[0]]
        return "| " + " | ".join(c for c in cells if c) + " |"

    @staticmethod
    def _find_table_extent(text: str, start: int) -> int:
        """Find the end of a markdown table starting at position start.

        Tables end at the first line that doesn't start with '|' or is empty.
        """
        pos = start
        while pos < len(text):
            line_end = text.find("\n", pos)
            if line_end == -1:
                return len(text)
            line = text[pos:line_end].strip()
            if not line.startswith("|"):
                return pos
            pos = line_end + 1
        return len(text)

    def _group_into_candidates(
        self,
        sections: list[dict],
        text: str,
    ) -> list[dict]:
        """Group structural elements into candidate chunks.

        Groups consecutive text elements under the same section header.
        Tables are always their own candidate (atomic).
        Respects effective token cap when accumulating text elements.

        Character positions are found by scanning the `text` argument
        (the original document text), not from Docling JSON positions.
        """
        candidates: list[dict] = []
        search_from = 0

        # Current accumulator
        current_texts: list[str] = []
        current_section: str | None = None
        current_start: int = -1

        def flush_current() -> None:
            nonlocal current_texts, current_section, current_start
            if not current_texts:
                return
            combined = "\n\n".join(current_texts)
            # Find in original text
            pos = text.find(current_texts[0], current_start if current_start >= 0 else 0)
            if pos == -1:
                pos = max(0, current_start)

            # Find end by locating the last text segment
            last_text = current_texts[-1]
            end_search = pos
            for ct in current_texts:
                found = text.find(ct, end_search)
                if found >= 0:
                    end_search = found + len(ct)
                else:
                    end_search += len(ct)

            candidates.append({
                "text": combined,
                "char_start": pos,
                "char_end": end_search,
                "section_id": current_section,
                "element_type": "text",
                "token_estimate": self._estimate_tokens(combined),
            })
            current_texts = []
            current_start = end_search

        for elem in sections:
            elem_type = elem["element_type"]
            elem_text = elem["text"]
            elem_section = elem["section_id"]

            if elem_type == "table":
                flush_current()
                # elem_text is a header-row search key from _table_to_text
                # Find any cell text from the key in the original text
                # to locate where the table starts
                # Extract cell texts from the search key
                cells = [c.strip() for c in elem_text.split("|") if c.strip()]
                table_pos = -1
                if cells:
                    # Search for first non-trivial cell
                    for cell in cells:
                        if len(cell) > 2:
                            # Look for "| cell" pattern in text
                            idx = text.find(cell, search_from)
                            if idx >= 0:
                                # Walk back to find the start of the table row
                                line_start = text.rfind("\n", 0, idx)
                                line_start = line_start + 1 if line_start >= 0 else 0
                                if text[line_start:line_start + 1] == "|":
                                    table_pos = line_start
                                    break

                if table_pos == -1:
                    log.warning("table_not_found_in_text", search_key=elem_text[:40])
                    continue

                # Find full table extent
                table_end = self._find_table_extent(text, table_pos)
                table_text = text[table_pos:table_end].rstrip("\n")

                candidates.append({
                    "text": table_text,
                    "char_start": table_pos,
                    "char_end": table_pos + len(table_text),
                    "section_id": elem_section,
                    "element_type": "table",
                    "token_estimate": self._estimate_tokens(table_text),
                })
                search_from = table_pos + len(table_text)
                current_start = search_from
                continue

            if elem_type == "section-header":
                # Section headers start a new grouping
                flush_current()
                current_section = elem_section

                # Find section header position in text
                pos = text.find(elem_text, search_from)
                if pos >= 0:
                    search_from = pos
                    current_start = pos
                continue

            # Regular text element
            if elem_section != current_section:
                flush_current()
                current_section = elem_section

            # Check if adding would exceed cap
            trial = "\n\n".join(current_texts + [elem_text]) if current_texts else elem_text
            if self._estimate_tokens(trial) > self._effective_cap and current_texts:
                flush_current()

            if not current_texts:
                # Track where this group starts
                pos = text.find(elem_text, search_from)
                if pos >= 0:
                    current_start = pos
                    search_from = pos
                else:
                    current_start = search_from

            current_texts.append(elem_text)

            # Advance search_from past this element
            pos = text.find(elem_text, search_from)
            if pos >= 0:
                search_from = pos + len(elem_text)

        flush_current()
        return candidates

    def _chunk_plain_text(self, text: str) -> list[dict]:
        """Chunk plain text at paragraph boundaries.

        Splits at double-newline. Groups paragraphs into candidates
        approaching effective_cap. Falls back to sentence splitting
        for single-block text with no double-newlines.
        """
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

        if len(paragraphs) <= 1:
            # Single block — use sentence splitting
            sub_texts = self._split_oversized(text.strip())
            candidates: list[dict] = []
            search_from = 0
            for st in sub_texts:
                pos = text.find(st, search_from)
                if pos == -1:
                    pos = search_from
                candidates.append({
                    "text": st,
                    "char_start": pos,
                    "char_end": pos + len(st),
                    "section_id": None,
                    "element_type": "text",
                    "token_estimate": self._estimate_tokens(st),
                })
                search_from = pos + len(st)
            return candidates

        # Group paragraphs toward effective_cap
        candidates = []
        current_paras: list[str] = []
        current_start = 0
        search_from = 0

        for para in paragraphs:
            trial = "\n\n".join(current_paras + [para]) if current_paras else para
            if self._estimate_tokens(trial) > self._effective_cap and current_paras:
                combined = "\n\n".join(current_paras)
                pos = text.find(current_paras[0], current_start)
                if pos == -1:
                    pos = current_start
                # Find end
                last_para = current_paras[-1]
                end_pos = text.find(last_para, pos)
                if end_pos >= 0:
                    end_pos += len(last_para)
                else:
                    end_pos = pos + len(combined)
                candidates.append({
                    "text": combined,
                    "char_start": pos,
                    "char_end": end_pos,
                    "section_id": None,
                    "element_type": "text",
                    "token_estimate": self._estimate_tokens(combined),
                })
                current_start = end_pos
                current_paras = []

            if not current_paras:
                p = text.find(para, current_start)
                if p >= 0:
                    current_start = p
            current_paras.append(para)

        # Flush remaining
        if current_paras:
            combined = "\n\n".join(current_paras)
            pos = text.find(current_paras[0], current_start)
            if pos == -1:
                pos = current_start
            last_para = current_paras[-1]
            end_pos = text.find(last_para, pos)
            if end_pos >= 0:
                end_pos += len(last_para)
            else:
                end_pos = pos + len(combined)
            candidates.append({
                "text": combined,
                "char_start": pos,
                "char_end": end_pos,
                "section_id": None,
                "element_type": "text",
                "token_estimate": self._estimate_tokens(combined),
            })

        return candidates

    def _split_oversized(self, text: str) -> list[str]:
        """Split text exceeding effective cap at sentence boundaries.

        Uses pySBD to find sentence boundaries, then groups sentences
        into segments approaching effective_cap. Never splits mid-sentence.
        """
        sentences = self._split_sentences(text)
        if not sentences:
            return [text] if text.strip() else []

        segments: list[str] = []
        current: list[str] = []

        for sentence in sentences:
            trial = "".join(current + [sentence])
            if self._estimate_tokens(trial) > self._effective_cap and current:
                segments.append("".join(current))
                current = []
            current.append(sentence)

        if current:
            segments.append("".join(current))

        return segments

    def _merge_small_candidates(
        self,
        candidates: list[dict],
        min_tokens: int = 100,
    ) -> list[dict]:
        """Merge undersized adjacent candidates sharing the same section.

        Only merges if combined size <= effective_cap and both candidates
        have the same section_id. Does not merge across section boundaries.
        Does not merge table candidates with text candidates.
        """
        if not candidates:
            return []

        merged: list[dict] = [candidates[0]]

        for cand in candidates[1:]:
            prev = merged[-1]

            # Can merge?
            same_section = prev["section_id"] == cand["section_id"]
            both_text = prev["element_type"] != "table" and cand["element_type"] != "table"
            prev_small = prev["token_estimate"] < min_tokens
            cand_small = cand["token_estimate"] < min_tokens
            either_small = prev_small or cand_small

            if same_section and both_text and either_small:
                combined_text = prev["text"] + "\n\n" + cand["text"]
                combined_tokens = self._estimate_tokens(combined_text)
                if combined_tokens <= self._effective_cap:
                    merged[-1] = {
                        "text": combined_text,
                        "char_start": prev["char_start"],
                        "char_end": cand["char_end"],
                        "section_id": prev["section_id"],
                        "element_type": "text",
                        "token_estimate": combined_tokens,
                    }
                    continue

            merged.append(cand)

        return merged

    def _apply_overlap(
        self,
        chunks: list[DocumentChunk],
    ) -> list[DocumentChunk]:
        """Prepend overlap from previous chunk to each subsequent chunk.

        Takes the final overlap_chars characters from the previous chunk's
        NON-OVERLAP text and prepends to the current chunk.

        Does NOT modify char_start/char_end — these always reference the
        original document text positions (non-overlap content only).

        Skips if chunk_overlap_tokens == 0 or this is the first chunk.
        """
        if self._overlap_chars == 0:
            return chunks

        result = [chunks[0]]

        for i in range(1, len(chunks)):
            prev = result[i - 1]
            curr = chunks[i]

            # Get non-overlap text from previous chunk
            prev_non_overlap = prev.text[prev.overlap_char_count:]
            overlap_text = prev_non_overlap[-self._overlap_chars:]

            if not overlap_text:
                result.append(curr)
                continue

            new_text = overlap_text + curr.text
            new_offsets = self._compute_sentence_offsets(new_text)

            new_chunk = DocumentChunk(
                chunk_id=curr.chunk_id,
                text=new_text,
                char_start=curr.char_start,
                char_end=curr.char_end,
                section_id=curr.section_id,
                sentence_offsets=new_offsets,
                token_count_estimate=self._estimate_tokens(new_text),
                overlap_char_count=len(overlap_text),
            )
            result.append(new_chunk)

        return result

    @staticmethod
    def _generate_chunk_id(document_id: str, index: int) -> str:
        """Deterministic SHA-256 based chunk ID, truncated to 16 hex chars.

        hash_input = f"{document_id}:{index}"
        """
        hash_input = f"{document_id}:{index}"
        return hashlib.sha256(hash_input.encode()).hexdigest()[:16]
