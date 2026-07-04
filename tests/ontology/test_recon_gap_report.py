"""Unit tests for the Perception-Evidence Gap Report computation (Chunk 36).

Five tests per spec §6 CP3 / chunk-36-prompt-v1-FINAL.md:

1. Per-section classification correctness.
2. ERD formula at threshold ``N=3``.
3. Band mapping ``{high >= 0.8, medium >= 0.5, low < 0.5}``.
4. Segment + reviewer partition resolution (session-scoped reviewer).
5. ``unemphasized_in_evidence`` rank-DESC + cap-at-20.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from src.ontology.recon_gap_report import (
    UNEMPHASIZED_CAP,
    _classify,
    _DecisionRow,
    classify_band,
    compute_gap_report,
)


def _row(
    name: str,
    decision: str = "approved",
    viewed: int = 0,
    top_ids: list[str] | None = None,
    element_type: str = "entity_type",
) -> _DecisionRow:
    return _DecisionRow(
        element_name=name,
        element_type=element_type,
        decision=decision,
        evidence_items_viewed=viewed,
        top_evidence_extraction_event_ids=top_ids or [],
    )


# ---------------------------------------------------------------------------
# 1. Per-section classification correctness.
# ---------------------------------------------------------------------------


def test_per_section_classification() -> None:
    """Approved-with-strong, approved-without-strong, and graph-only types
    each land in the correct section."""

    decisions = [
        _row("LegalEntity", decision="approved", viewed=5, top_ids=["e1", "e2", "e3"]),
        _row("Trust", decision="approved", viewed=1),
        _row("RejectedType", decision="rejected", viewed=10),
    ]
    graph_counts = {"LegalEntity": 47, "Trust": 0, "OrphanType": 9}

    with_e, without_e, unemph, strong, total = _classify(
        decisions, graph_counts, erd_threshold_n=3
    )

    assert [it.element_name for it in with_e] == ["LegalEntity"]
    assert with_e[0].instance_count == 47
    assert with_e[0].top_evidence_extraction_event_ids == ["e1", "e2", "e3"]

    assert [it.element_name for it in without_e] == ["Trust"]
    assert without_e[0].instance_count == 0
    assert without_e[0].suggested_actions  # non-empty

    assert [it.element_name for it in unemph] == ["OrphanType"]
    assert unemph[0].instance_count == 9
    assert unemph[0].decision_status == "not_reviewed"

    assert strong == 1
    assert total == 2  # approved decisions only — rejected excluded


# ---------------------------------------------------------------------------
# 2. ERD formula at threshold N=3.
# ---------------------------------------------------------------------------


def test_erd_formula_threshold_three() -> None:
    """Formula = approved_with_strong_evidence / total_approved at N=3.

    Three approved types, one with viewed >= 3 → score = 1/3.
    """

    decisions = [
        _row("A", viewed=3, top_ids=["e1"]),
        _row("B", viewed=2),
        _row("C", viewed=0),
    ]
    graph_counts = {"A": 10, "B": 5, "C": 1}

    _, _, _, strong, total = _classify(decisions, graph_counts, erd_threshold_n=3)

    assert strong == 1
    assert total == 3
    assert strong / total == pytest.approx(1 / 3)

    # Crank threshold up to 5: only viewed>=5 strong; none qualifies.
    _, _, _, strong5, total5 = _classify(decisions, graph_counts, erd_threshold_n=5)
    assert strong5 == 0
    assert total5 == 3


# ---------------------------------------------------------------------------
# 3. Band mapping.
# ---------------------------------------------------------------------------


def test_band_mapping() -> None:
    """Band thresholds: ``high >= 0.8``, ``medium >= 0.5``, ``low < 0.5``;
    ``None`` (graph below floor) yields no band."""

    assert classify_band(1.0) == "high"
    assert classify_band(0.8) == "high"
    assert classify_band(0.79) == "medium"
    assert classify_band(0.5) == "medium"
    assert classify_band(0.49) == "low"
    assert classify_band(0.0) == "low"
    assert classify_band(None) is None


# ---------------------------------------------------------------------------
# 4. Segment + reviewer partition resolution (compute uses session reviewer).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_segment_reviewer_partition_resolution() -> None:
    """``compute_gap_report`` reads the reviewer from the session row so that
    per-(segment, reviewer) ontology coexistence (D278) is preserved end to
    end. Two sessions with different reviewers must produce reports tagged
    with each reviewer's name."""

    session_id_a = uuid4()
    session_id_b = uuid4()

    # Stub the SQLAlchemy session: the reviewer lookup returns different rows
    # for each session id; review_decisions returns an empty list both times.
    db = MagicMock()
    reviewer_map = {str(session_id_a): "alice", str(session_id_b): "bob"}

    def execute(stmt, params=None):
        sql = str(stmt)
        result = MagicMock()
        if "FROM review_decisions" in sql:
            result.fetchall.return_value = []
        elif "FROM review_sessions" in sql:
            row = MagicMock()
            row.reviewer = reviewer_map[params["sid"]]
            result.fetchone.return_value = row
        else:
            result.fetchall.return_value = []
            result.fetchone.return_value = None
        return result

    db.execute.side_effect = execute

    arcade = MagicMock()
    # Provide >= floor V so the score code-path runs — but with no approved
    # decisions, score is None.
    arcade.execute_sql = AsyncMock(
        return_value={
            "result": [
                {"type_name": "LegalEntity", "cnt": 60},
                {"type_name": "Trust", "cnt": 60},
            ]
        }
    )

    report_a = await compute_gap_report(session_id_a, db, arcade)
    report_b = await compute_gap_report(session_id_b, db, arcade)

    assert report_a.reviewer == "alice"
    assert report_b.reviewer == "bob"
    # No approved decisions → score is None; threshold persisted.
    assert report_a.evidence_grounding_score is None
    assert report_a.evidence_grounding_threshold == 3


# ---------------------------------------------------------------------------
# 5. Unemphasized rank-DESC + cap-at-20.
# ---------------------------------------------------------------------------


def test_unemphasized_rank_desc_and_cap() -> None:
    """``unemphasized_in_evidence`` is sorted by ``instance_count DESC`` and
    capped at ``UNEMPHASIZED_CAP`` (20) entries."""

    decisions: list[_DecisionRow] = []  # no approved decisions
    graph_counts = {f"Type{i:02d}": 100 - i for i in range(25)}

    _, _, unemph, _, _ = _classify(decisions, graph_counts, erd_threshold_n=3)

    assert len(unemph) == UNEMPHASIZED_CAP == 20
    counts = [it.instance_count for it in unemph]
    assert counts == sorted(counts, reverse=True)
    # Top-of-list is the highest-count type.
    assert unemph[0].element_name == "Type00"
    assert unemph[0].instance_count == 100
