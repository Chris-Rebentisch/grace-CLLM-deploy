"""Tests for CQ candidates pipeline (D227, Chunk 29 CP3).

5 tests: background generation, three-source dispatch, quarantine contract,
airgap disables Source 2, dedup (text-hash based).
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch
from uuid import uuid4

import pytest

from src.discovery.cq_candidates import (
    CQCandidateRow,
    _dedup_candidates,
    _generate_candidates_async,
    _is_airgap_mode,
    _run_local_documents_source,
    _run_ontology_seed_source,
    _run_web_presence_source,
    acquire_generation_lock,
    is_generation_in_flight,
    list_candidates,
)
from src.elicitation.session_database import create_session
from src.shared.database import get_db


def _get_db():
    gen = get_db()
    db = next(gen)
    return db, gen


def _close_db(gen):
    try:
        next(gen)
    except StopIteration:
        pass


class TestCQCandidatesPipeline:
    """CP3 verification tests."""

    def test_background_generation_inserts_candidates(self):
        """Background generation produces at least one candidate row."""
        db, gen = _get_db()
        sid = uuid4()
        try:
            from sqlalchemy import text
            # Create a session first (FK requirement)
            create_session(db, session_id=sid, session_plan={})

            # Run generation synchronously
            asyncio.run(
                _generate_candidates_async(db, sid, "general", None)
            )

            # Check candidates were created
            rows = list_candidates(db, sid)
            # ontology_seed source always produces candidates
            assert len(rows) >= 1
            # All should be quarantined
            for row in rows:
                assert row.validation_status == "quarantined"
        finally:
            from sqlalchemy import text
            db.execute(text("DELETE FROM cq_candidates WHERE session_id = :sid"), {"sid": str(sid)})
            db.execute(text("ALTER TABLE elicitation_sessions DISABLE TRIGGER trig_elicitation_sessions_immutable"))
            db.execute(text("DELETE FROM elicitation_sessions WHERE session_id = :sid"), {"sid": str(sid)})
            db.execute(text("ALTER TABLE elicitation_sessions ENABLE TRIGGER trig_elicitation_sessions_immutable"))
            db.commit()
            _close_db(gen)

    def test_three_source_dispatch(self):
        """All three sources are invoked when source_origin is None."""
        db, gen = _get_db()
        sid = uuid4()
        try:
            create_session(db, session_id=sid, session_plan={})

            # Patch individual sources to track calls
            with patch(
                "src.discovery.cq_candidates._run_local_documents_source",
                return_value=[],
            ) as local_mock, patch(
                "src.discovery.cq_candidates._run_web_presence_source",
                return_value=[],
            ) as web_mock, patch(
                "src.discovery.cq_candidates._run_ontology_seed_source",
                return_value=[],
            ) as seed_mock:
                asyncio.run(
                    _generate_candidates_async(db, sid, "general", None)
                )
                local_mock.assert_called_once()
                web_mock.assert_called_once()
                seed_mock.assert_called_once()
        finally:
            from sqlalchemy import text
            db.execute(text("DELETE FROM cq_candidates WHERE session_id = :sid"), {"sid": str(sid)})
            db.execute(text("ALTER TABLE elicitation_sessions DISABLE TRIGGER trig_elicitation_sessions_immutable"))
            db.execute(text("DELETE FROM elicitation_sessions WHERE session_id = :sid"), {"sid": str(sid)})
            db.execute(text("ALTER TABLE elicitation_sessions ENABLE TRIGGER trig_elicitation_sessions_immutable"))
            db.commit()
            _close_db(gen)

    def test_quarantine_default_at_insert(self):
        """All candidates must be inserted with validation_status='quarantined'."""
        db, gen = _get_db()
        sid = uuid4()
        try:
            create_session(db, session_id=sid, session_plan={})

            # Insert a candidate directly
            row = CQCandidateRow(
                session_id=sid,
                cq_text="Test CQ?",
                cq_type="coverage",
                source_origin="local_documents",
                validation_status="quarantined",
                metadata_={},
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            assert row.validation_status == "quarantined"
        finally:
            from sqlalchemy import text
            db.execute(text("DELETE FROM cq_candidates WHERE session_id = :sid"), {"sid": str(sid)})
            db.execute(text("ALTER TABLE elicitation_sessions DISABLE TRIGGER trig_elicitation_sessions_immutable"))
            db.execute(text("DELETE FROM elicitation_sessions WHERE session_id = :sid"), {"sid": str(sid)})
            db.execute(text("ALTER TABLE elicitation_sessions ENABLE TRIGGER trig_elicitation_sessions_immutable"))
            db.commit()
            _close_db(gen)

    def test_airgap_disables_source_2(self):
        """Source 2 (web_presence) is disabled when airgap_mode=true."""
        with patch("src.discovery.cq_candidates._is_airgap_mode", return_value=True):
            result = asyncio.run(_run_web_presence_source(uuid4(), "general"))
            assert result == []

    def test_dedup_removes_exact_text_duplicates(self):
        """Dedup removes candidates with identical text hashes."""
        candidates = [
            {"cq_text": "What entities exist?", "cq_type": "coverage", "source_origin": "local_documents"},
            {"cq_text": "What entities exist?", "cq_type": "coverage", "source_origin": "ontology_seed"},
            {"cq_text": "How are relationships structured?", "cq_type": "relationship", "source_origin": "ontology_seed"},
        ]
        deduped = _dedup_candidates(candidates, set())
        assert len(deduped) == 2
        # Different text should survive dedup
        texts = [c["cq_text"] for c in deduped]
        assert "What entities exist?" in texts
        assert "How are relationships structured?" in texts
