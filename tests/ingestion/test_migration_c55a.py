"""Tests for migration c55a_ingest_sources_runs (CP6)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.shared.config import get_settings


@pytest.fixture(scope="module")
def db_session():
    """Create a test DB session."""
    settings = get_settings()
    engine = create_engine(str(settings.database_url))
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()


class TestMigrationC55a:
    def test_migration_applies_cleanly(self, db_session):
        """Tables exist after migration."""
        result = db_session.execute(
            text("SELECT tablename FROM pg_tables WHERE tablename IN ('ingestion_sources', 'ingestion_runs')")
        )
        tables = {row[0] for row in result}
        assert "ingestion_sources" in tables
        assert "ingestion_runs" in tables

    def test_trigger_rejects_delete(self, db_session):
        """Trigger rejects DELETE from ingestion_runs."""
        source_id = uuid4()
        run_id = uuid4()
        try:
            db_session.execute(
                text(
                    "INSERT INTO ingestion_sources (id, name, source_type, config_json, segment) "
                    "VALUES (:id, :name, 'mbox', '{}'::jsonb, 'test')"
                ),
                {"id": str(source_id), "name": f"test-del-{source_id}"},
            )
            db_session.execute(
                text(
                    "INSERT INTO ingestion_runs (id, source_id) VALUES (:id, :source_id)"
                ),
                {"id": str(run_id), "source_id": str(source_id)},
            )
            db_session.commit()

            with pytest.raises(Exception, match="append-only"):
                db_session.execute(
                    text("DELETE FROM ingestion_runs WHERE id = :id"),
                    {"id": str(run_id)},
                )
                db_session.commit()
        finally:
            db_session.rollback()
            db_session.execute(
                text("SET LOCAL alembic.downgrading = 'true'")
            )
            db_session.execute(
                text("DELETE FROM ingestion_runs WHERE id = :id"),
                {"id": str(run_id)},
            )
            db_session.execute(
                text("DELETE FROM ingestion_sources WHERE id = :id"),
                {"id": str(source_id)},
            )
            db_session.commit()

    def test_trigger_rejects_immutable_update(self, db_session):
        """Trigger rejects UPDATE on immutable columns."""
        source_id = uuid4()
        run_id = uuid4()
        try:
            db_session.execute(
                text(
                    "INSERT INTO ingestion_sources (id, name, source_type, config_json, segment) "
                    "VALUES (:id, :name, 'mbox', '{}'::jsonb, 'test')"
                ),
                {"id": str(source_id), "name": f"test-imm-{source_id}"},
            )
            db_session.execute(
                text(
                    "INSERT INTO ingestion_runs (id, source_id) VALUES (:id, :source_id)"
                ),
                {"id": str(run_id), "source_id": str(source_id)},
            )
            db_session.commit()

            with pytest.raises(Exception, match="mutable"):
                db_session.execute(
                    text("UPDATE ingestion_runs SET source_id = :new_id WHERE id = :id"),
                    {"new_id": str(uuid4()), "id": str(run_id)},
                )
                db_session.commit()
        finally:
            db_session.rollback()
            db_session.execute(
                text("SET LOCAL alembic.downgrading = 'true'")
            )
            db_session.execute(
                text("DELETE FROM ingestion_runs WHERE id = :id"),
                {"id": str(run_id)},
            )
            db_session.execute(
                text("DELETE FROM ingestion_sources WHERE id = :id"),
                {"id": str(source_id)},
            )
            db_session.commit()

    def test_trigger_admits_lifecycle_update(self, db_session):
        """Trigger admits UPDATE on lifecycle columns."""
        source_id = uuid4()
        run_id = uuid4()
        try:
            db_session.execute(
                text(
                    "INSERT INTO ingestion_sources (id, name, source_type, config_json, segment) "
                    "VALUES (:id, :name, 'mbox', '{}'::jsonb, 'test')"
                ),
                {"id": str(source_id), "name": f"test-lc-{source_id}"},
            )
            db_session.execute(
                text(
                    "INSERT INTO ingestion_runs (id, source_id) VALUES (:id, :source_id)"
                ),
                {"id": str(run_id), "source_id": str(source_id)},
            )
            db_session.commit()

            # This should succeed
            db_session.execute(
                text("UPDATE ingestion_runs SET status = 'running' WHERE id = :id"),
                {"id": str(run_id)},
            )
            db_session.commit()

            result = db_session.execute(
                text("SELECT status FROM ingestion_runs WHERE id = :id"),
                {"id": str(run_id)},
            )
            assert result.scalar() == "running"
        finally:
            db_session.execute(
                text("SET LOCAL alembic.downgrading = 'true'")
            )
            db_session.execute(
                text("DELETE FROM ingestion_runs WHERE id = :id"),
                {"id": str(run_id)},
            )
            db_session.execute(
                text("DELETE FROM ingestion_sources WHERE id = :id"),
                {"id": str(source_id)},
            )
            db_session.commit()
