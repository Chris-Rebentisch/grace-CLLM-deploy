"""CP1 — c80b_proc_docs_origin migration tests (D518).

Validates upgrade/downgrade round-trip and column presence.
"""

import subprocess
import sys

import pytest

from src.shared.database import get_session_factory


@pytest.fixture(autouse=True)
def _ensure_head():
    """Ensure we start at alembic head."""
    subprocess.check_call(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[3]),
    )


def test_upgrade_downgrade():
    """Migration c80b_proc_docs_origin up/down round-trips successfully."""
    root = str(__import__("pathlib").Path(__file__).resolve().parents[3])

    # Downgrade one step
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "-1"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"downgrade failed: {result.stderr}"

    # Re-upgrade
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"upgrade failed: {result.stderr}"


def test_columns_exist_after_upgrade():
    """After upgrade, origin and source_type columns exist on processed_documents."""
    from sqlalchemy import inspect, text

    session_factory = get_session_factory()
    db = session_factory()
    try:
        # Use inspector to check column existence
        inspector = inspect(db.bind)
        columns = {c["name"] for c in inspector.get_columns("processed_documents")}
        assert "origin" in columns, "origin column missing after upgrade"
        assert "source_type" in columns, "source_type column missing after upgrade"
    finally:
        db.close()
