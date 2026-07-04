"""Contract tests for the local-first issue tracker (scripts/issue.py)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "issue.py"


@pytest.fixture()
def tracker(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("issue_tracker", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["issue_tracker"] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod, "ISSUES_DIR", tmp_path / ".issues")
    monkeypatch.setattr(mod, "BUGS_MD", tmp_path / "BUGS.md")
    return mod


def test_new_list_close_index_lifecycle(tracker, capsys):
    tracker.main(["new", "Retrieval empty after re-ratify",
                  "--severity", "high", "--area", "retrieval"])
    files = list((tracker.ISSUES_DIR).glob("ISS-0001-*.md"))
    assert len(files) == 1
    meta, body = tracker._split(files[0])
    assert meta["id"] == "ISS-0001"
    assert meta["status"] == "open"
    assert meta["severity"] == "high"
    assert meta["github_issue"] is None
    assert "## Repro / evidence" in body

    tracker.main(["list"])
    out = capsys.readouterr().out
    assert "ISS-0001" in out and "open" in out

    tracker.main(["close", "ISS-0001", "--note", "fixed in abc123"])
    meta, body = tracker._split(files[0])
    assert meta["status"] == "fixed"
    assert "closed" in meta
    assert "fixed in abc123" in body

    # Index is regenerated and marks it fixed.
    bugs = tracker.BUGS_MD.read_text()
    assert "ISS-0001" in bugs and "| fixed |" in bugs
    assert "0 open/in-progress" in bugs


def test_ids_increment_and_slugs_sanitize(tracker):
    tracker.main(["new", "First!"])
    tracker.main(["new", "Sécond: with / weird — chars?"])
    names = sorted(p.name for p in tracker.ISSUES_DIR.glob("ISS-*.md"))
    assert names[0].startswith("ISS-0001-")
    assert names[1].startswith("ISS-0002-")
    assert " " not in names[1] and "/" not in names[1]


def test_push_dry_run_makes_no_changes(tracker, capsys):
    tracker.main(["new", "Push candidate"])
    tracker.main(["push", "ISS-0001", "--dry-run"])
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    meta, _ = tracker._split(next(tracker.ISSUES_DIR.glob("ISS-0001-*.md")))
    assert meta["github_issue"] is None  # untouched


def test_push_twice_refused(tracker):
    tracker.main(["new", "Already mirrored"])
    path = next(tracker.ISSUES_DIR.glob("ISS-0001-*.md"))
    meta, body = tracker._split(path)
    meta["github_issue"] = 42
    meta["github_url"] = "https://github.com/x/y/issues/42"
    path.write_text(tracker._join(meta, body), encoding="utf-8")
    with pytest.raises(SystemExit, match="already pushed"):
        tracker.main(["push", "ISS-0001"])


def test_find_accepts_bare_number(tracker, capsys):
    tracker.main(["new", "Findable"])
    tracker.main(["show", "1"])
    assert "Findable" in capsys.readouterr().out
