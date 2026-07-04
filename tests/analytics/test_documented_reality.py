"""Tests for the Documented Reality Report generator (Chunk 37, D286).

Five tests:

1. Aggregator output shape (Pydantic validation).
2. Template constant is a non-empty string.
3. Regeneration pipeline is called as client (mock).
4. Scheduled-vs-on-demand path (trigger label).
5. Empty-corpus carve-out (``corpus_below_floor=True`` when V count
   below ``corpus_floor``).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.analytics.documented_reality import (
    DOCUMENTED_REALITY_SYSTEM_PROMPT,
    DocumentedRealityConfig,
    compute_documented_reality_aggregations,
    generate_documented_reality_report,
)
from src.api.recon_models import (
    DocumentedRealityAggregations,
    DocumentedRealityReportResponse,
)


@pytest.mark.asyncio
async def test_aggregator_output_validates_against_pydantic_model():
    """Aggregator returns a structurally valid
    ``DocumentedRealityAggregations`` (extra='forbid' discipline)."""
    fake = AsyncMock()
    # Current flow (Phase-6 ArcadeDB fix): enumerate types via `schema:types`,
    # then issue one count query per type (ArcadeDB has no generic V/E supertype).
    fake.execute_sql = AsyncMock(
        side_effect=[
            # 1. schema:types enumeration → 2 vertex types + 1 edge type.
            {"result": [
                {"name": "Company", "type": "v"},
                {"name": "Person", "type": "v"},
                {"name": "works_at", "type": "e"},
            ]},
            # 2. per-vertex-type counts (70 + 30 = 100).
            {"result": [{"cnt": 70}]},
            {"result": [{"cnt": 30}]},
            # 3. per-edge-type count.
            {"result": [{"cnt": 50}]},
        ]
    )
    aggs = await compute_documented_reality_aggregations(fake)
    assert isinstance(aggs, DocumentedRealityAggregations)
    assert aggs.total_vertices == 100
    assert aggs.total_edges == 50
    assert len(aggs.top_entities) == 2


def test_template_constant_is_non_empty_string():
    """The system-prompt template must be a non-empty descriptive string
    free of forbidden vocabulary (EC-11/EC-12)."""
    assert isinstance(DOCUMENTED_REALITY_SYSTEM_PROMPT, str)
    assert len(DOCUMENTED_REALITY_SYSTEM_PROMPT.strip()) > 0
    forbidden = (
        "drift",
        "blind spot",
        "mistake",
        "wrong",
        "reality gap",
        "incorrect",
        "failure",
        "deficit",
    )
    body = DOCUMENTED_REALITY_SYSTEM_PROMPT.lower()
    for token in forbidden:
        assert token not in body, f"forbidden token in template: {token!r}"


@pytest.mark.asyncio
async def test_regeneration_pipeline_is_called_as_client():
    """When corpus is above the floor and a regeneration pipeline is
    supplied, the generator calls it as a client and embeds the
    returned narrative."""
    aggs = DocumentedRealityAggregations(
        top_entities=[{"type_name": "Company", "count": 60}],
        top_relationships=[],
        legal_entities=[],
        monetary_flow={},
        participants=[],
        business_activity_signature={},
        total_vertices=100,
        total_edges=50,
    )

    class _FakeResult:
        regenerated_text = "A documented summary of organization activity."

    fake_pipeline = AsyncMock()
    fake_pipeline.query = AsyncMock(return_value=_FakeResult())

    response = await generate_documented_reality_report(
        aggregations=aggs,
        retrieval_pipeline=None,
        regeneration_pipeline=fake_pipeline,
        trigger="on_demand",
    )
    assert isinstance(response, DocumentedRealityReportResponse)
    assert response.corpus_below_floor is False
    assert fake_pipeline.query.await_count == 1
    assert response.narrative is not None


@pytest.mark.asyncio
async def test_trigger_label_round_trips_for_scheduled_and_on_demand():
    """The ``trigger`` literal flows from caller into the response."""
    aggs = DocumentedRealityAggregations(
        top_entities=[],
        top_relationships=[],
        legal_entities=[],
        monetary_flow={},
        participants=[],
        business_activity_signature={},
        total_vertices=100,
        total_edges=0,
    )

    class _FakeResult:
        regenerated_text = "ok"

    fake_pipeline = AsyncMock()
    fake_pipeline.query = AsyncMock(return_value=_FakeResult())

    on_demand = await generate_documented_reality_report(
        aggregations=aggs,
        retrieval_pipeline=None,
        regeneration_pipeline=fake_pipeline,
        trigger="on_demand",
    )
    assert on_demand.trigger == "on_demand"

    scheduled = await generate_documented_reality_report(
        aggregations=aggs,
        retrieval_pipeline=None,
        regeneration_pipeline=fake_pipeline,
        trigger="scheduled",
    )
    assert scheduled.trigger == "scheduled"


@pytest.mark.asyncio
async def test_empty_corpus_carve_out_skips_llm_call(monkeypatch):
    """When V count is below ``corpus_floor`` (default 50), the
    regeneration pipeline must not be called and ``corpus_below_floor``
    is True with ``narrative=None``."""
    aggs = DocumentedRealityAggregations(
        top_entities=[],
        top_relationships=[],
        legal_entities=[],
        monetary_flow={},
        participants=[],
        business_activity_signature={},
        total_vertices=10,  # below default floor of 50
        total_edges=0,
    )
    fake_pipeline = AsyncMock()
    fake_pipeline.query = AsyncMock()

    response = await generate_documented_reality_report(
        aggregations=aggs,
        retrieval_pipeline=None,
        regeneration_pipeline=fake_pipeline,
        trigger="on_demand",
        config=DocumentedRealityConfig(corpus_floor=50),
    )
    assert response.corpus_below_floor is True
    assert response.narrative is None
    assert fake_pipeline.query.await_count == 0
