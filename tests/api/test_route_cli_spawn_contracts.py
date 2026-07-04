"""D476: CLI argv contract test framework.

Validates that route-side argv construction round-trips cleanly through
the target CLI's argparser. Seven production pairs + synthetic self-tests
(pair 7, decomposition, parses through the typer/click command instead of
an argparse factory).

The ``assert_route_spawn_matches_argparser`` harness strips the
``sys.executable`` and ``-m module.name`` prefix to get the flag portion,
then passes it to the CLI argparser factory. Success = no ``SystemExit``.
"""

from __future__ import annotations

import sys
from argparse import ArgumentParser
from pathlib import Path
from uuid import uuid4

import pytest

from src.api.extraction_routes import _build_extraction_argv
from src.discovery.batch_runner import _build_argparser as batch_build_argparser
from src.extraction.eval_checkpoint import _build_argparser as eval_build_argparser
from src.extraction.image_pipeline import _build_argparser as image_pipeline_build_argparser


def assert_route_spawn_matches_argparser(
    argv: list[str],
    cli_argparser_factory,
) -> object:
    """Validate that *argv* round-trips through the CLI argparser.

    Strips the ``sys.executable`` and ``-m module.name`` prefix to get the
    flag portion, then calls ``cli_argparser_factory().parse_args(flags)``.
    Returns the parsed ``Namespace`` on success; raises ``SystemExit`` on
    mismatch (which the caller should catch).
    """
    # Strip sys.executable prefix
    flags = list(argv)
    if flags and flags[0] == sys.executable:
        flags = flags[1:]
    # Strip -m module.name
    if len(flags) >= 2 and flags[0] == "-m":
        flags = flags[2:]

    parser = cli_argparser_factory()
    return parser.parse_args(flags)


# ── Production pair 1: document-job → eval_checkpoint ─────────────────────


def test_document_job_matches_eval_checkpoint_argparser(tmp_path):
    """Document-job argv passes eval_checkpoint argparser without SystemExit.

    F-0008/F-0009 / ISS-0041: the document job now targets the requested
    file via ``--doc-file`` (not ``--doc-dir <parent> --sample-count 1``,
    which extracted the alphabetically-first file in the directory) and
    carries ``--persist`` so claims/events actually land in Postgres.
    """
    schema_path = tmp_path / "schema.json"
    schema_path.write_text("{}")
    source_file = tmp_path / "doc.txt"
    source_file.write_text("hello")

    argv = _build_extraction_argv(
        job_kind="document",
        job_id=uuid4(),
        schema_path=schema_path,
        source_path=source_file,
        provider="ollama",
        model="qwen2.5:7b",
    )
    ns = assert_route_spawn_matches_argparser(argv, eval_build_argparser)
    assert ns.schema == str(schema_path)
    assert ns.provider == "ollama"
    # F-0008 / ISS-0041: the requested file itself rides the argv.
    assert ns.doc_file == str(source_file)
    # F-0009 / ISS-0041: persistence flag rides the argv.
    assert ns.persist is True


def test_document_job_from_processed_doc_matches_eval_checkpoint_argparser(tmp_path):
    """F-0008 / ISS-0041 (binary-format follow-up): binary document argv with
    ``from_processed_doc=True`` carries ``--from-processed-doc`` and
    round-trips through the eval_checkpoint argparser (D476)."""
    schema_path = tmp_path / "schema.json"
    schema_path.write_text("{}")
    source_file = tmp_path / "claim.pdf"
    source_file.write_bytes(b"%PDF-1.4 fake")

    argv = _build_extraction_argv(
        job_kind="document",
        job_id=uuid4(),
        schema_path=schema_path,
        source_path=source_file,
        from_processed_doc=True,
    )
    ns = assert_route_spawn_matches_argparser(argv, eval_build_argparser)
    assert ns.doc_file == str(source_file)
    assert ns.from_processed_doc is True
    # The persisted document→claims contract (F-0009) still rides along.
    assert ns.persist is True


