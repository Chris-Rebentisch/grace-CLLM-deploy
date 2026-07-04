"""Repo-guard: no raw `1.0 - distance` ANN conversions outside the
authoritative helpers (F-08 / F2-03 class).

The pattern has now caused catastrophic false merges TWICE (run-1 F-08 in the
native Tier-2 resolver; run-2 F2-03 in the harness's own ANN lookup) and was
found a THIRD time lying dormant in dedup_detection (fixed in the F2-03
sweep). ArcadeDB's vectorNeighbors() returns distance 0.0 for stale /
un-reindexed vectors, so `1.0 - distance` mints "similarity 1.00" out of thin
air. Every ANN similarity must route through an authoritative helper
(client-side cosine from the neighbor's own _embedding; distance-0.0 accepted
only on exact-name match).
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

# The ONLY files allowed to contain the raw conversion: the authoritative
# helpers themselves (their bounded, documented fallback branch).
_ALLOWED = {
    "src/extraction/entity_resolver.py",
    "grace-claude-skills/scripts/import_extraction.py",
}

_PATTERN = re.compile(r"1\.0?\s*-\s*(?:\w*\.)?distance\b")


def _scan(root: Path) -> list[str]:
    offenders: list[str] = []
    for py in root.rglob("*.py"):
        rel = str(py.relative_to(_REPO_ROOT))
        if rel in _ALLOWED or rel.startswith("tests/"):
            continue
        for lineno, line in enumerate(
            py.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1
        ):
            code = line.split("#", 1)[0]
            if _PATTERN.search(code):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    return offenders


def test_no_raw_ann_distance_conversion():
    offenders = _scan(_REPO_ROOT / "src")
    offenders += _scan(_REPO_ROOT / "grace-claude-skills")
    assert not offenders, (
        "Raw `1.0 - distance` ANN conversion found (F-08/F2-03 class — "
        "distance 0.0 from stale vectors becomes similarity 1.00 and merges "
        "unrelated entities). Route through _authoritative_similarity:\n"
        + "\n".join(offenders)
    )
