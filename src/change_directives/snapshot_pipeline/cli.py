"""CLI for snapshot pipeline (Chunk 39, D301)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID

import typer

from src.change_directives.snapshot_pipeline.config import load_snapshot_config
from src.change_directives.snapshot_pipeline.orchestrator import run_snapshots
from src.graph.arcade_client import get_arcade_client
from src.shared.database import get_session_factory
app = typer.Typer(add_completion=False)


def _parse_observation_time(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    ts = raw.strip()
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


@app.command("run-all")
def run_all(
    directive_id: str | None = typer.Option(
        None, "--directive-id", help="Only process this directive UUID."
    ),
    dry_run: bool = typer.Option(False, "--dry-run"),
    observation_time: str | None = typer.Option(
        None,
        "--observation-time",
        help="ISO8601 snapshot timestamp (default: now UTC).",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        help="Process at most N directives (stable id order).",
    ),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Execute realization snapshots for active directives."""
    _ = verbose

    cfg = load_snapshot_config()
    obs = _parse_observation_time(observation_time)
    did = UUID(directive_id) if directive_id else None

    arcade = get_arcade_client()
    db = get_session_factory()()
    try:
        asyncio.run(
            run_snapshots(
                db,
                arcade,
                cfg,
                obs,
                directive_id=did,
                dry_run=dry_run,
                limit=limit,
            )
        )
    finally:
        db.close()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
