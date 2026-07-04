"""Tests for Extraction_Event provenance tracking."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pydantic import BaseModel

from src.extraction.provenance import (
    VALID_TRANSITIONS,
    create_extraction_event_vertex,
    create_produced_by_edges,
    update_event_status_after_write,
    validate_status_transition,
)


class TestStatusTransition:
    def test_status_transition_valid(self):
        for current, targets in VALID_TRANSITIONS.items():
            for target in targets:
                validate_status_transition(current, target)

    def test_status_transition_invalid(self):
        with pytest.raises(ValueError, match="Illegal status transition"):
            validate_status_transition("running", "graph_written")

        with pytest.raises(ValueError, match="Illegal status transition"):
            validate_status_transition("verified", "running")


class _FakeWriteResult(BaseModel):
    entities_created: int = 0
    entities_matched: int = 0
    entities_failed: int = 0
    relationships_created: int = 0
    relationships_failed: int = 0


class TestUpdateEventStatus:
    def test_graph_written_all_success(self):
        session = MagicMock()
        wr = _FakeWriteResult(entities_created=3, relationships_created=2)
        with patch("src.extraction.provenance.update_extraction_event_status"):
            status = update_event_status_after_write(session, "evt-1", wr)
        assert status == "graph_written"

    def test_graph_failed_zero_success(self):
        session = MagicMock()
        wr = _FakeWriteResult(entities_failed=2, relationships_failed=1)
        with patch("src.extraction.provenance.update_extraction_event_status"):
            status = update_event_status_after_write(session, "evt-1", wr)
        assert status == "graph_failed"

    def test_partial_failed_mixed(self):
        session = MagicMock()
        wr = _FakeWriteResult(entities_created=1, entities_failed=1)
        with patch("src.extraction.provenance.update_extraction_event_status"):
            status = update_event_status_after_write(session, "evt-1", wr)
        assert status == "partial_failed"


@pytest.mark.asyncio
class TestCreateExtractionEventVertex:
    async def test_creates_vertex_returns_grace_id(self):
        client = AsyncMock()
        client.execute_cypher.return_value = {"result": [{"n": {}}]}
        grace_id = await create_extraction_event_vertex(client, {
            "extraction_event_id": "evt-abc",
            "batch_id": "batch-1",
            "status": "graph_written",
        })
        assert isinstance(grace_id, str)
        assert len(grace_id) == 36  # UUID length
        client.execute_cypher.assert_called_once()
        call_arg = client.execute_cypher.call_args[0][0]
        assert "Extraction_Event" in call_arg
        assert "evt-abc" in call_arg


@pytest.mark.asyncio
class TestCreateProducedByEdges:
    async def test_edges_created(self):
        client = AsyncMock()
        # First call: check existence (cnt=0), second: create edge
        client.execute_cypher.side_effect = [
            {"result": [{"cnt": 0}]},
            {"result": [{"a.grace_id": "e1"}]},
        ]
        count = await create_produced_by_edges(
            client, ["entity-1"], "event-gid", "evt-pg-id"
        )
        assert count == 1

    async def test_not_duplicated_on_replay(self):
        client = AsyncMock()
        # Edge already exists
        client.execute_cypher.return_value = {"result": [{"cnt": 1}]}
        count = await create_produced_by_edges(
            client, ["entity-1"], "event-gid", "evt-pg-id"
        )
        assert count == 0
