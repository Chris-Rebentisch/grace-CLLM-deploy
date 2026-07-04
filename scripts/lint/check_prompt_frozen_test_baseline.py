#!/usr/bin/env python3
"""Fail prompts that pin volatile live pytest --collect-only counts.

Entering Python test baselines must match the ratified outline/spec FINAL
(handoff-anchored frozen count). Live collection drifts with unrelated repo
activity; exact-match preflight steps cause dual-pass mechanical reject loops.

Usage:
  python3 scripts/lint/check_prompt_frozen_test_baseline.py <chunk> <prompt-path>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"

FORBIDDEN_PHRASES = (
    "use the live value",
    "use live collect",
    "use the live collect",
)

ENTERING_RE = re.compile(
    r"Cumulative\s+(?:Python\s+)?tests\s+entering:\s*\*?\*?\s*(\d+)",
    re.IGNORECASE,
)
OUTLINE_ENTERING_RE = re.compile(
    r"Cumulative\s+Python\s+tests\s+entering:\s*\*?\*?\s*(\d+)",
    re.IGNORECASE,
)
EXACT_COLLECT_RE = re.compile(
    r"(?:Should\s+report|reports?|yield[s]?)\s+~?(\d{4})\s+tests?\s+collected",
    re.IGNORECASE,
)
DRIFT_OK_RE = re.compile(
    r"(live\s+.*(higher|expected)|handoff\s+stated\s+\d+|≥\s*\d+|>=\s*\d+|not\s+a\s+failure)",
    re.IGNORECASE,
)


def _latest_final(chunk: str, stage: str) -> Path | None:
    finals = sorted(DOCS.glob(f"chunk-{chunk}-{stage}-v*-FINAL.md"))
    if not finals:
        return None
    valid = [
        p
        for p in finals
        if re.match(rf"^chunk-{re.escape(chunk)}-{stage}-v\d+(?:-FINAL)?$", p.stem)
        or p.stem.endswith("-FINAL")
    ]
    if not valid:
        return None

    def rank(p: Path) -> tuple[int, int]:
        m = re.search(r"-v(\d+)", p.stem)
        ver = int(m.group(1)) if m else -1
        return (ver, 1 if "-FINAL" in p.stem else 0)

    return max(valid, key=rank)


def _extract_entering(text: str) -> int | None:
    m = ENTERING_RE.search(text)
    return int(m.group(1)) if m else None


def _extract_outline_entering(text: str) -> int | None:
    m = OUTLINE_ENTERING_RE.search(text)
    return int(m.group(1)) if m else None


def _strip_changelog_sections(text: str) -> str:
    """Changelog may quote forbidden phrases when describing prior defects."""
    parts = re.split(r"(?m)^##\s+Changes from\b", text, maxsplit=1)
    return parts[0]


def main() -> int:
    if len(sys.argv) != 3:
        print(
            "Usage: python3 scripts/lint/check_prompt_frozen_test_baseline.py "
            "<chunk> <prompt-path>",
            file=sys.stderr,
        )
        return 2

    chunk = sys.argv[1]
    prompt_path = Path(sys.argv[2])
    if not prompt_path.is_file():
        print(f"check-prompt-frozen-test-baseline: missing {prompt_path}", file=sys.stderr)
        return 2

    text = prompt_path.read_text(encoding="utf-8")
    errors: list[str] = []

    lower = _strip_changelog_sections(text).lower()
    for phrase in FORBIDDEN_PHRASES:
        if phrase in lower:
            errors.append(f"forbidden phrase: {phrase!r} (use frozen handoff baseline only)")

    body = _strip_changelog_sections(text)
    prompt_entering = _extract_entering(body)
    outline_final = _latest_final(chunk, "outline")
    spec_final = _latest_final(chunk, "spec")
    upstream_entering: int | None = None
    upstream_label = ""
    for label, path in (("outline", outline_final), ("spec", spec_final)):
        if path is None:
            continue
        upstream_text = path.read_text(encoding="utf-8")
        val = _extract_outline_entering(upstream_text) or _extract_entering(upstream_text)
        if val is not None:
            upstream_entering = val
            upstream_label = f"{label} {path.name}"
            break

    if upstream_entering is not None and prompt_entering is not None:
        if prompt_entering != upstream_entering:
            errors.append(
                f"prompt entering baseline {prompt_entering} != "
                f"{upstream_label} frozen {upstream_entering}"
            )

    if upstream_entering is not None:
        for m in EXACT_COLLECT_RE.finditer(body):
            claimed = int(m.group(1))
            if claimed == upstream_entering:
                continue
            # Allow lines that document drift as expected.
            start = max(0, m.start() - 200)
            end = min(len(text), m.end() + 200)
            window = body[start:end]
            if DRIFT_OK_RE.search(window):
                continue
            errors.append(
                f"exact collect-only expectation ~{claimed} at offset {m.start()} "
                f"≠ frozen entering {upstream_entering} from {upstream_label} "
                "(use ≥ frozen + 'live runs higher, expected' or drop exact count)"
            )

    if errors:
        print("check-prompt-frozen-test-baseline: FAIL")
        for err in errors:
            print(f"  - {err}")
        return 1

    frozen = upstream_entering if upstream_entering is not None else prompt_entering
    print(
        f"check-prompt-frozen-test-baseline: OK "
        f"(frozen entering={frozen or 'n/a'})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
