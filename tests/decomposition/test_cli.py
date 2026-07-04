"""CLI tests for the decomposition pipeline (Chunk 40, CP10)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from src.decomposition.pipeline.cli import app


runner = CliRunner()


def test_cli_run_subcommand_executes(tmp_path: Path):
    """``run --archive-root <archive>`` runs to completion (mocked DB + LLM + orchestrator)."""
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "memo.txt").write_text("ops body. Acme Inc.")

    fake_session = MagicMock()
    fake_session.close.return_value = None

    async def fake_run(**kwargs):
        return {"run_id": "00000000-0000-0000-0000-000000000001",
                "status": "completed"}

    with patch(
        "src.decomposition.pipeline.cli._open_session", return_value=fake_session
    ), patch(
        "src.decomposition.pipeline.cli._resolve_provider", return_value=MagicMock()
    ), patch(
        "src.decomposition.pipeline.cli.run_decomposition", side_effect=fake_run
    ):
        result = runner.invoke(
            app, ["run", "--archive-root", str(archive)]
        )
    assert result.exit_code == 0, result.stdout
    assert "completed" in result.stdout


def test_cli_dry_run_passes_through(tmp_path: Path):
    """``--dry-run`` is forwarded to ``run_decomposition``."""
    archive = tmp_path / "archive"
    archive.mkdir()
    seen: dict = {}
    fake_session = MagicMock()

    async def fake_run(**kwargs):
        seen.update(kwargs)
        return {"run_id": "id", "status": "completed", "dry_run_used": True}

    with patch(
        "src.decomposition.pipeline.cli._open_session", return_value=fake_session
    ), patch(
        "src.decomposition.pipeline.cli._resolve_provider", return_value=MagicMock()
    ), patch(
        "src.decomposition.pipeline.cli.run_decomposition", side_effect=fake_run
    ):
        result = runner.invoke(
            app, ["run", "--archive-root", str(archive), "--dry-run"]
        )
    assert result.exit_code == 0, result.stdout
    assert seen.get("dry_run") is True


def test_cli_resume_on_non_paused_run_raises(tmp_path: Path):
    """``resume`` propagates a ValueError from the repository."""
    fake_session = MagicMock()

    def boom(_session, _id):
        raise ValueError("Run is not paused_pre_layer4")

    with patch(
        "src.decomposition.pipeline.cli._open_session", return_value=fake_session
    ), patch(
        "src.decomposition.pipeline.cli.run_repository.create_resume_run",
        side_effect=boom,
    ):
        result = runner.invoke(
            app,
            ["resume", "--run-id", "00000000-0000-0000-0000-000000000001"],
        )
    assert result.exit_code != 0
    assert "paused_pre_layer4" in str(result.exception) or result.exit_code != 0


def test_cli_status_renders_row():
    """``status`` prints a JSON-formatted run row."""
    fake_session = MagicMock()
    sample_row = {"run_id": "00000000-0000-0000-0000-000000000001",
                  "status": "completed"}
    sample_latest = MagicMock()
    sample_latest._mapping = {"run_id": "00000000-0000-0000-0000-000000000001"}

    fake_session.execute.return_value.one_or_none.return_value = sample_latest

    with patch(
        "src.decomposition.pipeline.cli._open_session", return_value=fake_session
    ), patch(
        "src.decomposition.pipeline.cli.run_repository.get_run",
        return_value=sample_row,
    ):
        result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.stdout
    assert "completed" in result.stdout


def test_cli_run_requires_archive_root_option():
    """``run`` without ``--archive-root`` exits non-zero."""
    result = runner.invoke(app, ["run"])
    assert result.exit_code != 0
    assert (
        "archive-root" in (result.stdout + (result.stderr or "")).lower()
        or "missing" in (result.stdout + (result.stderr or "")).lower()
    )


def test_cli_run_forwards_run_id(tmp_path: Path):
    """F-030 / ISS-0014: ``run --run-id`` forwards the placeholder id so the
    pipeline adopts the API trigger's row instead of INSERTing a new one."""
    from uuid import UUID

    archive = tmp_path / "archive"
    archive.mkdir()
    rid = "00000000-0000-0000-0000-00000000abcd"
    seen: dict = {}
    fake_session = MagicMock()

    async def fake_run(**kwargs):
        seen.update(kwargs)
        return {"run_id": rid, "status": "completed"}

    with patch(
        "src.decomposition.pipeline.cli._open_session", return_value=fake_session
    ), patch(
        "src.decomposition.pipeline.cli._resolve_provider", return_value=MagicMock()
    ), patch(
        "src.decomposition.pipeline.cli.run_decomposition", side_effect=fake_run
    ):
        result = runner.invoke(
            app, ["run", "--archive-root", str(archive), "--run-id", rid]
        )
    assert result.exit_code == 0, result.stdout
    assert seen.get("run_id") == UUID(rid)


def test_cli_resume_executes_successor_row(tmp_path: Path):
    """F-030 / ISS-0014: ``resume`` must execute the pipeline against the
    successor row — previously it only INSERTed the row and exited, leaving
    it 'running' forever with nothing driving it to a terminal status."""
    from uuid import UUID

    archive = tmp_path / "archive"
    archive.mkdir()
    paused_id = "00000000-0000-0000-0000-000000000001"
    successor_id = UUID("00000000-0000-0000-0000-000000000002")
    successor = {"run_id": successor_id, "archive_root": str(archive)}
    seen: dict = {}
    fake_session = MagicMock()

    async def fake_run(**kwargs):
        seen.update(kwargs)
        return {"run_id": str(successor_id), "status": "completed"}

    with patch(
        "src.decomposition.pipeline.cli._open_session", return_value=fake_session
    ), patch(
        "src.decomposition.pipeline.cli._resolve_provider", return_value=MagicMock()
    ), patch(
        "src.decomposition.pipeline.cli.run_repository.create_resume_run",
        return_value=successor,
    ), patch(
        "src.decomposition.pipeline.cli.run_decomposition", side_effect=fake_run
    ):
        result = runner.invoke(app, ["resume", "--run-id", paused_id])
    assert result.exit_code == 0, result.stdout
    # The successor row is adopted via run_id — no orphan 'running' row.
    assert seen.get("run_id") == successor_id
    assert str(seen.get("archive_root")) == str(archive)
    assert "completed" in result.stdout
