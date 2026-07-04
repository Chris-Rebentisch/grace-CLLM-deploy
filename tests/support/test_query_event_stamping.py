"""Query_Event support_session_id stamping tests (Chunk 45, CP5, D377).

Validates that persist_query_response() stamps the support_session_id
property on the Query_Event vertex when present, and omits it when None.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.retrieval.query_event_writer import persist_query_response


@pytest.fixture
def mock_client():
    """ArcadeDB client mock that records cypher calls."""
    client = AsyncMock()
    client.execute_cypher = AsyncMock(return_value={"result": []})
    return client


@pytest.mark.asyncio
async def test_support_session_id_stamped_on_vertex(mock_client):
    """When support_session_id is non-None, property appears in CREATE."""
    session_id = str(uuid4())
    result = await persist_query_response(
        client=mock_client,
        query_event_id=str(uuid4()),
        query_text="test query",
        results=[],
        response_metadata={},
        support_session_id=session_id,
    )
    assert result["query_events_created"] == 1

    # Find the CREATE Query_Event call.
    calls = mock_client.execute_cypher.call_args_list
    create_calls = [c for c in calls if "CREATE" in str(c) and "Query_Event" in str(c)]
    assert len(create_calls) >= 1
    cypher = str(create_calls[0])
    assert session_id in cypher


@pytest.mark.asyncio
async def test_support_session_id_omitted_when_none(mock_client):
    """When support_session_id is None, property is absent from CREATE."""
    result = await persist_query_response(
        client=mock_client,
        query_event_id=str(uuid4()),
        query_text="test query no session",
        results=[],
        response_metadata={},
        support_session_id=None,
    )
    assert result["query_events_created"] == 1

    calls = mock_client.execute_cypher.call_args_list
    create_calls = [c for c in calls if "CREATE" in str(c) and "Query_Event" in str(c)]
    assert len(create_calls) >= 1
    cypher = str(create_calls[0])
    assert "support_session_id" not in cypher


@pytest.mark.asyncio
async def test_existing_params_still_work(mock_client):
    """Existing sensitivity_tags param still works alongside support_session_id."""
    result = await persist_query_response(
        client=mock_client,
        query_event_id=str(uuid4()),
        query_text="test both params",
        results=[],
        response_metadata={},
        sensitivity_tags=["pii"],
        sensitivity_tags_matrix_id=str(uuid4()),
        support_session_id=str(uuid4()),
    )
    assert result["query_events_created"] == 1


def test_query_event_properties_includes_support_session_id():
    """QUERY_EVENT_PROPERTIES registry includes support_session_id."""
    from src.graph.migration_types import QUERY_EVENT_PROPERTIES

    names = {p["name"] for p in QUERY_EVENT_PROPERTIES}
    assert "support_session_id" in names
