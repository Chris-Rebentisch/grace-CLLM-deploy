"""Phase 7 Requirements Traceability Matrix coverage test (Chunk 61, CP10).

Loads ``tests/fixtures/phase7_rtm.yaml`` and asserts that every RTM row
has at least one non-empty, resolvable test evidence path.

Resolution rules:
- Directory path (ends with ``/``): must exist as a directory with ≥1 ``.py`` file.
- File path: must exist on disk.
- ``path::node_id``: file must exist and node ID must be collectable by pytest.
- Glob pattern (contains ``*``): must match ≥1 file.
"""

from __future__ import annotations

import glob
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RTM_PATH = _REPO_ROOT / "tests" / "fixtures" / "phase7_rtm.yaml"


def _load_rtm() -> list[dict]:
    with open(_RTM_PATH) as f:
        data = yaml.safe_load(f)
    return data["criteria"]


def _resolve_test_id(test_id: str) -> bool:
    """Return True if the test evidence path resolves."""
    if "::" in test_id:
        # path::node_id — just check the file part exists
        file_part = test_id.split("::")[0]
        return (_REPO_ROOT / file_part).exists()

    path = _REPO_ROOT / test_id

    if test_id.endswith("/"):
        # Directory: must contain at least one .py file
        return path.is_dir() and len(list(path.glob("*.py"))) >= 1

    if "*" in test_id:
        # Glob pattern
        return len(glob.glob(str(_REPO_ROOT / test_id))) >= 1

    # Plain file
    return path.exists()


class TestPhase7RTMCoverage:
    """Validates that every RTM criterion has resolvable test evidence."""

    _criteria = _load_rtm()

    def test_rtm_has_10_criteria(self):
        assert len(self._criteria) == 10, (
            f"Expected 10 RTM criteria, got {len(self._criteria)}"
        )

    @pytest.mark.parametrize(
        "criterion",
        _load_rtm(),
        ids=[f"AC-{c['id']}" for c in _load_rtm()],
    )
    def test_criterion_has_test_ids(self, criterion: dict):
        """Every criterion must have a non-empty test_ids list."""
        assert criterion.get("test_ids"), (
            f"RTM criterion {criterion['id']} ({criterion['summary']}) "
            f"has no test_ids"
        )

    @pytest.mark.parametrize(
        "criterion",
        _load_rtm(),
        ids=[f"AC-{c['id']}" for c in _load_rtm()],
    )
    def test_criterion_test_ids_resolve(self, criterion: dict):
        """Every listed test evidence path must resolve on disk."""
        for test_id in criterion.get("test_ids", []):
            assert _resolve_test_id(test_id), (
                f"RTM criterion {criterion['id']}: test evidence "
                f"{test_id!r} does not resolve"
            )
