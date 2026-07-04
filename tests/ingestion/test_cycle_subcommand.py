"""Tests for the ``cycle`` subcommand (Chunk 57, CP4)."""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.ingestion.adapter_base import AdapterAuthError, AdapterFatalError


def test_cycle_help():
    """cycle subcommand appears in help output."""
    result = subprocess.run(
        [sys.executable, "-m", "src.ingestion", "--help"],
        capture_output=True,
        text=True,
    )
    assert "cycle" in result.stdout


def test_cycle_requires_source_id():
    """cycle requires --source-id."""
    result = subprocess.run(
        [sys.executable, "-m", "src.ingestion", "cycle"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2


def _make_cycle_args(source_id=None, dry_run=True, limit=5, tiers="1,2,3,4"):
    args = MagicMock()
    args.source_id = str(source_id or uuid4())
    args.dry_run = dry_run
    args.limit = limit
    args.tiers = tiers
    args.checkpoint_interval = None
    return args


def _patch_config(deployment_path="A"):
    """Patch the discovery.yaml read inside ``_run_cycle``.

    F-0030d / ISS-0047: the shipped default is ``deployment_path: null`` and
    ``cycle`` now refuses to run on a missing/null/invalid value, so tests of
    the downstream pull/triage chain must pin an explicit valid path instead
    of leaning on the old silent-"A" default.
    """
    return patch(
        "yaml.safe_load",
        return_value={"ingestion": {"deployment_path": deployment_path}},
    )


@pytest.mark.asyncio
async def test_cycle_chains_pull_and_triage():
    """cycle chains adapter pull → triage when readiness passes."""
    from src.ingestion.__main__ import _run_cycle

    args = _make_cycle_args()

    mock_source = MagicMock()
    mock_source.segment = "test"
    mock_source.source_type = "mbox"

    mock_readiness = MagicMock()
    mock_readiness.overall_ready = True

    mock_pipeline = MagicMock()
    mock_pipeline.run = AsyncMock(return_value=uuid4())
    mock_pipeline._shutdown_requested = False

    mock_triage = MagicMock()
    mock_triage.run = AsyncMock(return_value=5)

    mock_db = MagicMock()
    mock_db.query.return_value.filter_by.return_value.first.return_value = mock_source

    with (
        patch("src.shared.database.get_session_factory", return_value=lambda: mock_db),
        _patch_config(),
        patch("src.ingestion.readiness.check_readiness", new_callable=AsyncMock, return_value=mock_readiness),
        patch("src.graph.arcade_client.get_arcade_client", return_value=MagicMock()),
        patch("src.ingestion.pipeline.IngestionPipeline", return_value=mock_pipeline),
        patch("src.ingestion.communications.triage.pipeline.TriagePipeline", return_value=mock_triage),
    ):
        await _run_cycle(args)

    mock_pipeline.run.assert_called_once()
    mock_triage.run.assert_called_once()


@pytest.mark.asyncio
async def test_cycle_readiness_gate_blocks():
    """cycle exits 1 when readiness gate fails."""
    from src.ingestion.__main__ import _run_cycle

    args = _make_cycle_args()

    mock_source = MagicMock()
    mock_source.segment = "test"

    mock_readiness = MagicMock()
    mock_readiness.overall_ready = False

    mock_db = MagicMock()
    mock_db.query.return_value.filter_by.return_value.first.return_value = mock_source

    with (
        patch("src.shared.database.get_session_factory", return_value=lambda: mock_db),
        _patch_config(),
        patch("src.ingestion.readiness.check_readiness", new_callable=AsyncMock, return_value=mock_readiness),
        patch("src.graph.arcade_client.get_arcade_client", return_value=MagicMock()),
    ):
        with pytest.raises(SystemExit) as exc_info:
            await _run_cycle(args)
        assert exc_info.value.code == 1


@pytest.mark.asyncio
async def test_cycle_skip_triage_on_adapter_auth_error():
    """Triage skipped when adapter pull raises AdapterAuthError."""
    from src.ingestion.__main__ import _run_cycle

    args = _make_cycle_args()

    mock_source = MagicMock()
    mock_source.segment = "test"

    mock_readiness = MagicMock()
    mock_readiness.overall_ready = True

    mock_pipeline = MagicMock()
    mock_pipeline.run = AsyncMock(side_effect=AdapterAuthError("bad creds"))
    mock_pipeline._shutdown_requested = False

    mock_triage = MagicMock()
    mock_triage.run = AsyncMock(return_value=0)

    mock_db = MagicMock()
    mock_db.query.return_value.filter_by.return_value.first.return_value = mock_source

    with (
        patch("src.shared.database.get_session_factory", return_value=lambda: mock_db),
        _patch_config(),
        patch("src.ingestion.readiness.check_readiness", new_callable=AsyncMock, return_value=mock_readiness),
        patch("src.graph.arcade_client.get_arcade_client", return_value=MagicMock()),
        patch("src.ingestion.pipeline.IngestionPipeline", return_value=mock_pipeline),
        patch("src.ingestion.communications.triage.pipeline.TriagePipeline", return_value=mock_triage),
    ):
        await _run_cycle(args)

    mock_triage.run.assert_not_called()


@pytest.mark.asyncio
async def test_cycle_triage_not_skipped_on_cursor_expired():
    """Triage NOT skipped on normal pipeline completion (CursorExpired handled internally)."""
    from src.ingestion.__main__ import _run_cycle

    args = _make_cycle_args()

    mock_source = MagicMock()
    mock_source.segment = "test"

    mock_readiness = MagicMock()
    mock_readiness.overall_ready = True

    mock_pipeline = MagicMock()
    mock_pipeline.run = AsyncMock(return_value=uuid4())
    mock_pipeline._shutdown_requested = False

    mock_triage = MagicMock()
    mock_triage.run = AsyncMock(return_value=5)

    mock_db = MagicMock()
    mock_db.query.return_value.filter_by.return_value.first.return_value = mock_source

    with (
        patch("src.shared.database.get_session_factory", return_value=lambda: mock_db),
        _patch_config(),
        patch("src.ingestion.readiness.check_readiness", new_callable=AsyncMock, return_value=mock_readiness),
        patch("src.graph.arcade_client.get_arcade_client", return_value=MagicMock()),
        patch("src.ingestion.pipeline.IngestionPipeline", return_value=mock_pipeline),
        patch("src.ingestion.communications.triage.pipeline.TriagePipeline", return_value=mock_triage),
    ):
        await _run_cycle(args)

    mock_triage.run.assert_called_once()


@pytest.mark.asyncio
async def test_cycle_dry_run():
    """cycle passes dry_run flag to both pipeline and triage."""
    from src.ingestion.__main__ import _run_cycle

    args = _make_cycle_args(dry_run=True)

    mock_source = MagicMock()
    mock_source.segment = "test"

    mock_readiness = MagicMock()
    mock_readiness.overall_ready = True

    mock_pipeline = MagicMock()
    mock_pipeline.run = AsyncMock(return_value=uuid4())
    mock_pipeline._shutdown_requested = False

    mock_triage = MagicMock()
    mock_triage.run = AsyncMock(return_value=0)

    mock_db = MagicMock()
    mock_db.query.return_value.filter_by.return_value.first.return_value = mock_source

    with (
        patch("src.shared.database.get_session_factory", return_value=lambda: mock_db),
        _patch_config(),
        patch("src.ingestion.readiness.check_readiness", new_callable=AsyncMock, return_value=mock_readiness),
        patch("src.graph.arcade_client.get_arcade_client", return_value=MagicMock()),
        patch("src.ingestion.pipeline.IngestionPipeline", return_value=mock_pipeline),
        patch("src.ingestion.communications.triage.pipeline.TriagePipeline", return_value=mock_triage),
    ):
        await _run_cycle(args)

    # Verify dry_run was passed
    call_kwargs = mock_pipeline.run.call_args
    assert call_kwargs[1]["dry_run"] is True


# --- deployment_path validation (F-0030d / ISS-0047, closed 2026-07-03) -----


def _deployment_path_mocks():
    mock_source = MagicMock()
    mock_source.segment = "test"
    mock_db = MagicMock()
    mock_db.query.return_value.filter_by.return_value.first.return_value = mock_source
    return mock_db


@pytest.mark.asyncio
async def test_cycle_null_deployment_path_exits_with_guidance(capsys):
    """Explicit-null (the shipped default) exits 2 with operator guidance
    instead of silently assuming path A."""
    from src.ingestion.__main__ import _run_cycle

    args = _make_cycle_args()
    mock_db = _deployment_path_mocks()

    with (
        patch("src.shared.database.get_session_factory", return_value=lambda: mock_db),
        _patch_config(deployment_path=None),
    ):
        with pytest.raises(SystemExit) as exc_info:
            await _run_cycle(args)
    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "set ingestion.deployment_path to A, B, or C" in err
    assert "PATCH /api/ingestion/config/deployment-path" in err


@pytest.mark.asyncio
async def test_cycle_missing_deployment_path_exits_with_guidance(capsys):
    """A MISSING key no longer silently defaults to 'A' — same guidance exit."""
    from src.ingestion.__main__ import _run_cycle

    args = _make_cycle_args()
    mock_db = _deployment_path_mocks()

    with (
        patch("src.shared.database.get_session_factory", return_value=lambda: mock_db),
        patch("yaml.safe_load", return_value={"ingestion": {}}),
    ):
        with pytest.raises(SystemExit) as exc_info:
            await _run_cycle(args)
    assert exc_info.value.code == 2
    assert "set ingestion.deployment_path to A, B, or C" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_cycle_invalid_deployment_path_exits_with_guidance(capsys):
    """An invalid value (hand-edited yaml) gets the same guidance, mirroring
    the route-side validation on GET /api/ingestion/readiness."""
    from src.ingestion.__main__ import _run_cycle

    args = _make_cycle_args()
    mock_db = _deployment_path_mocks()

    with (
        patch("src.shared.database.get_session_factory", return_value=lambda: mock_db),
        _patch_config(deployment_path="Z"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            await _run_cycle(args)
    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "'Z'" in err
    assert "set ingestion.deployment_path to A, B, or C" in err


@pytest.mark.asyncio
async def test_cycle_valid_deployment_path_proceeds():
    """deployment_path 'A' proceeds through readiness to the pull."""
    from src.ingestion.__main__ import _run_cycle

    args = _make_cycle_args()
    mock_db = _deployment_path_mocks()

    mock_readiness = MagicMock()
    mock_readiness.overall_ready = True

    mock_pipeline = MagicMock()
    mock_pipeline.run = AsyncMock(return_value=uuid4())
    mock_pipeline._shutdown_requested = False

    mock_triage = MagicMock()
    mock_triage.run = AsyncMock(return_value=0)

    with (
        patch("src.shared.database.get_session_factory", return_value=lambda: mock_db),
        _patch_config(deployment_path="A"),
        patch("src.ingestion.readiness.check_readiness", new_callable=AsyncMock, return_value=mock_readiness),
        patch("src.graph.arcade_client.get_arcade_client", return_value=MagicMock()),
        patch("src.ingestion.pipeline.IngestionPipeline", return_value=mock_pipeline),
        patch("src.ingestion.communications.triage.pipeline.TriagePipeline", return_value=mock_triage),
    ):
        await _run_cycle(args)

    mock_pipeline.run.assert_called_once()
