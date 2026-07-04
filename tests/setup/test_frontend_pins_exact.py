"""D493 — Tests for frontend dependency pin audit.

2 tests:
  1. All packages in frontend/package.json pinned exact (no ^ or ~ prefixes)
  2. Version strings match semantic-version pattern
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_JSON = ROOT / "frontend" / "package.json"


class TestFrontendPinsExact:
    """Test 1 + 2: exact pins and semver pattern."""

    def test_all_packages_pinned_exact(self):
        """All dependencies and devDependencies use exact pins (no ^ or ~ prefix)."""
        data = json.loads(PACKAGE_JSON.read_text())
        violations = []
        for section in ("dependencies", "devDependencies"):
            deps = data.get(section, {})
            for pkg, version in deps.items():
                if version.startswith("^") or version.startswith("~"):
                    violations.append(f"{section}.{pkg}: {version}")
        assert not violations, (
            f"Found {len(violations)} packages with non-exact pins (^ or ~):\n"
            + "\n".join(violations)
        )

    def test_version_strings_match_semver_pattern(self):
        """All version strings match the semantic-version pattern (X.Y.Z)."""
        data = json.loads(PACKAGE_JSON.read_text())
        semver = re.compile(r"^\d+\.\d+\.\d+")
        violations = []
        for section in ("dependencies", "devDependencies"):
            deps = data.get(section, {})
            for pkg, version in deps.items():
                if not semver.match(version):
                    violations.append(f"{section}.{pkg}: {version}")
        assert not violations, (
            f"Found {len(violations)} packages with non-semver versions:\n"
            + "\n".join(violations)
        )
