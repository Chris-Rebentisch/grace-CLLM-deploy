"""Chunk 39 D300 — c39a additive columns present on realization snapshots."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from src.shared.config import get_settings

_EXPECTED = frozenset(
    {
        "velocity",
        "evidence_count_consistent",
        "evidence_count_counter",
        "first_evidence_seen_at",
        "last_counter_evidence_seen_at",
        "criteria_all_satisfied",
    }
)


def test_c39a_columns_exist():
    settings = get_settings()
    eng = create_engine(settings.database_url, pool_pre_ping=True)
    try:
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'change_directive_realization_snapshots'
                    """
                )
            ).fetchall()
    finally:
        eng.dispose()
    found = {r[0] for r in rows}
    missing = _EXPECTED - found
    assert not missing, (
        "c39a columns missing — run `alembic upgrade head`. "
        f"Missing: {sorted(missing)}"
    )
