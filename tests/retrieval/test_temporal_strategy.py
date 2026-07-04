"""Tests for temporal filter and strategy."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from src.graph.arcade_client import ArcadeClient, ArcadeConfig
from src.retrieval.retrieval_config import RetrievalConfig
from src.retrieval.retrieval_models import RetrievalCandidate, RetrievalQuery
from src.retrieval.temporal_strategy import apply_temporal_filter, temporal_search


def _candidate(
    grace_id: str, valid_from: str | None = None, valid_to: str | None = None
) -> RetrievalCandidate:
    props: dict = {"name": f"entity-{grace_id}"}
    if valid_from:
        props["valid_from"] = valid_from
    if valid_to:
        props["valid_to"] = valid_to
    return RetrievalCandidate(
        grace_id=grace_id,
        entity_type="Entity",
        name=f"entity-{grace_id}",
        properties=props,
        strategy="graph",
    )


def test_temporal_filter_removes_out_of_range():
    """Temporal filter removes entities outside date range."""
    candidates = [
        _candidate("a", valid_from="2020-01-01T00:00:00", valid_to="2021-01-01T00:00:00"),
        _candidate("b", valid_from="2023-01-01T00:00:00", valid_to="2024-01-01T00:00:00"),
    ]
    filtered = apply_temporal_filter(
        candidates,
        temporal_start=datetime(2022, 1, 1, tzinfo=timezone.utc),
        temporal_end=datetime(2023, 6, 1, tzinfo=timezone.utc),
    )
    # "a" ends in 2021, before our start of 2022 → excluded
    # "b" starts in 2023, within our window → included
    assert len(filtered) == 1
    assert filtered[0].grace_id == "b"


def test_temporal_filter_keeps_null_valid_to():
    """Temporal filter keeps entities with null valid_to (assumed current)."""
    candidates = [
        _candidate("a", valid_from="2020-01-01T00:00:00"),  # no valid_to
        _candidate("b", valid_from="2020-01-01T00:00:00", valid_to="2019-01-01T00:00:00"),
    ]
    filtered = apply_temporal_filter(
        candidates,
        temporal_start=datetime(2020, 6, 1, tzinfo=timezone.utc),
        temporal_end=None,
    )
    # "a" has no valid_to → current → keep
    # "b" ends in 2019, before start of 2020-06 → excluded
    assert len(filtered) == 1
    assert filtered[0].grace_id == "a"


@pytest.mark.asyncio
async def test_temporal_strategy_returns_results():
    """Temporal strategy (mode=STRATEGY) returns ranked results from graph."""
    client = ArcadeClient(config=ArcadeConfig())
    client.execute_cypher = AsyncMock(return_value={
        "result": [
            {"grace_id": "id-1", "@type": "Event", "name": "Meeting", "_deprecated": False},
        ]
    })
    query = RetrievalQuery(
        query_text="meetings in 2023",
        temporal_start=datetime(2023, 1, 1, tzinfo=timezone.utc),
        temporal_end=datetime(2023, 12, 31, tzinfo=timezone.utc),
    )
    config = RetrievalConfig(temporal_as_strategy=True)
    results = await temporal_search(client, query, config)

    assert len(results) == 1
    assert results[0].strategy == "temporal"
    assert results[0].grace_id == "id-1"


def test_temporal_filter_no_date_range_returns_all():
    """Temporal filter with no date range returns all candidates."""
    candidates = [_candidate("a"), _candidate("b")]
    filtered = apply_temporal_filter(candidates, None, None)
    assert len(filtered) == 2