def test_document_job_txt_argv_omits_from_processed_doc(tmp_path):
    """ISS-0041 binary-format follow-up: default (.txt/.md) document argv is
    byte-identical to before — no --from-processed-doc."""
    schema_path = tmp_path / "schema.json"
    schema_path.write_text("{}")
    source_file = tmp_path / "doc.txt"
    source_file.write_text("hello")

    argv = _build_extraction_argv(
        job_kind="document",
        job_id=uuid4(),
        schema_path=schema_path,
        source_path=source_file,
    )
    assert "--from-processed-doc" not in argv
    ns = assert_route_spawn_matches_argparser(argv, eval_build_argparser)
    assert ns.from_processed_doc is False


def test_document_job_legacy_doc_dir_still_parses(tmp_path):
    """Legacy --doc-dir flag remains accepted by the argparser (ISS-0041:
    existing eval callers keep the directory-sampling behavior)."""
    argv = [
        sys.executable, "-m", "src.extraction.eval_checkpoint",
        "--schema", str(tmp_path / "schema.json"),
        "--doc-dir", str(tmp_path),
        "--sample-count", "1",
    ]
    ns = assert_route_spawn_matches_argparser(argv, eval_build_argparser)
    assert ns.doc_dir == str(tmp_path)
    assert ns.doc_file is None
    assert ns.persist is False


# ── Production pair 2: batch-job → batch_runner ───────────────────────────


def test_batch_job_matches_batch_runner_argparser(tmp_path):
    """Batch-job argv passes batch_runner argparser without SystemExit."""
    schema_path = tmp_path / "schema.json"
    schema_path.write_text("{}")

    argv = _build_extraction_argv(
        job_kind="batch",
        job_id=uuid4(),
        schema_path=schema_path,
        source_path=tmp_path,
    )
    ns = assert_route_spawn_matches_argparser(argv, batch_build_argparser)
    assert ns.source_dir == str(tmp_path)


# ── Production pair 3: batch-job with --router-strategy ───────────────────


def test_batch_job_with_router_strategy_matches_argparser(tmp_path):
    """Batch-job with --router-strategy passes batch_runner argparser."""
    schema_path = tmp_path / "schema.json"
    schema_path.write_text("{}")

    argv = _build_extraction_argv(
        job_kind="batch",
        job_id=uuid4(),
        schema_path=schema_path,
        source_path=tmp_path,
        router_strategy="sensitivity",
    )
    ns = assert_route_spawn_matches_argparser(argv, batch_build_argparser)
    assert ns.router_strategy == "sensitivity"


# ── Production pair 4: batch-job with --job-id ────────────────────────────


def test_batch_job_with_job_id_matches_argparser(tmp_path):
    """Batch-job with --job-id passes batch_runner argparser."""
    schema_path = tmp_path / "schema.json"
    schema_path.write_text("{}")
    job_id = uuid4()

    argv = _build_extraction_argv(
        job_kind="batch",
        job_id=job_id,
        schema_path=schema_path,
        source_path=tmp_path,
    )
    ns = assert_route_spawn_matches_argparser(argv, batch_build_argparser)
    assert ns.job_id == str(job_id)


# ── Production pair 5: image-job → image_pipeline ───────────────────────────


def test_image_job_matches_image_pipeline_argparser(tmp_path):
    """Image-job argv passes image_pipeline argparser (D476, Chunk 77b)."""
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"\xff\xd8\xff\xd9")

    argv = _build_extraction_argv(
        job_kind="image",
        job_id=uuid4(),
        schema_path=tmp_path / "unused.json",
        source_path=image_path,
    )
    ns = assert_route_spawn_matches_argparser(argv, image_pipeline_build_argparser)
    assert ns.source_path == str(image_path)


# ── Production pair 6: drift-detection → drift_detector ───────────────────


