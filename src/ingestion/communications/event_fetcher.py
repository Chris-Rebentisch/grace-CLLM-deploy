"""Fetch-time annotation — attaches thread-level propagated sensitivity
tags to communication event loads via LEFT JOIN (Chunk 59, D426/D270).

D270 single-engine invariant preserved: this module reads from Postgres
only. No Enforcer signature change. Sensitivity tags are fetch-time
annotation, not enforcement admission.

v1 consumers: none in this chunk. Fetcher ships with full test coverage
but no production caller — Chunk 60 is the first consumer.

This module MUST NOT import ``src.graph.*`` (D270 single-engine).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from src.ingestion.communications.sensitivity_tagger import tags_from_bar_form


@dataclass
class CommunicationEventWithTags:
    """Communication event row enriched with propagated sensitivity tags."""

    id: str
    message_id: str
    sender_email: str
    thread_id: str | None
    triage_tier_outcome: str
    propagated_tags: list[str] = field(default_factory=list)


def fetch_events_with_propagated_tags(
    session: Session,
    where_clause: str = "1=1",
    params: dict | None = None,
) -> list[CommunicationEventWithTags]:
    """Fetch communication events with propagated sensitivity tags via LEFT JOIN.

    The ``where_clause`` is injected into the SQL WHERE clause (caller must
    parameterize). The LEFT JOIN on ``communication_sensitivity_propagation``
    attaches ``propagated_tags`` as parsed ``list[str]``.

    Args:
        session: SQLAlchemy session.
        where_clause: Additional WHERE filter (default ``'1=1'``).
        params: Bind parameters for ``where_clause``.

    Returns:
        List of enriched event objects.
    """
    sql = sa_text(
        f"SELECT ce.id, ce.message_id, ce.sender_email, ce.thread_id, "
        f"ce.triage_tier_outcome, csp.propagated_tags "
        f"FROM communication_events ce "
        f"LEFT JOIN communication_sensitivity_propagation csp "
        f"ON csp.thread_id = COALESCE(ce.thread_id, ce.message_id) "
        f"WHERE {where_clause} "
        f"ORDER BY ce.id"
    )

    rows = session.execute(sql, params or {}).fetchall()
    results: list[CommunicationEventWithTags] = []
    for row in rows:
        results.append(
            CommunicationEventWithTags(
                id=str(row[0]),
                message_id=row[1],
                sender_email=row[2],
                thread_id=row[3],
                triage_tier_outcome=row[4],
                propagated_tags=tags_from_bar_form(row[5]),
            )
        )
    return results
