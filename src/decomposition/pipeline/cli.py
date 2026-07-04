"""Typer CLI for the decomposition pipeline (Chunk 40, D315).

Subcommands:

* ``run`` — execute a fresh pipeline against ``--archive-root``.
* ``status`` — show the latest (or specified) run's status JSONB.
* ``resume`` — Path B resume of a ``paused_pre_layer4`` run.

D246 mirror: this is the *only* sanctioned invocation surface. The
package never imports ``fastapi`` or ``apscheduler``.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

import typer

from src.decomposition import run_repository
from src.decomposition.config import load_config
from src.decomposition.pipeline.orchestrator import run_decomposition

app = typer.Typer(add_completion=False, help="GrACE decomposition pipeline")


def _flush_otel() -> None:
    try:  # pragma: no cover — best-effort flush
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            provider.force_flush()
    except Exception:  # noqa: BLE001
        pass


def _open_session():
    from src.shared.database import get_session_factory

    return get_session_factory()()


def _resolve_provider():
    from src.shared.llm_provider import get_provider

    return get_provider()


def _format_run(row: dict) -> str:
    safe = {k: (str(v) if hasattr(v, "isoformat") or isinstance(v, UUID) else v)
            for k, v in row.items()}
    return json.dumps(safe, indent=2, default=str)


@app.command("run")
def run_cmd(
    archive_root: Path = typer.Option(
        ..., "--archive-root", exists=True, dir_okay=True, file_okay=False
    ),
    run_id: str | None = typer.Option(None, "--run-id"),
    limit: int | None = typer.Option(None, "--limit"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    verbose: bool = typer.Option(False, "--verbose"),
    rerun_direction: str | None = typer.Option(
        None,
        "--rerun-direction",
        help=(
            "ISS-0024: 'finer' or 'coarser' — apply the documented ±1.5x "
            "Layer-3 Leiden resolution scaling for a rerun successor row. "
            "Direction is not persisted on the append-only run row, so the "
            "spawn argv is its transport."
        ),
    ),
) -> None:
    """Execute Layers 1–4 over ``--archive-root``."""
    _ = verbose
    if rerun_direction is not None and rerun_direction not in ("finer", "coarser"):
        raise typer.BadParameter(
            "rerun-direction must be 'finer' or 'coarser'"
        )
    cfg = load_config()
    rid = UUID(run_id) if run_id else None
    session = _open_session()
    try:
        provider = _resolve_provider()
        result = asyncio.run(
            run_decomposition(
                archive_root=archive_root,
                config=cfg,
                db_session=session,
                llm_provider=provider,
                dry_run=dry_run,
                limit=limit,
                run_id=rid,
                rerun_direction=rerun_direction,
            )
        )
        typer.echo(_format_run(result))
    finally:
        session.close()
        _flush_otel()


@app.command("status")
def status_cmd(
    run_id: str | None = typer.Option(None, "--run-id"),
) -> None:
    """Print the latest (or specified) run's row as JSON."""
    session = _open_session()
    try:
        if run_id is not None:
            row = run_repository.get_run(session, UUID(run_id))
        else:
            from sqlalchemy import text as _text

            r = session.execute(
                _text(
                    "SELECT run_id FROM decomposition_runs "
                    "ORDER BY started_at DESC LIMIT 1"
                )
            ).one_or_none()
            row = (
                run_repository.get_run(session, r._mapping["run_id"])
                if r is not None
                else None
            )
        if row is None:
            typer.echo("No runs found.")
            raise typer.Exit(code=1)
        typer.echo(_format_run(row))
    finally:
        session.close()
        _flush_otel()


@app.command("resume")
def resume_cmd(
    run_id: str = typer.Option(..., "--run-id"),
) -> None:
    """Path B resume — INSERT a successor row and execute it."""
    session = _open_session()
    try:
        successor = run_repository.create_resume_run(session, UUID(run_id))
        session.commit()
        # F-030 / ISS-0014: previously this command only INSERTed the
        # successor row and exited — the row stayed 'running' forever with
        # nothing executing it. Run the pipeline against the successor via
        # run_id adoption; the orchestrator reuses the seeded Layer 1–3
        # artifacts and resumes at Layer 4.
        cfg = load_config()
        provider = _resolve_provider()
        result = asyncio.run(
            run_decomposition(
                archive_root=Path(successor["archive_root"]),
                config=cfg,
                db_session=session,
                llm_provider=provider,
                run_id=successor["run_id"],
            )
        )
        typer.echo(_format_run(result))
    finally:
        session.close()
        _flush_otel()


def main() -> None:  # pragma: no cover
    # F-0049/ISS-0040: mirror this D246 subprocess's OTel metrics into the
    # prometheus multiproc dir (no-op when PROMETHEUS_MULTIPROC_DIR is unset).
    from src.analytics.subprocess_metrics import init_subprocess_metrics

    init_subprocess_metrics()

    app()


if __name__ == "__main__":  # pragma: no cover
    main()
