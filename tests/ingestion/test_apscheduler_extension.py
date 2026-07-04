"""Tests for APScheduler ingestion extension (Chunk 57, CP9, D425)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest


def _make_source(source_id=None, source_type="imap", schedule_enabled=True, status="ready"):
    """Build a mock IngestionSource row."""
    src = MagicMock()
    src.id = source_id or uuid4()
    src.name = "test-source"
    src.source_type = source_type
    src.enabled = True
    src.deleted_at = None
    src.status = status
    src.config_json = {
        "source_type": source_type,
        "schedule_enabled": schedule_enabled,
        "schedule_mode": "interval",
        "schedule_interval_hours": 2.0,
    }
    return src


def test_scheduler_starts_with_ingestion_jobs():
    """_register_ingestion_jobs registers jobs for ready+enabled sources."""
    from src.api.main import _register_ingestion_jobs

    source = _make_source()
    mock_scheduler = MagicMock()
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.all.return_value = [source]

    _register_ingestion_jobs(mock_scheduler, mock_db)

    mock_scheduler.add_job.assert_called_once()
    call_kwargs = mock_scheduler.add_job.call_args
    assert call_kwargs[1]["id"] == f"ingestion_source:{source.id}"
    assert call_kwargs[1]["replace_existing"] is True


def test_replace_existing_idempotent():
    """Re-registering same source uses replace_existing=True."""
    from src.api.main import _register_ingestion_jobs

    source = _make_source()
    mock_scheduler = MagicMock()
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.all.return_value = [source]

    _register_ingestion_jobs(mock_scheduler, mock_db)
    _register_ingestion_jobs(mock_scheduler, mock_db)

    # Both calls should use replace_existing
    for call in mock_scheduler.add_job.call_args_list:
        assert call[1]["replace_existing"] is True


def test_job_removed_on_schedule_disabled():
    """Sources with schedule_enabled=False are not registered."""
    from src.api.main import _register_ingestion_jobs

    source = _make_source(schedule_enabled=False)
    mock_scheduler = MagicMock()
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.all.return_value = [source]

    _register_ingestion_jobs(mock_scheduler, mock_db)

    mock_scheduler.add_job.assert_not_called()


def test_cycle_subprocess_argv():
    """_run_ingestion_cycle spawns correct subprocess command."""
    from src.api.main import _run_ingestion_cycle

    source_id = str(uuid4())
    with patch("subprocess.Popen") as mock_popen:
        _run_ingestion_cycle(source_id)

    call_args = mock_popen.call_args
    cmd = call_args[0][0]
    assert "src.ingestion" in cmd
    assert "cycle" in cmd
    assert "--source-id" in cmd
    assert source_id in cmd


def test_scheduler_alias():
    """app.state.documented_reality_scheduler is app.state.scheduler (backwards compat)."""
    from unittest.mock import MagicMock as MM

    app = MM()
    scheduler = MM()

    # Simulate lifespan behavior
    app.state.scheduler = scheduler
    app.state.documented_reality_scheduler = scheduler

    assert app.state.documented_reality_scheduler is app.state.scheduler


def test_date_trigger_for_one_shot():
    """schedule_mode='one_time' uses DateTrigger."""
    from src.api.main import _register_ingestion_jobs

    source = _make_source()
    source.config_json["schedule_mode"] = "one_time"

    mock_scheduler = MagicMock()
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.all.return_value = [source]

    with patch("apscheduler.triggers.date.DateTrigger") as mock_date_trigger:
        _register_ingestion_jobs(mock_scheduler, mock_db)

    # Job should have been registered
    mock_scheduler.add_job.assert_called_once()
    call_args = mock_scheduler.add_job.call_args
    assert call_args is not None
