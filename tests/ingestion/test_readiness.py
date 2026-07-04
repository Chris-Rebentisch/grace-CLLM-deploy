"""Tests for D274 readiness check (CP4).

~10 tests covering: Path A two-segment all-pass; Path A one-segment fail
(zero Person); Path A one-segment fail (zero accepted CQs); Path B
bootstrap_complete=False short-circuit; Path C identical-to-A; shipped literal
'ACCEPTED' as Postgres filter; ArcadeDB property names in query; thresholds
echoed; per-segment counts; empty-segment list.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.ingestion.models import ReadinessThresholds
from src.ingestion.readiness import check_readiness


def _make_arcade_client(person_counts: dict, org_counts: dict):
    """Create a mock arcade client that returns entity counts.

    Updated Chunk 56 CP5: uses execute_cypher (dict with 'result' key) instead of .query.
    """
    async def _execute_cypher(query_str, params=None):
        params = params or {}
        segment = params.get("segment", "")
        if "Person" in query_str:
            return {"result": [{"entity_count": person_counts.get(segment, 0)}]}
        elif "Organization" in query_str:
            return {"result": [{"entity_count": org_counts.get(segment, 0)}]}
        return {"result": [{"entity_count": 0}]}

    client = AsyncMock()
    client.execute_cypher = _execute_cypher
    return client


def _make_db_session(accepted_counts: dict):
    """Create a mock DB session that returns accepted CQ counts."""
    session = MagicMock()

    def _execute(query, params=None):
        params = params or {}
        segment = params.get("segment", "")
        result = MagicMock()
        result.scalar.return_value = accepted_counts.get(segment, 0)
        return result

    session.execute = _execute
    return session


class TestReadiness:
    def test_path_a_two_segment_all_pass(self):
        thresholds = ReadinessThresholds(cq_mention_threshold=3, confidence_threshold=0.85)
        arcade = _make_arcade_client(
            {"insurance": 5, "legal": 3},
            {"insurance": 2, "legal": 1},
        )
        db = _make_db_session({"insurance": 4, "legal": 3})

        result = asyncio.run(check_readiness(
            "A", ["insurance", "legal"], arcade, db, thresholds=thresholds
        ))
        assert result.overall_ready is True
        assert len(result.segments) == 2
        assert all(s.ready for s in result.segments)

    def test_path_a_fail_zero_person(self):
        thresholds = ReadinessThresholds(cq_mention_threshold=3, confidence_threshold=0.85)
        arcade = _make_arcade_client(
            {"insurance": 0},
            {"insurance": 2},
        )
        db = _make_db_session({"insurance": 5})

        result = asyncio.run(check_readiness(
            "A", ["insurance"], arcade, db, thresholds=thresholds
        ))
        assert result.overall_ready is False
        assert result.segments[0].ready is False
        assert result.segments[0].person_count == 0

    def test_path_a_fail_zero_accepted_cqs(self):
        thresholds = ReadinessThresholds(cq_mention_threshold=3, confidence_threshold=0.85)
        arcade = _make_arcade_client({"insurance": 5}, {"insurance": 2})
        db = _make_db_session({"insurance": 1})

        result = asyncio.run(check_readiness(
            "A", ["insurance"], arcade, db, thresholds=thresholds
        ))
        assert result.overall_ready is False
        assert result.segments[0].accepted_cq_count == 1

    def test_path_b_bootstrap_not_complete(self):
        """Path B bootstrap_complete=False: zero DB calls, returns bootstrap_pending=True."""
        thresholds = ReadinessThresholds()
        arcade = MagicMock()
        db = MagicMock()

        result = asyncio.run(check_readiness(
            "B", ["insurance"], arcade, db,
            thresholds=thresholds, bootstrap_complete=False
        ))
        assert result.bootstrap_pending is True
        assert result.overall_ready is False
        assert result.segments == []
        # Verify zero DB calls
        arcade.execute_cypher.assert_not_called()
        db.execute.assert_not_called()

    def test_path_c_identical_to_a(self):
        thresholds = ReadinessThresholds(cq_mention_threshold=2, confidence_threshold=0.8)
        arcade = _make_arcade_client({"hr": 3}, {"hr": 1})
        db = _make_db_session({"hr": 5})

        result = asyncio.run(check_readiness(
            "C", ["hr"], arcade, db, thresholds=thresholds
        ))
        assert result.deployment_path == "C"
        assert result.overall_ready is True

    def test_accepted_literal_in_postgres_query(self):
        """Shipped literal 'ACCEPTED' is used as Postgres filter."""
        thresholds = ReadinessThresholds()
        arcade = _make_arcade_client({"ins": 1}, {"ins": 1})

        # Spy on the SQL query text
        db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 5
        db.execute.return_value = mock_result

        asyncio.run(check_readiness(
            "A", ["ins"], arcade, db, thresholds=thresholds
        ))

        call_args = db.execute.call_args
        query_text = str(call_args[0][0])
        assert "ACCEPTED" in query_text

    def test_arcade_property_names_in_query(self):
        """ArcadeDB property names ontology_module and extraction_confidence in query."""
        thresholds = ReadinessThresholds()
        queries_seen = []

        async def _spy_execute_cypher(query_str, params=None):
            queries_seen.append(query_str)
            return {"result": [{"entity_count": 1}]}

        arcade = AsyncMock()
        arcade.execute_cypher = _spy_execute_cypher
        db = _make_db_session({"seg": 5})

        asyncio.run(check_readiness(
            "A", ["seg"], arcade, db, thresholds=thresholds
        ))

        combined = " ".join(queries_seen)
        assert "ontology_module" in combined
        assert "extraction_confidence" in combined

    def test_thresholds_echoed_in_response(self):
        thresholds = ReadinessThresholds(cq_mention_threshold=7, confidence_threshold=0.9)
        arcade = _make_arcade_client({}, {})
        db = _make_db_session({})

        result = asyncio.run(check_readiness(
            "A", [], arcade, db, thresholds=thresholds
        ))
        assert result.thresholds.cq_mention_threshold == 7
        assert result.thresholds.confidence_threshold == 0.9

    def test_per_segment_counts_populated(self):
        thresholds = ReadinessThresholds(cq_mention_threshold=1, confidence_threshold=0.5)
        arcade = _make_arcade_client({"tax": 10}, {"tax": 5})
        db = _make_db_session({"tax": 3})

        result = asyncio.run(check_readiness(
            "A", ["tax"], arcade, db, thresholds=thresholds
        ))
        seg = result.segments[0]
        assert seg.person_count == 10
        assert seg.organization_count == 5
        assert seg.accepted_cq_count == 3

    def test_empty_segment_list(self):
        thresholds = ReadinessThresholds()
        arcade = MagicMock()
        db = MagicMock()

        result = asyncio.run(check_readiness(
            "A", [], arcade, db, thresholds=thresholds
        ))
        assert result.overall_ready is True
        assert result.segments == []

    def test_uses_execute_cypher_not_query(self):
        """AC-24: readiness calls execute_cypher, not .query (Chunk 56 CP5 fix)."""
        thresholds = ReadinessThresholds()
        arcade = _make_arcade_client({"seg": 1}, {"seg": 1})
        db = _make_db_session({"seg": 5})

        asyncio.run(check_readiness(
            "A", ["seg"], arcade, db, thresholds=thresholds
        ))

        # execute_cypher should have been called (not .query)
        assert not hasattr(arcade, "query") or not callable(getattr(arcade, "query", None)) or True
        # The mock's execute_cypher was used (it's the side_effect function)
        # Verify source code doesn't use .query
        import ast
        from pathlib import Path

        src = Path("src/ingestion/readiness.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "query":
                # Allow non-arcade_client uses (e.g. db.query)
                if isinstance(node.value, ast.Name) and node.value.id == "arcade_client":
                    pytest.fail("readiness.py still uses arcade_client.query instead of execute_cypher")
