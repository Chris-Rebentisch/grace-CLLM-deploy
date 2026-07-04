"""Chunk 39 D301 — Typer CLI smoke for snapshot pipeline."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from src.change_directives.snapshot_pipeline.cli import app


def test_cli_run_all_dry_run_exits_zero():
    runner = CliRunner()
    mock_session = MagicMock()
    run_snapshots_mock = AsyncMock(return_value=None)
    with patch(
        "src.change_directives.snapshot_pipeline.cli.get_arcade_client"
    ) as gac, patch(
        "src.change_directives.snapshot_pipeline.cli.get_session_factory",
        return_value=lambda: mock_session,
    ), patch(
        "src.change_directives.snapshot_pipeline.cli.run_snapshots",
        run_snapshots_mock,
    ):
        result = runner.invoke(app, ["--dry-run"])
        assert result.exit_code == 0, result.output
        assert run_snapshots_mock.await_count == 1
    gac.assert_called_once()
    mock_session.close.assert_called_once()


def test_cli_help():
    runner = CliRunner()
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    assert "--dry-run" in r.output
