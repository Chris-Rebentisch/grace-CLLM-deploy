#!/usr/bin/env python3
"""Warn when spec instructs CREATE OR REPLACE without adjacent source quote (P59-W15).

Usage: python3 scripts/lint/check_spec_ddl_quote.py <spec-path>
Exit 0 if ok, 1 if violations.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CREATE_OR_REPLACE = re.compile(
    r"CREATE\s+OR\s+REPLACE\s+(?:FUNCTION|TRIGGER)",
    re.IGNORECASE,
)
FENCED = re.compile(r"^```", re.MULTILINE)
PATH_RANGE = re.compile(
    r"`((?:src|alembic)/[^`\s]+)`(?:\s*:\s*\d+\s*[-–]\s*\d+)?",
    re.IGNORECASE,
)


def violations(text: str) -> list[str]:
    lines = text.splitlines()
    issues: list[str] = []
    for i, line in enumerate(lines):
        if not CREATE_OR_REPLACE.search(line):
            continue
        window_start = max(0, i - 25)
        window = "\n".join(lines[window_start : i + 1])
        if FENCED.search(window):
            continue
        if PATH_RANGE.search(window):
            continue
        issues.append(f"line {i + 1}: CREATE OR REPLACE without adjacent fenced body or path range")
    return issues


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) != 1:
        print("Usage: python3 scripts/lint/check_spec_ddl_quote.py <spec-path>", file=sys.stderr)
        return 2
    path = Path(argv[0]).resolve()
    if not path.is_file():
        print(f"ERROR: not found: {path}", file=sys.stderr)
        return 2
    issues = violations(path.read_text(encoding="utf-8"))
    if not issues:
        print(f"OK: DDL quote discipline passed for {path.name}")
        return 0
    print(f"FAIL: DDL quote discipline ({len(issues)} issue(s)) for {path.name}", file=sys.stderr)
    for issue in issues:
        print(f"  - {issue}", file=sys.stderr)
    print(
        "  Hint: paste trigger/function body in a fenced block or cite `file.py:NN–MM` "
        "immediately before the instruction. See runtime-trace-format.md § DDL/trigger discipline.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
