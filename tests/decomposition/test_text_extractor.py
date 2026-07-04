"""CP4 text extractor tests (D309).

Per-format title source + body extraction; HTML tag-strip; first-500
words cap; unsupported skip-with-reason; empty file; plain-text
filename stem title.
"""

from __future__ import annotations

from pathlib import Path

from src.decomposition.text_extractor import extract_text


def test_text_extractor_md_uses_filename_stem(tmp_path: Path):
    p = tmp_path / "ops_plan.md"
    p.write_text("# Plan\n\nBody text here.\n")
    res = extract_text(p)
    assert res.skipped is False
    assert res.title == "ops_plan"
    assert "Body text here" in (res.body or "")


def test_text_extractor_txt_uses_filename_stem(tmp_path: Path):
    p = tmp_path / "memo.txt"
    p.write_text("plain text content")
    res = extract_text(p)
    assert res.title == "memo"
    assert res.body == "plain text content"


def test_text_extractor_html_strips_script_and_style(tmp_path: Path):
    p = tmp_path / "page.html"
    p.write_text(
        "<html><head><style>.x { color: red; }</style>"
        "<script>alert(1)</script></head>"
        "<body><p>Visible body text.</p></body></html>"
    )
    res = extract_text(p)
    assert res.title == "page"
    body = res.body or ""
    assert "Visible body text" in body
    assert "alert" not in body
    assert "color: red" not in body


def test_text_extractor_first_500_words_cap_is_whitespace_tokenized(tmp_path: Path):
    p = tmp_path / "big.txt"
    words = [f"w{i}" for i in range(700)]
    p.write_text(" ".join(words))
    res = extract_text(p)
    body_words = (res.body or "").split()
    assert len(body_words) == 500
    assert body_words[0] == "w0"
    assert body_words[-1] == "w499"


def test_text_extractor_unsupported_suffix_returns_skipped(tmp_path: Path):
    p = tmp_path / "blob.bin"
    p.write_bytes(b"\x00\x01\x02")
    res = extract_text(p)
    assert res.skipped is True
    assert res.reason == "unsupported_suffix"
    assert res.title is None
    assert res.body is None


def test_text_extractor_empty_file_handled_gracefully(tmp_path: Path):
    p = tmp_path / "empty.txt"
    p.write_text("")
    res = extract_text(p)
    assert res.skipped is False
    assert res.title == "empty"
    assert res.body == ""