def test_drift_spawn_matches_drift_detector_argparser():
    """Drift-detection route argv passes drift_detector argparser."""
    from src.permissions.drift_detector import _parse_args

    # Simulate the argv_tail that the drift route constructs
    # (after _spawn_permissions_cli strips "drift" prefix and picks
    # src.permissions.drift_detector as the module).
    job_id = uuid4()
    argv_tail = [
        "run",
        "--observation-time", "2026-05-27T12:00:00+00:00",
        "--job-id", str(job_id),
    ]
    # _parse_args calls parser.parse_args(list(argv)) directly
    ns = _parse_args(argv_tail)
    assert ns.command == "run"
    assert ns.job_id == str(job_id)


# ── Production pair 7: decomposition trigger → pipeline CLI (typer) ───────


def _decomposition_run_click_command():
    """Resolve the typer ``run`` subcommand as a click command for parsing."""
    import typer as _typer

    from src.decomposition.pipeline.cli import app as decomposition_app

    group = _typer.main.get_command(decomposition_app)
    return group.commands["run"]


def test_decomposition_trigger_matches_pipeline_cli(tmp_path):
    """F-030 / ISS-0014 + D476: trigger argv round-trips through the CLI.

    The decomposition pipeline CLI is typer-based (no ``_build_argparser``
    factory), so this pair parses the flag portion through the resolved
    click command instead of the argparse harness. Critically asserts that
    ``--run-id`` carries the placeholder row id (D460 API-INSERT-first
    pattern) so the polled run_id is the executing run.
    """
    from src.api.decomposition_routes import _build_decomposition_argv

    rid = uuid4()
    argv = _build_decomposition_argv(str(tmp_path), run_id=rid, limit=3)

    # Same prefix-strip convention as assert_route_spawn_matches_argparser.
    assert argv[0] == sys.executable
    assert argv[1:3] == ["-m", "src.decomposition.pipeline"]
    flags = argv[3:]
    assert flags[0] == "run"

    run_command = _decomposition_run_click_command()
    with run_command.make_context("run", list(flags[1:])) as ctx:
        params = ctx.params
    assert params["run_id"] == str(rid)
    assert str(params["archive_root"]) == str(tmp_path)
    assert params["limit"] == 3


def test_decomposition_rerun_argv_matches_pipeline_cli(tmp_path):
    """ISS-0024 + D476: rerun-spawn argv round-trips through the CLI.

    The rerun route spawns the successor row's execution with
    ``--rerun-direction`` (the ±1.5x resolution intent is not persisted
    on the append-only row — the argv is its transport).
    """
    from src.api.decomposition_routes import _build_decomposition_argv

    rid = uuid4()
    argv = _build_decomposition_argv(
        str(tmp_path), run_id=rid, limit=None, rerun_direction="finer"
    )
    flags = argv[3:]
    assert flags[0] == "run"
    assert "--rerun-direction" in flags and "finer" in flags

    run_command = _decomposition_run_click_command()
    with run_command.make_context("run", list(flags[1:])) as ctx:
        params = ctx.params
    assert params["run_id"] == str(rid)
    assert params["rerun_direction"] == "finer"


def test_decomposition_bogus_flag_rejected(tmp_path):
    """Self-test for pair 7: an unknown flag fails click parsing."""
    import click

    run_command = _decomposition_run_click_command()
    with pytest.raises((click.UsageError, SystemExit)):
        run_command.make_context(
            "run", ["--archive-root", str(tmp_path), "--bogus"]
        )


# ── Synthetic self-test: bogus flag causes SystemExit ─────────────────────


def test_bogus_flag_caught_by_framework(tmp_path):
    """Synthetic self-test: --bogus flag triggers SystemExit from argparser."""
    bogus_argv = [sys.executable, "-m", "src.extraction.eval_checkpoint", "--bogus"]
    with pytest.raises(SystemExit):
        assert_route_spawn_matches_argparser(bogus_argv, eval_build_argparser)
