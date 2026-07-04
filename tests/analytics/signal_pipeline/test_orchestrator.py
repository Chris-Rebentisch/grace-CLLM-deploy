"""Orchestrator tests (D247): partial-failure isolation + idempotency."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.analytics.signal_pipeline.base import (
    SignalDetector,
    SignalRecord,
    SignalRunContext,
)
from src.analytics.signal_pipeline.orchestrator import run_all


class _StubGood(SignalDetector):
    signal_type = "A"

    async def detect(self, ctx: SignalRunContext) -> list[SignalRecord]:
        return [
            SignalRecord(
                run_id=ctx.run_id,
                signal_type="A",
                ontology_module="m1",
                strength=0.5,
                evidence_snapshot={"ok": True},
                detected_at=datetime.now(UTC),
            )
        ]


class _StubGoodB(SignalDetector):
    signal_type = "B"

    async def detect(self, ctx: SignalRunContext) -> list[SignalRecord]:
        return [
            SignalRecord(
                run_id=ctx.run_id,
                signal_type="B",
                ontology_module="m1",
                strength=0.25,
                evidence_snapshot={"ok": True},
                detected_at=datetime.now(UTC),
            )
        ]


class _StubFails(SignalDetector):
    signal_type = "C"

    async def detect(self, ctx: SignalRunContext) -> list[SignalRecord]:
        raise RuntimeError("synthetic failure")


@pytest.mark.asyncio
async def test_run_all_partial_failure_isolates_failing_detector(
    signal_run_context, test_session_factory, cleanup_signal_tables
):
    """One detector failing → status='partial_failure', siblings persisted."""
    ctx = signal_run_context()
    run, records = await run_all(
        [_StubGood(), _StubFails(), _StubGoodB()],
        context=ctx,
        session_factory=test_session_factory,
    )
    assert run.status == "partial_failure"
    assert {r.signal_type for r in records} == {"A", "B"}


@pytest.mark.asyncio
async def test_run_all_all_success(
    signal_run_context, test_session_factory, cleanup_signal_tables
):
    ctx = signal_run_context()
    run, records = await run_all(
        [_StubGood(), _StubGoodB()],
        context=ctx,
        session_factory=test_session_factory,
    )
    assert run.status == "success"
    assert len(records) == 2


@pytest.mark.asyncio
async def test_run_all_dry_run_does_not_persist(
    signal_run_context, test_session_factory, test_engine
):
    """dry_run=True returns records without writing to DB."""
    from sqlalchemy import text

    ctx = signal_run_context()
    run, records = await run_all(
        [_StubGood()],
        context=ctx,
        session_factory=test_session_factory,
        dry_run=True,
    )
    assert run.status == "success"
    assert len(records) == 1
    # No rows should have been written for this run_id.
    with test_engine.connect() as conn:
        n = conn.execute(
            text(
                "SELECT COUNT(*) FROM analytics_signals WHERE run_id = :rid"
            ),
            {"rid": str(ctx.run_id)},
        ).scalar_one()
    assert n == 0
