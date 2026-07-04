"""RFC 5322 thread reconstruction for communication events (Chunk 80a, D513, D246 mirror).

CLI-only module. Builds References-chain DAGs, assigns thread positions via
topological sort, and handles orphans with a subject+participants proxy fallback.

D356 capture-the-why: D513 — RFC 5322 thread reconstruction; D246 mirror.
This module MUST NOT import ``fastapi`` or ``apscheduler``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.shared.database import get_engine

logger = structlog.get_logger()


def _build_argparser() -> argparse.ArgumentParser:
    """Build the CLI argument parser for the thread reconstructor."""
    parser = argparse.ArgumentParser(
        prog="python -m src.ingestion.communications.thread_reconstructor",
        description=(
            "GrACE RFC 5322 Thread Reconstructor (D513). Rebuilds thread_id "
            "and thread_position from References/In-Reply-To headers."
        ),
    )
    sub = parser.add_subparsers(dest="command")
    run_parser = sub.add_parser("run", help="Run thread reconstruction.")
    run_parser.add_argument(
        "--source-id",
        type=str,
        default=None,
        help="Filter to a specific ingestion source UUID.",
    )
    run_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max events to process.",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print actions without writing to DB.",
    )
    run_parser.add_argument(
        "--reprocess",
        action="store_true",
        default=False,
        help="Reprocess events that already have thread_id assigned.",
    )
    # F-0032(a) / ISS-0036: re-drive path — re-apply supersession over
    # ALREADY-threaded events (documented pipeline order leaves the graph
    # empty during `run`, and re-running `run` no-ops on threaded events).
    supersede_parser = sub.add_parser(
        "supersede",
        help="Re-apply supersession over already-threaded events (F-0032 re-drive).",
    )
    supersede_parser.add_argument(
        "--source-id",
        type=str,
        default=None,
        help="Filter to a specific ingestion source UUID.",
    )
    supersede_parser.add_argument(
        "--thread-id",
        type=str,
        default=None,
        help="Filter to a single thread_id (root message-id).",
    )
    supersede_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max events to process.",
    )
    supersede_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Report thread/event counts without applying supersession.",
    )
    return parser


def _normalize_subject(subject: str | None) -> str:
    """Strip Re:/Fwd: prefixes and normalize whitespace for proxy matching."""
    if not subject:
        return ""
    import re
    cleaned = re.sub(r"^(Re|Fwd|Fw)\s*:\s*", "", subject, flags=re.IGNORECASE)
    return cleaned.strip().lower()


def _recipients_hash(recipients_json: list | None) -> str:
    """Deterministic hash of sorted recipient list for proxy grouping."""
    if not recipients_json:
        return ""
    sorted_recipients = sorted(
        r.lower() if isinstance(r, str) else json.dumps(r, sort_keys=True)
        for r in recipients_json
    )
    return hashlib.sha256("|".join(sorted_recipients).encode()).hexdigest()[:16]


def _date_bucket(sent_at) -> str:
    """Bucket date to YYYY-MM-DD for proxy grouping."""
    if sent_at is None:
        return "unknown"
    return str(sent_at.date()) if hasattr(sent_at, "date") else str(sent_at)[:10]


def _apply_supersession_for_threads(
    session: Session,
    thread_members: dict[str, list[dict]],
) -> dict:
    """Run final-message supersession for each reconstructed thread (D514 / CP3 step 5).

    D356 capture-the-why: D513/D514 — supersession is invoked in the same CLI
    invocation as thread reconstruction; lazy import preserves D246 (no fastapi).

    Returns an aggregate summary dict (threads_processed, vertex_superseded,
    claims_superseded) so the F-0032(a) re-drive path can report what it did.
    """
    summary = {"threads_processed": 0, "vertex_superseded": 0, "claims_superseded": 0}
    if not thread_members:
        return summary

    import asyncio

    from src.ingestion.communications.supersession import (
        apply_claim_level_supersession,
        apply_thread_supersession_async,
        fetch_thread_entities_from_arcade,
    )

    # F-0032(a) / ISS-0036 capture-the-why: arcade unavailability must NOT
    # abort the whole pass — the claim-level pass below is Postgres-only and
    # still applies. The old early `return` silently skipped everything.
    arcade_client = None
    try:
        from src.graph.arcade_client import get_arcade_client

        arcade_client = get_arcade_client()
    except Exception as exc:  # noqa: BLE001
        logger.warning("thread_reconstructor.arcade_unavailable", error=str(exc))

    # D541 round-2: run the WHOLE per-source supersession phase in ONE event loop
    # (one pooled arcade client, one aclose). The prior per-thread/per-write
    # asyncio.run + reset_pool juggling orphaned a live httpx client on every call
    # (reaped only at process exit). Here every load + write is awaited on the same
    # loop, and the client is closed once in finally.
    async def _supersede_all_threads() -> None:
        try:
            for thread_id, members in thread_members.items():
                try:
                    entities = await fetch_thread_entities_from_arcade(arcade_client, members)
                    if not entities:
                        continue
                    result = await apply_thread_supersession_async(
                        thread_id=thread_id,
                        thread_entities=entities,
                        arcade_client=arcade_client,
                        session=session,
                    )
                    summary["vertex_superseded"] += result.get("superseded_count", 0)
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "thread_reconstructor.supersession_failed",
                        thread_id=thread_id,
                        error=str(exc),
                    )
        finally:
            await arcade_client.aclose()

    if arcade_client is not None:
        asyncio.run(_supersede_all_threads())

    # F-0032(c) / ISS-0036: claim-level pass — covers contradictions whose
    # claims ER-merged into ONE vertex (no earlier/later vertex pair, so the
    # vertex pass above is structurally blind to them). Postgres-only; runs
    # regardless of arcade availability.
    for thread_id, members in thread_members.items():
        try:
            result = apply_claim_level_supersession(session, thread_id, members)
            summary["claims_superseded"] += result.get("claims_superseded", 0)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "thread_reconstructor.claim_supersession_failed",
                thread_id=thread_id,
                error=str(exc),
            )

    summary["threads_processed"] = len(thread_members)
    return summary


def resupersede_threads(
    session: Session,
    source_id: UUID | None = None,
    thread_id: str | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict:
    """Re-apply supersession over ALREADY-threaded communication events.

    F-0032(a) / ISS-0036 capture-the-why: in the documented pipeline order
    (thread reconstruction FIRST, then extraction bridge) the graph is EMPTY
    when the reconstructor's same-invocation supersession fires, and re-running
    `run` afterwards no-ops (`no_events_to_process` — events already carry
    thread_id). Supersession could therefore NEVER fire in the documented
    pipeline. This re-drive path groups existing events by their persisted
    thread_id (no new events required, no thread mutation) and replays the
    supersession pass per thread. D246 discipline: CLI-only, no fastapi import.
    """
    where_clauses = ["thread_id IS NOT NULL"]
    params: dict = {}
    if source_id:
        where_clauses.append("source_id = :source_id")
        params["source_id"] = source_id
    if thread_id:
        where_clauses.append("thread_id = :thread_id")
        params["thread_id"] = thread_id
    limit_sql = f"LIMIT {limit}" if limit else ""

    query = text(
        f"SELECT id, message_id, thread_id, thread_position, sent_at "
        f"FROM communication_events "
        f"WHERE {' AND '.join(where_clauses)} "
        f"ORDER BY thread_id, thread_position ASC NULLS LAST "
        f"{limit_sql}"
    )
    rows = session.execute(query, params).fetchall()

    if not rows:
        logger.info("thread_reconstructor.supersede_no_threaded_events")
        return {"thread_count": 0, "event_count": 0}

    thread_members: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        thread_members[row.thread_id].append({
            "message_id": row.message_id,
            "thread_position": row.thread_position or 0,
            "sent_at": row.sent_at,
        })

    if dry_run:
        logger.info(
            "thread_reconstructor.supersede_dry_run",
            thread_count=len(thread_members),
            event_count=len(rows),
        )
        return {"thread_count": len(thread_members), "event_count": len(rows)}

    summary = _apply_supersession_for_threads(session, thread_members)
    logger.info(
        "thread_reconstructor.supersede_complete",
        thread_count=len(thread_members),
        event_count=len(rows),
        **summary,
    )
    return {"thread_count": len(thread_members), "event_count": len(rows), **summary}


def reconstruct_threads(
    session: Session,
    source_id: UUID | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    reprocess: bool = False,
) -> dict:
    """Reconstruct email threads from RFC 5322 headers.

    Returns summary dict with thread_count and event_count.
    """
    # Query communication events with threading-relevant headers
    where_clauses = []
    params: dict = {}

    if not reprocess:
        where_clauses.append("(thread_position IS NULL)")

    if source_id:
        where_clauses.append("source_id = :source_id")
        params["source_id"] = source_id

    where_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"
    limit_sql = f"LIMIT {limit}" if limit else ""

    query = text(
        f"SELECT id, message_id, in_reply_to, references_json, "
        f"subject, recipients_json, sent_at, thread_id, thread_orphan "
        f"FROM communication_events "
        f"WHERE {where_sql} "
        f"ORDER BY sent_at ASC NULLS LAST "
        f"{limit_sql}"
    )

    rows = session.execute(query, params).fetchall()

    if not rows:
        logger.info("thread_reconstructor.no_events_to_process")
        return {"thread_count": 0, "event_count": 0}

    # Build lookup: message_id -> row
    msg_id_to_row: dict[str, object] = {}
    all_events = []
    for row in rows:
        msg_id_to_row[row.message_id] = row
        all_events.append(row)

    # Phase 1: DAG reconstruction via References + In-Reply-To
    # For each message, determine its root (thread_id) and build adjacency
    children: dict[str, list[str]] = defaultdict(list)  # parent_msg_id -> [child_msg_ids]
    parent_of: dict[str, str] = {}  # msg_id -> immediate parent msg_id
    roots: set[str] = set()  # root message IDs

    events_with_refs = []
    events_without_refs = []

    for row in all_events:
        refs = row.references_json or []
        in_reply_to = row.in_reply_to

        if not refs and not in_reply_to:
            events_without_refs.append(row)
            continue

        events_with_refs.append(row)

        # JWZ robustness rule: if In-Reply-To is not in References, append it
        if in_reply_to and in_reply_to not in refs:
            refs = list(refs) + [in_reply_to]

        if refs:
            # Root = first message-id in References chain
            root = refs[0]
            # Direct parent = In-Reply-To or last in References
            direct_parent = in_reply_to if in_reply_to else refs[-1]
            parent_of[row.message_id] = direct_parent
            children[direct_parent].append(row.message_id)
        else:
            root = row.message_id

        # Walk References chain to build parent-child links
        for i in range(len(refs) - 1):
            if refs[i + 1] not in parent_of:
                parent_of[refs[i + 1]] = refs[i]
                children[refs[i]].append(refs[i + 1])

    # Find roots (messages with no parent in our set)
    all_msg_ids = {row.message_id for row in all_events}
    for row in events_with_refs:
        # Walk up to find root
        current = row.message_id
        visited = set()
        while current in parent_of and current not in visited:
            visited.add(current)
            current = parent_of[current]
        roots.add(current)

    # Phase 2: Assign thread_id and thread_position via topological sort
    threads: dict[str, list] = defaultdict(list)  # root_msg_id -> [(position, event_row)]

    for row in events_with_refs:
        # Find root for this message
        current = row.message_id
        visited = set()
        while current in parent_of and current not in visited:
            visited.add(current)
            current = parent_of[current]
        root = current
        threads[root].append(row)

    # Also add root messages themselves if they're in our set
    for root in list(roots):
        if root in msg_id_to_row:
            root_row = msg_id_to_row[root]
            if root_row not in threads[root]:
                threads[root].insert(0, root_row)

    updates = []
    thread_members: dict[str, list[dict]] = defaultdict(list)

    for root_msg_id, thread_events in threads.items():
        # Sort by sent_at within thread
        thread_events.sort(key=lambda r: (r.sent_at or r.sent_at, r.message_id))

        for position, event_row in enumerate(thread_events):
            is_orphan = event_row.message_id not in all_msg_ids and event_row.message_id != root_msg_id
            # Check if in_reply_to target is absent from database
            if event_row.in_reply_to and event_row.in_reply_to not in msg_id_to_row:
                is_orphan = True

            updates.append({
                "id": event_row.id,
                "thread_id": root_msg_id,
                "thread_position": position,
                "thread_orphan": is_orphan,
            })
            thread_members[root_msg_id].append({
                "message_id": event_row.message_id,
                "thread_position": position,
                "sent_at": event_row.sent_at,
            })

    # Phase 3: Subject+participants proxy fallback for messages without refs
    proxy_groups: dict[str, list] = defaultdict(list)

    for row in events_without_refs:
        normalized = _normalize_subject(row.subject)
        recip_hash = _recipients_hash(row.recipients_json)
        bucket = _date_bucket(row.sent_at)
        group_key = f"{recip_hash}:{normalized}:{bucket}"
        proxy_groups[group_key].append(row)

    for group_key, group_events in proxy_groups.items():
        if len(group_events) < 2:
            # Single message — still assign position but not a real thread
            for event_row in group_events:
                # Only update if not already handled
                if not any(u["id"] == event_row.id for u in updates):
                    updates.append({
                        "id": event_row.id,
                        "thread_id": event_row.message_id,
                        "thread_position": 0,
                        "thread_orphan": False,
                    })
                    thread_members[event_row.message_id].append({
                        "message_id": event_row.message_id,
                        "thread_position": 0,
                        "sent_at": event_row.sent_at,
                    })
            continue

        # Multiple messages in proxy group — treat as orphan thread
        group_events.sort(key=lambda r: (r.sent_at or r.sent_at, r.message_id))
        root_msg_id = group_events[0].message_id

        for position, event_row in enumerate(group_events):
            updates.append({
                "id": event_row.id,
                "thread_id": root_msg_id,
                "thread_position": position,
                "thread_orphan": True,  # R1 fallback: proxy-grouped → orphan
            })
            thread_members[root_msg_id].append({
                "message_id": event_row.message_id,
                "thread_position": position,
                "sent_at": event_row.sent_at,
            })

    # Phase 4: Apply updates
    if dry_run:
        logger.info(
            "thread_reconstructor.dry_run",
            updates=len(updates),
            threads=len(threads) + len(proxy_groups),
        )
        return {
            "thread_count": len(threads) + len(proxy_groups),
            "event_count": len(updates),
        }

    for upd in updates:
        session.execute(
            text(
                "UPDATE communication_events "
                "SET thread_id = :thread_id, "
                "    thread_position = :thread_position, "
                "    thread_orphan = :thread_orphan "
                "WHERE id = :id"
            ),
            upd,
        )

    session.commit()

    _apply_supersession_for_threads(session, thread_members)

    # Emit OTel counter — one per thread reconstructed (D513)
    thread_count = len(threads) + len(
        [g for g in proxy_groups.values() if len(g) >= 2]
    )
    try:
        from src.analytics.metrics import grace_email_thread_reconstructed_total

        grace_email_thread_reconstructed_total.add(thread_count)
    except Exception:  # noqa: BLE001
        pass

    logger.info(
        "thread_reconstructor.complete",
        thread_count=thread_count,
        event_count=len(updates),
    )

    return {
        "thread_count": thread_count,
        "event_count": len(updates),
    }


def main() -> None:
    """CLI entry point."""
    # F-0049 family / F-15: mirror this subprocess's OTel counters into the
    # prometheus multiproc dir (no-op when PROMETHEUS_MULTIPROC_DIR is unset)
    # — same pattern as src/extraction/extraction_bridge.py; without it the
    # D513 grace_email_thread_reconstructed_total counter dies with the
    # subprocess and is structurally unobservable.
    from src.analytics.subprocess_metrics import init_subprocess_metrics

    init_subprocess_metrics()

    parser = _build_argparser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    from sqlalchemy.orm import Session as SASession

    engine = get_engine()

    with SASession(engine) as session:
        if args.command == "supersede":
            # F-0032(a) / ISS-0036: supersession re-drive over threaded events.
            result = resupersede_threads(
                session=session,
                source_id=UUID(args.source_id) if args.source_id else None,
                thread_id=args.thread_id,
                limit=args.limit,
                dry_run=args.dry_run,
            )
        else:
            result = reconstruct_threads(
                session=session,
                source_id=UUID(args.source_id) if args.source_id else None,
                limit=args.limit,
                dry_run=args.dry_run,
                reprocess=args.reprocess,
            )

    logger.info("thread_reconstructor.exit", **result)


if __name__ == "__main__":
    main()
