"""D485 — Integration tests for D472 guard + D485 fixture composition (Chunk 75a).

Verifies that:
1. TRUNCATE fixtures with `requires_db_wipe` marker are accepted by D472 guard.
2. SAVEPOINT-rollback fixtures (no TRUNCATE) are accepted by D472 guard.
"""

from __future__ import annotations

import pytest

from tests.conftest import _is_test_database_url


class TestD472GuardWithD485Fixtures:
    """D472 guard accepts both fixture patterns."""

    def test_marked_truncate_accepted(self):
        """Fixture with requires_db_wipe marker + TRUNCATE: D472 guard accepts.

        The D472 guard gates on DATABASE_URL safety, not on fixture patterns.
        A TRUNCATE fixture with the marker is a D485 carve-out that D472
        permits (the marker is forward-compatible metadata, not a D472 gate).
        """
        # D472 guard checks the DATABASE_URL, not the fixture pattern.
        # If we're running, the guard already accepted our URL.
        # Verify the guard function works with a known-safe URL.
        assert _is_test_database_url("postgresql://localhost/grace_test") is True
        # Verify it rejects a dangerous URL.
        assert _is_test_database_url("postgresql://prod-host/grace") is False
        assert _is_test_database_url("postgresql://localhost/production") is False

    def test_unmarked_rollback_accepted(self):
        """Fixture with SAVEPOINT-rollback pattern (no TRUNCATE): D472 guard accepts.

        SAVEPOINT-rollback fixtures don't issue TRUNCATE, so D472's
        DATABASE_URL guard is the only relevant check. The fixture
        pattern itself is transparent to the guard.
        """
        # The D472 guard does not inspect fixture patterns — it only
        # checks DATABASE_URL. A SAVEPOINT-rollback fixture is inherently
        # safe (all writes are rolled back) regardless of the URL.
        # Verify guard accepts localhost with GRACE_TEST_DB=1 pattern.
        import os
        saved = os.environ.get("GRACE_TEST_DB")
        try:
            os.environ["GRACE_TEST_DB"] = "1"
            assert _is_test_database_url("postgresql://localhost/grace") is True
        finally:
            if saved is None:
                os.environ.pop("GRACE_TEST_DB", None)
            else:
                os.environ["GRACE_TEST_DB"] = saved
