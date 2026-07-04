"""Shared fixtures for permissions tests (Chunk 42)."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", "postgresql+psycopg2:///grace")


@pytest.fixture
def db_session() -> Session:
    """Yield a Postgres session and roll back any changes at end."""
    engine = create_engine(_database_url(), future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 5, 8, 16, 0, 0, tzinfo=timezone.utc)
