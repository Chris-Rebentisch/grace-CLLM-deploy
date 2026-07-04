"""Adaptive Evolution Agent Daemon (Chunk 50, D398 — ninth D246-mirror pipeline).

CLI-only.  Never imported from FastAPI lifespan, APScheduler, or any route
module.  Invoked by launchd on a fixed interval (default 300s).

Public API: ``python -m src.ontology.agent_daemon run [--dry-run] [--tick-interval 300]``

Invariant: D246 — agent daemon runs out-of-process only.
Invariant: D401 — Tier 3 hard ceiling: daemon NEVER evaluates Tier 3 proposals.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import structlog
import yaml
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.ontology.kgcl_inverter import invert as kgcl_invert
from src.ontology.models import ProposalStatus

logger = structlog.get_logger(__name__)

# Default config values.
_DEFAULT_TICK_INTERVAL = 300
_DEFAULT_COOLING_HOURS = 48
_DEFAULT_PID_PATH = "~/.grace/agent-daemon.pid"

UTC = timezone.utc


def _load_config() -> dict:
    """Load daemon config from config/agent_daemon.yaml."""
    config_path = Path(__file__).resolve().parents[2] / "config" / "agent_daemon.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    return {}


def _make_agent_id() -> str:
    return f"agent-daemon-{socket.gethostname()}-{os.getpid()}"


# ---------------------------------------------------------------------------
# PID-file guard
# ---------------------------------------------------------------------------

def _acquire_pid(pid_path: str) -> bool:
    """Write PID file.  Returns False if another daemon is alive."""
    expanded = os.path.expanduser(pid_path)
    os.makedirs(os.path.dirname(expanded), exist_ok=True)
    if os.path.exists(expanded):
        try:
            with open(expanded) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)  # Check if alive
            logger.warning("daemon_already_running", pid=old_pid)
            return False
        except (OSError, ValueError):
            pass  # Stale PID
    with open(expanded, "w") as f:
        f.write(str(os.getpid()))
    return True


def _release_pid(pid_path: str) -> None:
    expanded = os.path.expanduser(pid_path)
    try:
        os.remove(expanded)
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Governance event dual-write
# ---------------------------------------------------------------------------

def _record_governance_event(
    db: Session,
    *,
    decision_type: str,
    agent_id: str,
    proposal_id: UUID | None = None,
    schema_version_id: UUID | None = None,
    tier: int | None = None,
    trust_score_at_time: float | None = None,
    outcome: str | None = None,
    reason: str | None = None,
) -> None:
    """Dual-write GovernanceDecision_Event: Postgres INSERT first, ArcadeDB second.

    PG is authoritative on ArcadeDB failure (log-and-continue).
    """
    from src.ontology.database import GovernanceDecisionEventRow

    event_id = uuid4()
    now = datetime.now(UTC)

    # 1. Postgres INSERT
    row = GovernanceDecisionEventRow(
        id=event_id,
        decision_type=decision_type,
        agent_id=agent_id,
        proposal_id=proposal_id,
        schema_version_id=schema_version_id,
        tier=tier,
        trust_score_at_time=trust_score_at_time,
        outcome=outcome,
        reason=reason,
        recorded_at=now,
    )
    db.add(row)
    db.flush()

    # 2. ArcadeDB vertex (best-effort)
    try:
        from src.graph.arcade_client import ArcadeClient
        client = ArcadeClient()
        props = {
            "grace_id": str(event_id),
            "decision_type": decision_type,
            "agent_id": agent_id,
            "proposal_id": str(proposal_id) if proposal_id else None,
            "schema_version_id": str(schema_version_id) if schema_version_id else None,
            "tier": tier,
            "trust_score_at_time": trust_score_at_time,
            "outcome": outcome,
            "reason": reason,
            "recorded_at": now.isoformat(),
        }
        # Filter None values
        props = {k: v for k, v in props.items() if v is not None}
        # F-026 / ISS-0011: parameterized OpenCypher. The previous code built a
        # literal-interpolated statement (reason/decision text carries KGCL
        # commands whose embedded single quotes broke it) and called the
        # nonexistent ``client.command(...)`` — the ArcadeClient API is the
        # async ``execute_cypher(query, params=...)``. Both defects made every
        # ArcadeDB-side governance audit record silently vanish. $params are
        # sent out-of-band, so quoted payloads can never break the statement.
        prop_str = ", ".join(f"{k}: ${k}" for k in props)
        query = f"CREATE (n:GovernanceDecision_Event {{{prop_str}}}) RETURN n.grace_id"
        asyncio.run(client.execute_cypher(query, params=props))
    except Exception as exc:
        # F-026 / ISS-0011: include the error detail — the previous bare
        # warning hid the AttributeError root cause.
        logger.warning(
            "arcadedb_governance_event_write_failed",
            event_id=str(event_id),
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Tick logic
# ---------------------------------------------------------------------------

def run_tick(
    db: Session,
    *,
    agent_id: str,
    cooling_period_hours: int = _DEFAULT_COOLING_HOURS,
    dry_run: bool = False,
    observation_time: datetime | None = None,
) -> dict:
    """Execute one daemon tick.

    Returns a summary dict for telemetry and logging.
    """
    from src.ontology.database import SchemaProposalRow, TrustScoreRow

    now = observation_time or datetime.now(UTC)
    summary = {
        "proposals_evaluated": 0,
        "proposals_applied": 0,
        "suspended_tiers": [],
        "cooling_finalized": 0,
    }

    # Step 1: Read trust_scores for all tiers.
    tier_rows = db.execute(
        select(TrustScoreRow).order_by(TrustScoreRow.tier)
    ).scalars().all()

    # Check if any tier has autonomy enabled.
    if not any(r.autonomy_enabled for r in tier_rows):
        logger.info("daemon_tick_skipped", reason="autonomy_disabled_all_tiers")
        return summary

    # Step 2+3: Per-tier evaluation (Tiers 1 and 2 ONLY — D401 Tier 3 hard ceiling).
    for tier_num in (1, 2):
        tier_row = next((r for r in tier_rows if r.tier == tier_num), None)
        if tier_row is None:
            continue
        if not tier_row.autonomy_enabled:
            continue
        if tier_row.regression_detected:
            summary["suspended_tiers"].append(tier_num)
            logger.info("daemon_tier_suspended", tier=tier_num, reason="regression_detected")
            continue

        # Step 4: Query pending proposals for this tier where trust >= threshold.
        pending = db.execute(
            select(SchemaProposalRow)
            .where(SchemaProposalRow.status == "pending")
            .where(SchemaProposalRow.change_tier == tier_num)
            .with_for_update(skip_locked=True)
        ).scalars().all()

        for proposal_row in pending:
            if tier_row.trust_score < tier_row.autonomy_threshold:
                continue

            summary["proposals_evaluated"] += 1

            # Step 5: Filter revertible-only via kgcl_inverter.
            if kgcl_invert(proposal_row.kgcl_command) is None:
                logger.info(
                    "daemon_proposal_skipped",
                    proposal_id=str(proposal_row.id),
                    reason="non_revertible",
                )
                continue

            if dry_run:
                logger.info(
                    "daemon_dry_run_would_apply",
                    proposal_id=str(proposal_row.id),
                    kgcl=proposal_row.kgcl_command,
                    tier=tier_num,
                )
                continue

            # Step 6: Transition pending -> approved, then apply.
            try:
                proposal_row.status = "approved"
                proposal_row.reviewer = "system:autonomy"
                proposal_row.reviewed_at = now
                proposal_row.applied_autonomously = True
                proposal_row.trust_score_at_time = tier_row.trust_score
                db.flush()

                from src.ontology.change_executor import apply_proposal
                result = asyncio.run(apply_proposal(db, proposal_row.id))

                if not result.success:
                    logger.error(
                        "daemon_apply_failed",
                        proposal_id=str(proposal_row.id),
                        error=result.error,
                    )
                    continue

                # Transition APPLIED -> COOLING with 48h expiry.
                proposal_row.status = "cooling"
                proposal_row.cooling_period_expires_at = now + timedelta(hours=cooling_period_hours)
                db.flush()

                _record_governance_event(
                    db,
                    decision_type="cooling_initiated",
                    agent_id=agent_id,
                    proposal_id=proposal_row.id,
                    schema_version_id=result.version_id,
                    tier=tier_num,
                    trust_score_at_time=tier_row.trust_score,
                    outcome="cooling_entered",
                )

                summary["proposals_applied"] += 1
                logger.info(
                    "daemon_proposal_applied",
                    proposal_id=str(proposal_row.id),
                    tier=tier_num,
                    cooling_expires=proposal_row.cooling_period_expires_at.isoformat(),
                )

            except Exception:
                logger.exception(
                    "daemon_proposal_error",
                    proposal_id=str(proposal_row.id),
                )
                db.rollback()

    # Step 7: Check cooling-expired proposals (auto-finalize).
    if not dry_run:
        cooling_expired = db.execute(
            select(SchemaProposalRow)
            .where(SchemaProposalRow.status == "cooling")
            .where(SchemaProposalRow.cooling_period_expires_at < now)
            .with_for_update(skip_locked=True)
        ).scalars().all()

        for row in cooling_expired:
            row.status = "applied"
            row.cooling_outcome = "auto_finalized"
            db.flush()

            _record_governance_event(
                db,
                decision_type="cooling_auto_finalized",
                agent_id=agent_id,
                proposal_id=row.id,
                tier=row.change_tier,
                outcome="auto_finalized",
            )
            summary["cooling_finalized"] += 1
            logger.info(
                "daemon_cooling_auto_finalized",
                proposal_id=str(row.id),
            )

    db.commit()
    return summary


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    # F-0049/ISS-0040: mirror this D246 subprocess's OTel metrics into the
    # prometheus multiproc dir (no-op when PROMETHEUS_MULTIPROC_DIR is unset).
    from src.analytics.subprocess_metrics import init_subprocess_metrics

    init_subprocess_metrics()

    parser = argparse.ArgumentParser(
        prog="python -m src.ontology.agent_daemon",
        description="Adaptive Evolution Agent Daemon (D398, D246 mirror).",
    )
    sub = parser.add_subparsers(dest="subcommand")

    run_parser = sub.add_parser("run", help="Execute one tick (or loop with --tick-interval).")
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument("--tick-interval", type=int, default=None,
                            help="Seconds between ticks (0 = single tick). Default from config.")
    run_parser.add_argument("--observation-time", type=str, default=None,
                            help="ISO8601 observation time (testing).")

    args = parser.parse_args(argv)

    if args.subcommand != "run":
        parser.print_help()
        sys.exit(1)

    config = _load_config()
    tick_interval = args.tick_interval or config.get("tick_interval_seconds", _DEFAULT_TICK_INTERVAL)
    cooling_hours = config.get("cooling_period_hours", _DEFAULT_COOLING_HOURS)
    pid_path = config.get("pid_file_path", _DEFAULT_PID_PATH)

    observation_time = None
    if args.observation_time:
        observation_time = datetime.fromisoformat(args.observation_time)

    if not _acquire_pid(pid_path):
        logger.error("daemon_pid_conflict")
        sys.exit(1)

    agent_id = _make_agent_id()
    logger.info("daemon_starting", agent_id=agent_id, tick_interval=tick_interval)

    try:
        from src.shared.database import get_session_factory
        session_factory = get_session_factory()

        # Single tick for launchd invocation pattern.
        db = session_factory()
        try:
            summary = run_tick(
                db,
                agent_id=agent_id,
                cooling_period_hours=cooling_hours,
                dry_run=args.dry_run,
                observation_time=observation_time,
            )
            logger.info("daemon_tick_complete", **summary)
        finally:
            db.close()

    finally:
        _release_pid(pid_path)


if __name__ == "__main__":
    main()
