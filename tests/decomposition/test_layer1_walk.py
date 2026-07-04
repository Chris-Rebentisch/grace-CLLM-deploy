"""CP5 Layer 1 walk tests (D316).

Covers deep nesting, mixed suffixes, hidden-file exclusion,
symlink default-deny, depth tracking, Pydantic round-trip, and
deterministic 10-by-size + 5-by-recency sample-title selection.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

from src.decomposition.config import DecompositionConfig, Layer1Config
from src.decomposition.layer1_walk import walk_archive
from src.decomposition.models import Layer1Summary


def _config(exclude_hidden: bool = True, max_depth: int | None = None) -> DecompositionConfig:
    cfg = DecompositionConfig()
    cfg.layer1 = Layer1Config(exclude_hidden=exclude_hidden, max_depth=max_depth)
    return cfg


def test_layer1_walk_handles_deep_nesting(tmp_path: Path):
    deep = tmp_path / "a" / "b" / "c" / "d"
    deep.mkdir(parents=True)
    (deep / "leaf.txt").write_text("leaf")
    summary = walk_archive(tmp_path, _config())
    assert summary.total_files == 1
    leaf = summary.files[0]
    assert leaf.relative_path == os.path.join("a", "b", "c", "d", "leaf.txt")
    assert leaf.depth == 5


def test_layer1_walk_captures_mixed_suffixes(tmp_path: Path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "b.md").write_text("y")
    (tmp_path / "c.bin").write_bytes(b"z")
    summary = walk_archive(tmp_path, _config())
    suffixes = sorted(f.suffix for f in summary.files)
    assert suffixes == [".bin", ".md", ".txt"]


def test_layer1_walk_excludes_hidden_files_by_default(tmp_path: Path):
    (tmp_path / "visible.txt").write_text("v")
    (tmp_path / ".hidden_file").write_text("h")
    (tmp_path / ".hidden_dir").mkdir()
    (tmp_path / ".hidden_dir" / "x.txt").write_text("x")
    summary = walk_archive(tmp_path, _config(exclude_hidden=True))
    rels = {f.relative_path for f in summary.files}
    assert "visible.txt" in rels
    assert all(not r.startswith(".") and ".hidden" not in r for r in rels)


def test_layer1_walk_includes_hidden_when_disabled(tmp_path: Path):
    (tmp_path / "v.txt").write_text("v")
    (tmp_path / ".hidden.txt").write_text("h")
    summary = walk_archive(tmp_path, _config(exclude_hidden=False))
    rels = {f.relative_path for f in summary.files}
    assert ".hidden.txt" in rels


@pytest.mark.skipif(sys.platform == "win32", reason="symlink test posix-only")
def test_layer1_walk_does_not_follow_symlinks(tmp_path: Path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "inside.txt").write_text("x")
    target = tmp_path / "target"
    target.mkdir()
    (target / "outside.txt").write_text("o")
    link = real / "linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation not permitted in this env")
    summary = walk_archive(real, _config())
    rels = {f.relative_path for f in summary.files}
    assert "inside.txt" in rels
    # Symlinked directory contents must not appear.
    assert not any(r.endswith("outside.txt") for r in rels)


def test_layer1_summary_pydantic_round_trip(tmp_path: Path):
    (tmp_path / "a.txt").write_text("aa")
    summary = walk_archive(tmp_path, _config())
    raw = summary.model_dump(mode="json")
    reload = Layer1Summary.model_validate(raw)
    assert reload.total_files == summary.total_files
    assert reload.files[0].relative_path == summary.files[0].relative_path


def test_layer1_sample_titles_are_deterministic(tmp_path: Path):
    """10-by-size + 5-by-recency selection is stable on tie-break.

    Builds a flat folder of 12 files of varying size and mtime so the
    by-size top-10 and by-recency top-5 are well-defined.
    """
    folder = tmp_path / "flat"
    folder.mkdir()
    base_ts = time.time() - 1000
    expected_top_10 = []
    for i in range(12):
        f = folder / f"doc_{i:02d}.txt"
        f.write_text("X" * (i + 1) * 10)  # increasing size
        os.utime(f, (base_ts + i, base_ts + i))  # increasing mtime
        expected_top_10.append(f.stem)

    s1 = walk_archive(tmp_path, _config())
    s2 = walk_archive(tmp_path, _config())
    flat = next(folder for folder in s1.folders if folder.path.endswith("flat"))
    flat2 = next(folder for folder in s2.folders if folder.path.endswith("flat"))
    assert flat.sample_titles == flat2.sample_titles
    # First entry must be the largest file (doc_11).
    assert flat.sample_titles[0] == "doc_11"
