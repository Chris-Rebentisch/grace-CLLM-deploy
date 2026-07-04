"""Chunk 39 — snapshot orchestrator behavior (dry-run, empty fleet)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.change_directives.snapshot_pipeline.config import load_snapshot_config
from src.change_directives.snapshot_pipeline.orchestrator import run_snapshots


@pytest.fixture()
def session():
    """Orchestrator tests mock persistence; a lightweight stand-in suffices."""
    return MagicMock()


@pytest.mark.asyncio
async def test_run_snapshots_no_directives_skips_insert(session):
    arcade = MagicMock()
    cfg = load_snapshot_config()
    obs = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
    with patch(
        "src.change_directives.snapshot_pipeline.orchestrator.repository."
        "list_active_directives_for_snapshots",
        return_value=[],
    ), patch(
        "src.change_directives.snapshot_pipeline.orchestrator.repository."
        "insert_realization_snapshot"
    ) as ins:
        await run_snapshots(session, arcade, cfg, obs, dry_run=False)
        ins.assert_not_called()


@pytest.mark.asyncio
async def test_run_snapshots_dry_run_skips_persist(session):
    arcade = MagicMock()
    cfg = load_snapshot_config()
    obs = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
    did = uuid4()
    stub_row = {
        "directive_id": str(did),
        "tier": "Operational_Adjustment",
        "status": "active",
    }
    fake_cr = MagicMock()
    fake_cr.criterion_id = uuid4()
    fake_cr.satisfied = False
    fake_cr.measured_value = None
    fake_cr.query_executed_at = obs
    fake_cr.result_hash = "ab" * 32
    fake_cr.sample_grace_ids = []
    fake_cr.counter_evidence = None

    with patch(
        "src.change_directives.snapshot_pipeline.orchestrator.repository."
        "list_active_directives_for_snapshots",
        return_value=[stub_row],
    ), patch(
        "src.change_directives.snapshot_pipeline.orchestrator.repository."
        "get_latest_snapshot",
        return_value=None,
    ), patch(
        "src.change_directives.snapshot_pipeline.orchestrator._load_criteria",
        return_value=[
            {
                "criterion_id": str(fake_cr.criterion_id),
                "compiled_query": "RETURN 1",
            }
        ],
    ), patch(
        "src.change_directives.snapshot_pipeline.orchestrator.execute_criterion",
        new_callable=AsyncMock,
        return_value=fake_cr,
    ), patch(
        "src.change_directives.snapshot_pipeline.orchestrator.repository."
        "insert_realization_snapshot"
    ) as ins:
        await run_snapshots(session, arcade, cfg, obs, dry_run=True)
        ins.assert_not_called()


@pytest.mark.asyncio
async def test_run_snapshots_persists_when_not_dry_run(session):
    arcade = MagicMock()
    cfg = load_snapshot_config()
    obs = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
    did = uuid4()
    stub_row = {
        "directive_id": str(did),
        "tier": "Operational_Adjustment",
        "status": "active",
    }
    fake_cr = MagicMock()
    fake_cr.criterion_id = uuid4()
    fake_cr.satisfied = True
    fake_cr.measured_value = 1.0
    fake_cr.query_executed_at = obs
    fake_cr.result_hash = "cd" * 32
    fake_cr.sample_grace_ids = ["g1"]
    fake_cr.counter_evidence = {"sample_grace_ids": ["x"]}

    with patch(
        "src.change_directives.snapshot_pipeline.orchestrator.repository."
        "list_active_directives_for_snapshots",
        return_value=[stub_row],
    ), patch(
        "src.change_directives.snapshot_pipeline.orchestrator.repository."
        "get_latest_snapshot",
        return_value=None,
    ), patch(
        "src.change_directives.snapshot_pipeline.orchestrator._load_criteria",
        return_value=[
            {
                "criterion_id": str(fake_cr.criterion_id),
                "compiled_query": "RETURN 1",
            }
        ],
    ), patch(
        "src.change_directives.snapshot_pipeline.orchestrator.execute_criterion",
        new_callable=AsyncMock,
        return_value=fake_cr,
    ), patch(
        "src.change_directives.snapshot_pipeline.orchestrator.repository."
        "insert_realization_snapshot"
    ) as ins:
        await run_snapshots(session, arcade, cfg, obs, dry_run=False)
        assert ins.call_count == 1
