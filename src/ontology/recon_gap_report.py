"""Perception-Evidence Gap Report computation (Chunk 36, D279/D280).

The Gap Report is an end-of-session audit artifact that compares what a
reviewer emphasized in a Structure/Clarify session against what is
actually instantiated in the knowledge graph. It produces three sections:

* ``emphasized_with_evidence`` — types the reviewer approved AND that
  carry strong evidence (>= ``erd_threshold_n`` evidence items viewed).
* ``emphasized_without_evidence`` — types the reviewer approved but
  that lack strong evidence (potential investment without payoff).
* ``unemphasized_in_evidence`` — types instantiated in the graph but
  not emphasized in the review (potential blind spots, framed as
  opportunities per EC-12 / D281).

The Evidence-Grounding Ratio (ERD score, internal name) is

    erd_score = approved_with_strong_evidence / total_approved

It maps to bands ``{"high" >= 0.8, "medium" >= 0.5, "low" < 0.5}``. When
the graph is below the population floor (default 100 vertices) the score
is reported as ``null`` with ``graph_population_floor_breach =
"graph_population_below_floor"`` rather than misleading 0.0.

Internal storage uses ``erd_*`` names; user-facing surfaces use
``evidence_grounding_*`` per D279 / EC-8 vocabulary discipline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Literal
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.api.recon_models import (
    EmphasizedWithEvidenceItem,
    EmphasizedWithoutEvidenceItem,
    GapReportResponse,
    UnemphasizedInEvidenceItem,
)
from src.graph.arcade_client import ArcadeClient

logger = structlog.get_logger()

UNEMPHASIZED_CAP = 20
TOP_EVIDENCE_PER_ELEMENT_CAP = 3
APPROVED_DECISIONS = ("approved", "renamed", "edited", "auto_approved")

ErdBand = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class _DecisionRow:
    element_name: str
    element_type: str
    decision: str
    evidence_items_viewed: int
    top_evidence_extraction_event_ids: list[str]


def classify_band(score: float | None) -> ErdBand | None:
    """Map a numeric ERD score onto its band label.

    Returns ``None`` when the score itself is ``None`` (graph below floor).
    """
    if score is None:
        return None
    if score >= 0.8:
        return "high"
    if score >= 0.5:
        return "medium"
    return "low"


def _is_approved(decision: str) -> bool:
    return decision in APPROVED_DECISIONS


def _classify(
    decisions: Iterable[_DecisionRow],
    graph_counts: dict[str, int],
    erd_threshold_n: int,
) -> tuple[
    list[EmphasizedWithEvidenceItem],
    list[EmphasizedWithoutEvidenceItem],
    list[UnemphasizedInEvidenceItem],
    int,
    int,
]:
    """Pure classification step. Returns the three sections plus
    (approved_with_strong_evidence, total_approved) counters."""

    emphasized_with: list[EmphasizedWithEvidenceItem] = []
    emphasized_without: list[EmphasizedWithoutEvidenceItem] = []
    approved_strong = 0
    total_approved = 0
    emphasized_names: set[str] = set()

    for d in decisions:
        if not _is_approved(d.decision):
            continue
        total_approved += 1
        emphasized_names.add(d.element_name)
        instance_count = int(graph_counts.get(d.element_name, 0))
        if d.evidence_items_viewed >= erd_threshold_n:
            approved_strong += 1
            top_ids = list(d.top_evidence_extraction_event_ids)[
                :TOP_EVIDENCE_PER_ELEMENT_CAP
            ]
            emphasized_with.append(
                EmphasizedWithEvidenceItem(
                    element_name=d.element_name,
                    element_type=d.element_type,
                    instance_count=instance_count,
                    top_evidence_extraction_event_ids=top_ids,
                )
            )
        else:
            emphasized_without.append(
                EmphasizedWithoutEvidenceItem(
                    element_name=d.element_name,
                    element_type=d.element_type,
                    instance_count=instance_count,
                    suggested_actions=[
                        "document_concept",
                        "ingest_additional_corpus",
                    ],
                )
            )

    # Unemphasized — types in the graph but never approved in the session,
    # ranked by instance_count DESC and capped at 20.
    unemphasized: list[UnemphasizedInEvidenceItem] = []
    for type_name, cnt in sorted(
        graph_counts.items(),
        key=lambda kv: kv[1],
        reverse=True,
    ):
        if type_name in emphasized_names:
            continue
        unemphasized.append(
            UnemphasizedInEvidenceItem(
                element_name=type_name,
                element_type="entity_type",
                instance_count=int(cnt),
                decision_status="not_reviewed",
            )
        )
        if len(unemphasized) >= UNEMPHASIZED_CAP:
            break

    return emphasized_with, emphasized_without, unemphasized, approved_strong, total_approved


def _read_decisions(
    db_session: Session,
    session_id: UUID,
) -> list[_DecisionRow]:
    """Read approved decisions for the target session and join D228 evidence
    fields from elicitation_events when present."""

    rows = db_session.execute(
        text(
            """
            SELECT
                rd.element_name,
                rd.element_type,
                rd.decision,
                COALESCE(rd.metadata_extra, '{}'::jsonb) AS meta
            FROM review_decisions rd
            WHERE rd.session_id = :sid
            """
        ),
        {"sid": str(session_id)},
    ).fetchall()

    out: list[_DecisionRow] = []
    for r in rows:
        meta = r.meta or {}
        viewed = meta.get("evidence_items_viewed", [])
        if isinstance(viewed, list):
            viewed_count = len(viewed)
        else:
            try:
                viewed_count = int(viewed)
            except (TypeError, ValueError):
                viewed_count = 0
        top_ids = meta.get("top_evidence_extraction_event_ids", []) or []
        if not isinstance(top_ids, list):
            top_ids = []
        out.append(
            _DecisionRow(
                element_name=r.element_name,
                element_type=r.element_type,
                decision=r.decision,
                evidence_items_viewed=viewed_count,
                top_evidence_extraction_event_ids=[str(x) for x in top_ids],
            )
        )
    return out


async def _read_graph_counts(
    arcade_client: ArcadeClient,
) -> tuple[int, dict[str, int]]:
    """Return (total_v, {type_name: count}) using the same SQL pattern as
    ``src/graph/health_metrics.py:35``.

    When the graph is fresh and the ``V`` base type has not been
    materialized yet, ArcadeDB returns ``SchemaException: Type with name
    'V' was not found``. That is semantically equivalent to ``total_v =
    0`` (no vertices populated). Treat it as below-floor and let the
    caller emit the standard ``graph_population_below_floor`` response.
    """

    try:
        result = await arcade_client.execute_sql(
            "SELECT @type AS type_name, count(*) AS cnt FROM V GROUP BY @type"
        )
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "Type with name 'V'" in msg or "SchemaException" in msg:
            logger.info("recon.gap_report.graph_unpopulated", reason=msg)
            return 0, {}
        raise
    rows = result.get("result", [])
    counts: dict[str, int] = {}
    total = 0
    for row in rows:
        type_name = str(row.get("type_name") or "")
        cnt = int(row.get("cnt") or 0)
        if type_name:
            counts[type_name] = cnt
            total += cnt
    return total, counts


def _read_session_reviewer(db_session: Session, session_id: UUID) -> str:
    row = db_session.execute(
        text("SELECT reviewer FROM review_sessions WHERE id = :sid"),
        {"sid": str(session_id)},
    ).fetchone()
    if row is None:
        return ""
    return str(row.reviewer or "")


async def compute_gap_report(
    session_id: UUID,
    db_session: Session,
    arcade_client: ArcadeClient,
    erd_threshold_n: int = 3,
    graph_population_floor: int = 100,
) -> GapReportResponse:
    """Compute a Gap Report for a completed review session.

    Side-effect-free with respect to the database — the caller is responsible
    for persisting the resulting report row + updating ``review_sessions``
    columns.
    """

    decisions = _read_decisions(db_session, session_id)
    total_v, graph_counts = await _read_graph_counts(arcade_client)
    reviewer = _read_session_reviewer(db_session, session_id)

    if total_v < graph_population_floor:
        logger.info(
            "recon.gap_report.below_floor",
            session_id=str(session_id),
            total_v=total_v,
            floor=graph_population_floor,
        )
        return GapReportResponse(
            session_id=session_id,
            reviewer=reviewer,
            generated_at=datetime.now(timezone.utc),
            evidence_grounding_score=None,
            evidence_grounding_threshold=erd_threshold_n,
            graph_population_floor_breach="graph_population_below_floor",
            emphasized_with_evidence=[],
            emphasized_without_evidence=[],
            unemphasized_in_evidence=[],
        )

    (
        emphasized_with,
        emphasized_without,
        unemphasized,
        approved_strong,
        total_approved,
    ) = _classify(decisions, graph_counts, erd_threshold_n)

    score: float | None
    if total_approved == 0:
        score = None
    else:
        score = approved_strong / total_approved

    logger.info(
        "recon.gap_report.computed",
        session_id=str(session_id),
        total_approved=total_approved,
        approved_strong=approved_strong,
        score=score,
        unemphasized_count=len(unemphasized),
    )

    # Chunk 59 (D426 — CP7): source-type breakdown.
    # Count vertices by evidence_origin; legacy vertices without the property
    # default to 'document' via COALESCE (R6 mitigation).
    from src.api.recon_models import SourceTypeBreakdown

    doc_count = 0
    comm_count = 0
    mixed_count = 0
    try:
        origin_rows = await arcade_client.query(
            "SELECT COALESCE(evidence_origin, 'document') AS origin, count(*) AS cnt "
            "FROM V GROUP BY COALESCE(evidence_origin, 'document')",
            language="sql",
        )
        origin_map: dict[str, int] = {}
        for r in (origin_rows.get("result", []) if isinstance(origin_rows, dict) else []):
            if isinstance(r, dict):
                origin_map[r.get("origin", "document")] = int(r.get("cnt", 0))
        doc_count = origin_map.get("document", 0)
        comm_count = origin_map.get("communication", 0)
        mixed_count = origin_map.get("hybrid", 0)
    except Exception:
        # Graceful degradation — origin query optional
        doc_count = total_v

    breakdown = SourceTypeBreakdown(
        document=doc_count,
        communication=comm_count,
        mixed=mixed_count,
    )

    return GapReportResponse(
        session_id=session_id,
        reviewer=reviewer,
        generated_at=datetime.now(timezone.utc),
        evidence_grounding_score=score,
        evidence_grounding_threshold=erd_threshold_n,
        graph_population_floor_breach=None,
        emphasized_with_evidence=emphasized_with,
        emphasized_without_evidence=emphasized_without,
        unemphasized_in_evidence=unemphasized,
        source_type_breakdown=breakdown,
    )
