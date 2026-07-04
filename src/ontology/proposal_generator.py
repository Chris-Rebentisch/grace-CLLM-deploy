"""CLI-only proposal generator — signal→proposal pipeline (D387, Chunk 47).

D246-mirror: this module is the sole entry point for proposal generation.
Never import or invoke from FastAPI lifespan, APScheduler, or any
in-process scheduler. Sanctioned entry: ``python -m src.ontology.proposal_generator run``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import structlog
import yaml
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from src.analytics.signal_pipeline.base import SignalTypeLiteral
from src.ontology.database import (
    SchemaProposalRow,
    create_proposal,
    get_active_version,
    update_proposal_status,
)
from src.ontology.evidence_bundle import (
    EvidenceBundle,
    affected_types_from_parsed_change,
    generate_evidence_summary,
)
from src.ontology.kgcl_parser import parse_kgcl
from src.ontology.models import (
    ProposalPriority,
    ProposalStatus,
    ProposalType,
    SchemaProposal,
    classify_tier,
)
from src.ontology.signal_mapping import SIGNAL_LITERAL_TO_ENUM, map_signal_to_proposals
from src.shared.database import get_session_factory

logger = structlog.get_logger()

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "proposal_generator.yaml"

# Matches proposal_routes sentinel — CLI/server proposal lifecycle in one session bucket.
_PROPOSAL_TELEMETRY_SESSION_ID = UUID("00000000-0000-0000-0000-000000000047")

# Tier→ProposalPriority bridge (generator-local per spec §6 Step 3).
TIER_TO_PRIORITY: dict[int, ProposalPriority] = {
    1: ProposalPriority.LOW,
    2: ProposalPriority.MEDIUM,
    3: ProposalPriority.HIGH,
}


def _load_config() -> dict:
    """Load operator knobs from config/proposal_generator.yaml."""
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


def _compute_proposed_diff(active_schema_json: dict, kgcl_command: str) -> tuple[dict, object | None]:
    """Parse the KGCL command and compute the diff it would apply.

    Returns ``(proposed_diff, parsed_change)``; on any failure returns
    ``({}, None)`` and logs (graceful degradation — a proposal with an
    empty diff is still reviewable, just less self-contained).

    Capture-the-why (F-0040 / ISS-0053, validation run 2026-07-03):
    generator-authored rows persisted ``proposed_diff = {}`` — reviewers
    had to mentally simulate the KGCL command. The row must be
    self-contained, and the c47a append-only trigger makes ``proposed_diff``
    IMMUTABLE post-INSERT, so creation time is the ONLY time it can be
    persisted (preview-time backfill would be rejected by the trigger).
    """
    try:
        parsed = parse_kgcl(kgcl_command)
    except Exception as exc:  # noqa: BLE001
        logger.warning("proposal.diff_parse_failed", kgcl=kgcl_command, error=str(exc))
        return ({}, None)
    try:
        # Import inside the function: change_executor is owned by the
        # executor pipeline; we only borrow its pure schema-mutation helper
        # (same pattern as src/api/proposal_routes.py create route, D462).
        from src.ontology.change_executor import _apply_change_to_schema
        from src.ontology.diff_engine import compute_om4ov_diff

        new_schema = _apply_change_to_schema(active_schema_json, parsed)
        return (compute_om4ov_diff(active_schema_json, new_schema), parsed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("proposal.diff_compute_failed", kgcl=kgcl_command, error=str(exc))
        return ({}, parsed)


def _compute_dedup_hash(kgcl_command: str, ontology_module: str) -> str:
    """SHA-256 of kgcl_command + ontology_module."""
    payload = f"{kgcl_command}|{ontology_module}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _get_recurrence_count(
    db: Session,
    signal_type: str,
    ontology_module: str,
) -> int:
    """Count distinct signal runs for this signal_type + ontology_module."""
    result = db.execute(
        text(
            "SELECT count(DISTINCT run_id) FROM analytics_signals "
            "WHERE signal_type = :st AND ontology_module = :om"
        ),
        {"st": signal_type, "om": ontology_module},
    )
    return result.scalar() or 0


def _get_queue_depth(db: Session, tier: int) -> int:
    """Count pending proposals for a given tier."""
    result = db.execute(
        text(
            "SELECT count(*) FROM schema_proposals "
            "WHERE status = 'pending' AND change_tier = :tier"
        ),
        {"tier": tier},
    )
    return result.scalar() or 0


def _check_dedup_phase1(
    db: Session,
    dedup_hash: str,
    window_days: int,
) -> bool:
    """Return True if a duplicate exists within the dedup window (skip)."""
    cutoff = datetime.now(UTC) - timedelta(days=window_days)
    result = db.execute(
        text(
            "SELECT count(*) FROM schema_proposals "
            "WHERE dedup_hash = :hash AND generated_at > :cutoff"
        ),
        {"hash": dedup_hash, "cutoff": cutoff},
    )
    return (result.scalar() or 0) > 0


def _supersede_phase2(
    db: Session,
    dedup_hash: str,
    window_days: int,
) -> None:
    """Phase 2: supersede older pending proposals with same hash outside window."""
    cutoff = datetime.now(UTC) - timedelta(days=window_days)
    rows = (
        db.query(SchemaProposalRow)
        .filter(
            SchemaProposalRow.dedup_hash == dedup_hash,
            SchemaProposalRow.status == "pending",
            SchemaProposalRow.generated_at <= cutoff,
        )
        .all()
    )
    for row in rows:
        update_proposal_status(db, row.id, ProposalStatus.SUPERSEDED)
        logger.info("proposal.superseded", proposal_id=str(row.id), dedup_hash=dedup_hash)


async def _run(args: argparse.Namespace) -> None:
    """Core generator logic (async for LLM calls)."""
    config = _load_config()
    dedup_window_days = config.get("dedup_window_days", 7)
    queue_soft_cap = config.get("queue_depth_soft_cap", 50)
    signal_strength_threshold = config.get("signal_strength_threshold", 0.3)
    recurrence_denominator = config.get("recurrence_denominator", 3)

    signal_filter = set(args.signal.split(",")) if args.signal else None
    module_filter = args.ontology_module
    limit = args.limit
    dry_run = args.dry_run

    session_factory = get_session_factory()
    db = session_factory()

    try:
        # Check for active ontology version.
        active_version = get_active_version(db)
        if active_version is None:
            logger.warning("proposal_generator.no_active_version", msg="Skipping — no active ontology version")
            return

        # Read latest signals per (signal_type, ontology_module).
        query = text(
            "SELECT DISTINCT ON (signal_type, ontology_module) "
            "id, run_id, signal_type, ontology_module, strength, "
            "evidence_snapshot, detected_at "
            "FROM analytics_signals "
            "ORDER BY signal_type, ontology_module, detected_at DESC"
        )
        signal_rows = db.execute(query).fetchall()

        generated_count = 0
        for row in signal_rows:
            sig_id, run_id, signal_type, ontology_module, strength, evidence_snapshot, detected_at = row

            # Apply filters.
            if signal_filter and signal_type not in signal_filter:
                continue
            if module_filter and ontology_module != module_filter:
                continue
            if strength < signal_strength_threshold:
                continue

            # Map signal to proposals.
            proposals = map_signal_to_proposals(signal_type, evidence_snapshot)
            if not proposals:
                continue

            for proposal_type, kgcl_command in proposals:
                if limit and generated_count >= limit:
                    break

                tier = classify_tier(proposal_type)
                priority = TIER_TO_PRIORITY[tier]

                # Compute recurrence-weighted confidence.
                # F-0042 / ISS-0053 deferral closure: this generator path is
                # always signal-backed (source_signal_ids=[sig_id]), so
                # raw_confidence here is REAL agent confidence derived from
                # signal strength — never fabricated. Signal-less /
                # human-initiated proposals (create route) store None instead
                # (D120/D217; migration r4a_raw_confidence_nullable).
                run_count = _get_recurrence_count(db, signal_type, ontology_module)
                raw_confidence = strength * min(run_count / recurrence_denominator, 1.0)

                # F-0040 / ISS-0053: parse the KGCL command once — it yields
                # both the real proposed_diff (rows are self-contained; the
                # c47a trigger makes proposed_diff immutable post-INSERT) and
                # the affected-type fallback below.
                proposed_diff, parsed_change = _compute_proposed_diff(
                    active_version.schema_json, kgcl_command
                )

                affected_types = evidence_snapshot.get("affected_entity_types", [])
                if not affected_types and parsed_change is not None:
                    # F-0040 / ISS-0053: the target is right there in the KGCL
                    # string — never persist empty affected_entity_types when
                    # the parse result carries it.
                    affected_types = affected_types_from_parsed_change(parsed_change)

                # Build typed EvidenceBundle.
                bundle = EvidenceBundle(
                    source_signal_ids=[sig_id],
                    signal_type=signal_type,
                    signal_strength=strength,
                    affected_entity_types=affected_types,
                    ontology_module=ontology_module,
                    example_documents=evidence_snapshot.get("example_documents", []),
                    example_text_snippets=evidence_snapshot.get("example_text_snippets", [])[:3],
                    extraction_failure_count=evidence_snapshot.get("extraction_failure_count"),
                    co_occurrence_count=evidence_snapshot.get("co_occurrence_count"),
                    cq_text=evidence_snapshot.get("cq_text"),
                )

                # Generate NL summary (async, graceful degradation).
                # F-0040 / ISS-0053: pass the signal legend/target context —
                # without it the model refused and the refusal was stored.
                bundle.evidence_summary_nl = await generate_evidence_summary(
                    bundle,
                    kgcl_command=kgcl_command,
                    proposal_type=proposal_type.value,
                )

                dedup_hash = _compute_dedup_hash(kgcl_command, ontology_module)

                # Phase 1 dedup: skip if identical hash within window.
                if _check_dedup_phase1(db, dedup_hash, dedup_window_days):
                    logger.debug("proposal.dedup_skip", dedup_hash=dedup_hash)
                    continue

                # Phase 2 supersession: supersede older pending with same hash.
                _supersede_phase2(db, dedup_hash, dedup_window_days)

                # Check overflow.
                queue_depth = _get_queue_depth(db, tier)
                is_overflow = queue_depth >= queue_soft_cap

                # Map SignalTypeLiteral to SignalType enum.
                signal_type_enum = SIGNAL_LITERAL_TO_ENUM.get(signal_type)

                now = datetime.now(UTC)
                proposal = SchemaProposal(
                    id=uuid4(),
                    created_at=now,
                    proposal_type=proposal_type,
                    change_tier=tier,
                    kgcl_command=kgcl_command,
                    # F-0040 / ISS-0053: real diff, not {} (immutable post-INSERT per c47a).
                    proposed_diff=proposed_diff,
                    evidence=bundle,
                    signal_type=signal_type_enum,
                    raw_confidence=raw_confidence,
                    priority=priority,
                    status=ProposalStatus.PENDING,
                    current_schema_version_id=active_version.id,
                    ontology_module=ontology_module,
                    dedup_hash=dedup_hash,
                    overflow=is_overflow,
                    generated_at=now,
                )

                if dry_run:
                    logger.info(
                        "proposal.dry_run",
                        proposal_type=proposal_type.value,
                        kgcl=kgcl_command,
                        confidence=raw_confidence,
                        tier=tier,
                        overflow=is_overflow,
                    )
                else:
                    create_proposal(db, proposal)
                    # Best-effort OTel counter.
                    try:
                        from src.analytics.metrics import record_proposal_generated
                        record_proposal_generated(tier=str(tier), signal_type=signal_type)
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        from src.elicitation.event_writer import write_event
                        from src.elicitation.models import (
                            ElicitationEventEnvelope,
                            validate_payload_for_event_type,
                        )

                        validated = validate_payload_for_event_type(
                            "proposal_generated",
                            {
                                "proposal_id": str(proposal.id),
                                "signal_type": signal_type,
                                "change_tier": tier,
                                "ontology_module": ontology_module,
                            },
                        )
                        envelope = ElicitationEventEnvelope(
                            event_id=uuid4(),
                            event_type="proposal_generated",
                            session_id=_PROPOSAL_TELEMETRY_SESSION_ID,
                            actor_type="system",
                            phase_name="none",
                            emitted_at=now,
                            schema_version=1,
                            grace_version="0.1.0",
                            payload=validated.model_dump(mode="json"),
                            payload_schema_version=1,
                        )
                        write_event(db, envelope)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "proposal.generator.telemetry_failed",
                            error=str(exc),
                        )

                generated_count += 1

            if limit and generated_count >= limit:
                break

        logger.info("proposal_generator.complete", generated=generated_count, dry_run=dry_run)
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="proposal_generator",
        description="Signal→proposal pipeline (D387, Chunk 47). CLI-only (D246 mirror).",
    )
    sub = parser.add_subparsers(dest="command")
    run_parser = sub.add_parser("run", help="Generate proposals from analytics_signals")
    run_parser.add_argument("--dry-run", action="store_true", help="Generate without DB writes")
    run_parser.add_argument("--signal", type=str, default=None, help="Comma-separated signal filter (e.g. A,B,C)")
    run_parser.add_argument("--ontology-module", type=str, default=None, help="Filter by ontology module")
    run_parser.add_argument("--limit", type=int, default=None, help="Max proposals to generate")

    args = parser.parse_args()
    if args.command == "run":
        asyncio.run(_run(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
