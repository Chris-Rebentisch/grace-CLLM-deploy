#!/usr/bin/env python3
"""Lint spec §6 checkpoints for required Runtime trace blocks (cross-boundary CPs).

Guarantees declaration, not correctness:
  - Forces an explicit call-graph table on high-risk CPs.
  - Files(CP) ⊆ paths(trace) ⊆ paths(§2 Created/Edited).

Usage:
  python3 scripts/lint/check_runtime_trace.py <spec-path>

Exit 0 on pass, 1 on failure. Opt-out: GRACE_SKIP_RUNTIME_TRACE=1
Per-chunk skip: GRACE_RUNTIME_TRACE_SKIP=58,59 (comma-separated chunk ids)
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Dispatch / triage class — supplement structural triggers (not sole gate).
# registry/subprocess use word boundaries to avoid adapter_registry / prose noise.
DISPATCH_KEYWORD_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsubprocess\b", re.IGNORECASE),
    re.compile(r"\bregistry\b", re.IGNORECASE),
    re.compile(r"subprocess\.run", re.IGNORECASE),
    re.compile(r"\bpopen\b", re.IGNORECASE),
    re.compile(r"\badapterresult\b", re.IGNORECASE),
    re.compile(r"\basyncio\.run\b", re.IGNORECASE),
    re.compile(r"batch flush", re.IGNORECASE),
    re.compile(r"passed_to_t", re.IGNORECASE),
    re.compile(r"run_tier4", re.IGNORECASE),
    re.compile(r"_flush_", re.IGNORECASE),
    re.compile(r"deferred dispatch", re.IGNORECASE),
    re.compile(r"background task", re.IGNORECASE),
    re.compile(r"buffered write", re.IGNORECASE),
    re.compile(r"\bqueue\b", re.IGNORECASE),
)

# No backend call graph — structural/keyword triggers are N/A.
_NON_BACKEND_ONLY_ROOTS = frozenset({"scripts", "docs", "frontend"})

PATH_IN_BACKTICKS = re.compile(
    r"`((?:src|tests|frontend|alembic|config|scripts|docs)/[^`\s]+)`"
)
CP_LABEL = re.compile(r"\*\[CP(\d+)\]\*", re.IGNORECASE)
SECTION2_START = re.compile(r"^## 2\.\s", re.MULTILINE)
SECTION6_START = re.compile(r"^## 6\.\s", re.MULTILINE)
NEXT_H2 = re.compile(r"^## \d+\.\s", re.MULTILINE)
STEP_OR_CP_HEAD = re.compile(
    r"^(?:### (?:Step \d+|CP\d+)|### CP\d+ —)",
    re.MULTILINE | re.IGNORECASE,
)
TRACE_HEADER = re.compile(r"^\*\*Runtime trace:\*\*", re.MULTILINE | re.IGNORECASE)
PREREQ = re.compile(r"\*\*Prerequisites?:\*\*", re.IGNORECASE)


@dataclass
class CheckpointBlock:
    label: str
    title: str
    body: str
    files: set[str] = field(default_factory=set)


def _slice_between(text: str, start_pat: re.Pattern[str], end_pat: re.Pattern[str] | None) -> str:
    m = start_pat.search(text)
    if not m:
        return ""
    start = m.start()
    if end_pat:
        m2 = end_pat.search(text, m.end())
        end = m2.start() if m2 else len(text)
    else:
        end = len(text)
    return text[start:end]


def _section2_paths(text: str) -> set[str]:
    block = _slice_between(text, SECTION2_START, SECTION6_START)
    if not block:
        block = _slice_between(text, SECTION2_START, NEXT_H2)
    paths: set[str] = set()
    for m in PATH_IN_BACKTICKS.finditer(block):
        p = m.group(1).strip()
        if "{" in p or "*" in p:
            continue
        paths.add(p)
    return paths


def _parse_checkpoints(section6: str) -> list[CheckpointBlock]:
    heads = list(STEP_OR_CP_HEAD.finditer(section6))
    blocks: list[CheckpointBlock] = []
    for i, hm in enumerate(heads):
        start = hm.start()
        end = heads[i + 1].start() if i + 1 < len(heads) else len(section6)
        chunk = section6[start:end]
        title = hm.group(0).strip()
        label_m = CP_LABEL.search(chunk)
        label = f"CP{label_m.group(1)}" if label_m else title
        files: set[str] = set()
        for line in chunk.splitlines():
            if "**Files:**" in line or "**Files touched:**" in line:
                for m in PATH_IN_BACKTICKS.finditer(line):
                    p = m.group(1).strip()
                    if "{" not in p and "*" not in p:
                        files.add(p)
        blocks.append(CheckpointBlock(label=label, title=title, body=chunk, files=files))
    return blocks


_COLLAPSED_NON_SRC_ROOTS = frozenset({"tests", "frontend", "alembic", "scripts", "config"})


def _file_zone(path: str) -> str:
    """Collapse paths into call-graph-relevant zones (not per-file for tests)."""
    parts = path.split("/")
    if not parts:
        return path
    root = parts[0]
    if root in _COLLAPSED_NON_SRC_ROOTS:
        return root
    if root != "src" or len(parts) < 2:
        return root
    if parts[1] == "api":
        return "src/api"
    if len(parts) >= 3 and parts[1] == "ingestion" and parts[2] == "communications":
        return "src/ingestion/communications"
    if parts[1] == "ingestion":
        return "src/ingestion/root"
    return f"src/{parts[1]}"


def _src_zones(files: set[str]) -> set[str]:
    return {_file_zone(p) for p in files if p.startswith("src/")}


def _files_are_non_backend_only(files: set[str]) -> bool:
    """CPs that only touch scripts/, docs/, or frontend/ have no src call graph."""
    if not files:
        return False
    return all(p.split("/")[0] in _NON_BACKEND_ONLY_ROOTS for p in files)


def _dispatch_keyword_hit(body: str) -> str | None:
    for pat in DISPATCH_KEYWORD_PATTERNS:
        if pat.search(body):
            return pat.pattern
    return None


def _trace_required(files: set[str], body: str) -> tuple[bool, str]:
    if _files_are_non_backend_only(files):
        return False, ""

    src_zones = _src_zones(files)
    all_zones = {_file_zone(p) for p in files}
    non_src_zones = all_zones - src_zones
    # Tests and migrations accompany most CPs; not runtime call-graph boundaries.
    cross_stack = non_src_zones & {"frontend", "scripts", "config"}
    reasons: list[str] = []

    if "src/api" in src_zones and len(src_zones) > 1:
        reasons.append("Files span src/api/ and non-api src/ modules")

    if len(src_zones) >= 2:
        reasons.append(f"Files span {len(src_zones)} src zones: {', '.join(sorted(src_zones))}")

    if src_zones and cross_stack:
        reasons.append(
            "Files span src/ modules and "
            f"{', '.join(sorted(cross_stack))} (cross-stack coordination)"
        )

    if PREREQ.search(body):
        reasons.append("block has **Prerequisites:**")

    kw_hit = _dispatch_keyword_hit(body)
    if kw_hit:
        reasons.append(f"keyword trigger: {kw_hit!r}")

    if not reasons:
        return False, ""
    return True, "; ".join(reasons)


def _extract_trace_section(body: str) -> str:
    m = TRACE_HEADER.search(body)
    if not m:
        return ""
    start = m.end()
    # Until next **Bold field:** at start of line (Scope, Files, Verification, etc.)
    end_m = re.search(
        r"^\*\*(?:Scope|Files|Verification|Checkpoint|Expected|Invariants)",
        body[start:],
        re.MULTILINE | re.IGNORECASE,
    )
    end = start + end_m.start() if end_m else len(body)
    return body[start:end]


def _strip_line_anchor(path: str) -> str:
    """`src/foo.py:86` → `src/foo.py` for parity with **Files:** lists."""
    if ":" in path and path.startswith(("src/", "tests/", "frontend/")):
        base, _, suffix = path.rpartition(":")
        if suffix.isdigit() and "." in base:
            return base
    return path


def _paths_from_trace(trace: str) -> set[str]:
    """Extract paths cited in a runtime-trace block.

    Symmetric with ``cp.files`` (no extension filter) so the invariant
    Files(CP) ⊆ paths(trace) ⊆ paths(§2) holds when **Files:** legitimately
    lists non-source artifacts (config YAML, plist examples, HTML fixtures).
    See ``test_trace_covers_non_py_files_in_files_list`` regression test.
    """
    paths: set[str] = set()
    for m in PATH_IN_BACKTICKS.finditer(trace):
        p = _strip_line_anchor(m.group(1).strip())
        if "{" not in p and "*" not in p:
            paths.add(p)
    return paths


def _trace_src_prefixes(paths: set[str]) -> set[str]:
    return {_file_zone(p) for p in paths if p.startswith("src/")}


def _count_trace_rows(trace: str) -> int:
    count = 0
    for line in trace.splitlines():
        s = line.strip()
        if not s or s.startswith("---"):
            continue
        if s.startswith("|") and ("---" in s or "Caller" in s or "Step" in s):
            continue
        if s.startswith("|") and s.count("|") >= 2:
            count += 1
            continue
        if re.match(r"^\d+[\.\)]\s", s):
            count += 1
            continue
        if "`src/" in s:
            count += 1
    return count


def lint_spec(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    section2 = _section2_paths(text)
    section6 = _slice_between(text, SECTION6_START, re.compile(r"^## 7\.\s", re.MULTILINE))
    if not section6:
        section6 = _slice_between(text, SECTION6_START, NEXT_H2)
    if not section6:
        return ["missing ## 6. Build Steps section"]

    errors: list[str] = []
    for cp in _parse_checkpoints(section6):
        required, why = _trace_required(cp.files, cp.body)
        if not required:
            continue

        trace = _extract_trace_section(cp.body)
        if not trace.strip():
            errors.append(f"{cp.label}: runtime trace required ({why}) but **Runtime trace:** missing")
            continue

        row_count = _count_trace_rows(trace)
        if row_count < 3:
            errors.append(
                f"{cp.label}: runtime trace has {row_count} row(s); need ≥3 ({why})"
            )

        trace_paths = _paths_from_trace(trace)
        prefixes = _trace_src_prefixes(trace_paths)
        if len(prefixes) < 2 and required:
            # Only enforce 2-prefix rule when structural multi-src-zone trigger fired
            src_zones = _src_zones(cp.files)
            if len(src_zones) >= 2 and len(prefixes) < 2:
                errors.append(
                    f"{cp.label}: trace lists {len(prefixes)} distinct src/ prefix(es); "
                    f"need ≥2 when Files span multiple zones ({why})"
                )

        missing_in_trace = cp.files - trace_paths
        if missing_in_trace:
            errors.append(
                f"{cp.label}: **Files:** not covered in runtime trace: "
                + ", ".join(sorted(missing_in_trace))
                + " — File:anchor must be standalone backticks (e.g. "
                "`config/foo.yaml`), not inside function-call backticks; "
                "see skills/spec-author/references/runtime-trace-format.md"
            )

        extra = trace_paths - section2
        if extra:
            errors.append(
                f"{cp.label}: trace paths not in §2 Created/Edited: "
                + ", ".join(sorted(extra))
            )

    return errors


def _chunk_id_from_path(path: Path) -> str | None:
    m = re.search(r"chunk-([^/]+)-spec-v", path.name)
    return m.group(1) if m else None


def _skip_for_chunk(chunk_id: str | None) -> bool:
    if os.environ.get("GRACE_SKIP_RUNTIME_TRACE") == "1":
        return True
    skip = os.environ.get("GRACE_RUNTIME_TRACE_SKIP", "")
    if not skip or not chunk_id:
        return False
    allowed = {s.strip() for s in skip.split(",") if s.strip()}
    return chunk_id in allowed


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) != 1:
        print("Usage: python3 scripts/lint/check_runtime_trace.py <spec-path>", file=sys.stderr)
        return 2

    spec_path = Path(argv[0]).resolve()
    if not spec_path.is_file():
        print(f"ERROR: spec not found: {spec_path}", file=sys.stderr)
        return 2

    chunk_id = _chunk_id_from_path(spec_path)
    if _skip_for_chunk(chunk_id):
        print(f"check-runtime-trace: skipped for chunk={chunk_id}")
        return 0

    errors = lint_spec(spec_path)
    if not errors:
        print(f"OK: runtime trace lint passed for {spec_path.name}")
        return 0

    print(f"FAIL: runtime trace lint ({len(errors)} issue(s)) for {spec_path.name}", file=sys.stderr)
    for err in errors:
        print(f"  - {err}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
