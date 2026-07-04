"""CP3 migration round-trip test: c77a_image_jobs (D502).

Verifies that the migration applies cleanly, accepts job_kind='image',
and round-trips through downgrade + re-upgrade.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

# F-57 (validation run, 2026-07-02): this test hardcoded
# ``cwd=os.path.expanduser("~/grace")`` — the PARENT repo — so it ran alembic
# against a different migration set than the one under test. In the CLLM deploy
# repo (whose migration set includes deploy-only revisions like
# f57_prune_children_first) that parent-repo alembic could not locate the
# deploy head and the round-trip failed. Resolve the repo root from the test
# file location instead (matches the ``parents[2]`` portability discipline used
# across scripts/pipeline).
_REPO_ROOT = str(pathlib.Path(__file__).resolve().parents[2])


def test_c77a_round_trip():
    """Downgrade one step, then re-upgrade — round-trips the current head."""
    env = dict(os.environ)
    # Use test DB if available via test-DB isolation (2026-05-28)
    db_url = os.environ.get("GRACE_PYTEST_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
    if not db_url.endswith("_test"):
        # Derive the _test sibling
        if "grace" in db_url and not db_url.endswith("_test"):
            db_url = db_url.rsplit("/", 1)[0] + "/grace_test"
    if db_url:
        env["DATABASE_URL"] = db_url

    # Downgrade one step
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "-1"],
        capture_output=True, text=True, cwd=_REPO_ROOT,
        env=env,
    )
    assert result.returncode == 0, f"Downgrade failed: {result.stderr}"

    # Re-upgrade
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        capture_output=True, text=True, cwd=_REPO_ROOT,
        env=env,
    )
    assert result.returncode == 0, f"Upgrade failed: {result.stderr}"
