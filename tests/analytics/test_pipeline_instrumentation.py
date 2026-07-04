"""Tests for `record_pipeline_stage` (spec §7, §10.4)."""

from __future__ import annotations

import asyncio

import pytest
from opentelemetry import trace

from src.analytics.pipeline_instrumentation import record_pipeline_stage


@pytest.mark.asyncio
async def test_pipeline_stage_child_span_shares_trace_with_parent(span_exporter):
    """A stage span opened inside an outer span shares trace_id."""
    tracer = trace.get_tracer("test.pipeline")

    with tracer.start_as_current_span("regeneration.run"):
        async with record_pipeline_stage(pipeline="regeneration", stage="retrieve"):
            pass

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 2

    by_name = {s.name: s for s in spans}
    parent = by_name["regeneration.run"]
    child = by_name["regeneration.stage.retrieve"]

    assert child.context.trace_id == parent.context.trace_id
    assert child.parent is not None
    assert child.parent.span_id == parent.context.span_id
    assert child.attributes["grace.pipeline"] == "regeneration"
    assert child.attributes["grace.stage"] == "retrieve"


@pytest.mark.asyncio
async def test_gathered_stages_share_trace_id_with_parent(span_exporter):
    """asyncio.gather of N stages under one parent → 1 parent + N children,
    all sharing trace_id. This is the §7.3 async-context-propagation guard.
    """
    tracer = trace.get_tracer("test.pipeline")
    N = 5

    async def one_stage(i: int) -> None:
        async with record_pipeline_stage(
            pipeline="retrieval", stage=f"strategy_{i}"
        ):
            await asyncio.sleep(0)

    with tracer.start_as_current_span("retrieval.run"):
        await asyncio.gather(*(one_stage(i) for i in range(N)))

    spans = span_exporter.get_finished_spans()
    parent_spans = [s for s in spans if s.name == "retrieval.run"]
    child_spans = [s for s in spans if s.name.startswith("retrieval.stage.strategy_")]

    assert len(parent_spans) == 1
    assert len(child_spans) == N

    parent = parent_spans[0]
    trace_ids = {s.context.trace_id for s in child_spans}
    assert trace_ids == {parent.context.trace_id}


@pytest.mark.asyncio
async def test_pipeline_stage_error_path_records_error_and_reraises(span_exporter):
    """Exceptions set span status to error and are re-raised."""

    class _StageBoom(RuntimeError):
        pass

    with pytest.raises(_StageBoom):
        async with record_pipeline_stage(pipeline="extraction", stage="verify"):
            raise _StageBoom("verifier down")

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].status.status_code.name == "ERROR"
