"""Suffix-dispatched text extractor for the Decomposition pipeline (D309).

Returns ``TextExtraction(title, body, skipped, reason)`` per document.
Body is truncated to the first 500 whitespace-tokenized words. Title
source depends on suffix:

* PDF/DOCX/XLSX/PPTX → Docling document-level title; Docling-extracted
  body.
* MD/TXT → filename stem for title; raw text body.
* HTML → filename stem for title; single-pass tag-stripped body
  (``<script>`` and ``<style>`` removed).
* CSV → filename stem for title; raw text body.
* Anything else → ``skipped=True, reason='unsupported_suffix'``.

Docling delegate runs for binary formats; stdlib otherwise. The
extractor never raises on missing files — callers should check
``.exists()``; corrupt-file handling is best-effort with
``errors='replace'``.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict


_DOCLING_SUFFIXES = {".pdf", ".docx", ".xlsx", ".pptx"}
_PLAIN_SUFFIXES = {".md", ".txt"}
_HTML_SUFFIXES = {".html", ".htm"}
_CSV_SUFFIXES = {".csv"}

_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


class TextExtraction(BaseModel):
    """Result of ``extract_text`` for a single file (D309)."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    body: str | None = None
    skipped: bool = False
    reason: str | None = None


def _truncate_first_500_words(text: str) -> str:
    """Whitespace-tokenize and keep first 500 tokens."""
    if not text:
        return ""
    tokens = text.split()
    return " ".join(tokens[:500])


def _strip_html(raw: str) -> str:
    """Remove ``<script>``/``<style>`` blocks, then strip remaining tags."""
    cleaned = _SCRIPT_STYLE_RE.sub(" ", raw)
    cleaned = _TAG_RE.sub(" ", cleaned)
    return cleaned


def _docling_extract(path: Path) -> tuple[str | None, str | None]:
    """Delegate to Docling for binary formats. Returns ``(title, body)``.

    On any extraction error, returns ``(filename_stem, "")`` so callers
    surface the document with an empty body rather than aborting the
    Layer 1 walk.
    """
    try:
        from docling.document_converter import DocumentConverter

        converter = DocumentConverter()
        result = converter.convert(str(path))
        doc = result.document
        body = doc.export_to_text() or ""
        title = getattr(doc, "name", None) or path.stem
        return title, body
    except Exception:  # noqa: BLE001 — best-effort
        return path.stem, ""


def extract_text(file_path: Path) -> TextExtraction:
    """Extract title + first-500-words body from ``file_path``.

    Unsupported suffixes return ``skipped=True``; the file is still
    surfaced in the Layer 1 inventory but excluded from Layer 2 input.
    """
    suffix = file_path.suffix.lower()

    if suffix in _DOCLING_SUFFIXES:
        title, body = _docling_extract(file_path)
        return TextExtraction(
            title=title,
            body=_truncate_first_500_words(body or ""),
        )

    if suffix in _PLAIN_SUFFIXES:
        body = file_path.read_text(encoding="utf-8", errors="replace")
        return TextExtraction(
            title=file_path.stem,
            body=_truncate_first_500_words(body),
        )

    if suffix in _HTML_SUFFIXES:
        raw = file_path.read_text(encoding="utf-8", errors="replace")
        cleaned = _strip_html(raw)
        return TextExtraction(
            title=file_path.stem,
            body=_truncate_first_500_words(cleaned),
        )

    if suffix in _CSV_SUFFIXES:
        body = file_path.read_text(encoding="utf-8", errors="replace")
        return TextExtraction(
            title=file_path.stem,
            body=_truncate_first_500_words(body),
        )

    return TextExtraction(skipped=True, reason="unsupported_suffix")
