"""D495/D497: uv sync integration tests — 4 tests."""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.slow
def test_uv_sync_extra_dev_succeeds():
    """uv sync --extra dev must exit 0."""
    result = subprocess.run(
        ["uv", "sync", "--extra", "dev"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, f"uv sync failed: {result.stderr}"


@pytest.mark.slow
def test_uv_sync_no_dev_excludes_pytest():
    """uv sync --no-dev must exclude pytest from the environment."""
    # First sync without dev
    sync_result = subprocess.run(
        ["uv", "sync", "--no-dev"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert sync_result.returncode == 0, f"uv sync --no-dev failed: {sync_result.stderr}"

    # Check that pytest is NOT importable in the uv-managed env
    check_result = subprocess.run(
        ["uv", "run", "python3", "-c", "import pytest"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert check_result.returncode != 0, (
        "pytest should NOT be importable after uv sync --no-dev"
    )

    # Restore dev deps for subsequent tests
    subprocess.run(
        ["uv", "sync", "--extra", "dev"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        timeout=300,
    )


def test_uv_lock_check_succeeds():
    """uv lock --check must exit 0 (verifies lockfile is up-to-date with manifest)."""
    result = subprocess.run(
        ["uv", "lock", "--check"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"uv lock --check failed: {result.stderr}"


def test_pytest_discovery_post_sync():
    """pytest --co -q tests/setup/ must succeed post-sync."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--co", "-q", "tests/setup/"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"pytest discovery failed: {result.stderr}"
