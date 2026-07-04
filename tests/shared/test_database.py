"""Tests for GrACE shared database layer."""

from sqlalchemy import text

from src.shared.database import get_db, get_engine


def test_database_connection():
    """Engine connects to PostgreSQL successfully."""
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1"))
        assert result.scalar() == 1


def test_session_creates():
    """get_db() yields a working session."""
    gen = get_db()
    db = next(gen)
    try:
        result = db.execute(text("SELECT 1"))
        assert result.scalar() == 1
    finally:
        try:
            next(gen)
        except StopIteration:
            pass
