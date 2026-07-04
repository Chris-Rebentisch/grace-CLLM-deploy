"""Repository tests for ``sensitivity_classification_reports`` (Chunk 43, CP3 / D344).

Covers:

* INSERT happy path returns the row + sets denormalized columns on
  ``permission_matrices``.
* BEFORE UPDATE / BEFORE DELETE trigger raises (append-only governance).
* FK integrity: ``permission_matrix_id`` must reference an existing row.
* ``get_latest_for_matrix`` returns the most recently inserted row.
* ``get_report_by_id`` lookup.
* ``list_reports_for_matrix`` paginated newest-first ordering.
* Two reports against the same matrix both visible (chronological list).
* ``hydrate_report`` round-trips the persisted JSONB cleanly.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, InternalError, ProgrammingError, IntegrityError

from src.permissions import repository as matrix_repo
from src.permissions import sensitivity_repository as repo
from src.permissions.models import (
    AccessRule,
    CoverageBreakdownEntry,
    FrameworkMapping,
    PermissionMatrix,
    RoleCluster,
    SensitivityClassificationReport,
    SensitivityTag,
    TagHygieneFinding,
    TagInventoryEntry,
    UntaggedRuleEntry,
)


# D485 carve-out (Chunk 75a): this module genuinely requires empty-baseline
# semantics (none-when-empty, chain integrity, append-only triggers).
# TRUNCATE retained with requires_db_wipe marker for D472 interlock.
pytestmark = [
    pytest.mark.skipif(
        os.environ.get("GRACE_SKIP_DB_TESTS") == "1",
        reason="Postgres not available",
    ),
    pytest.mark.requires_db_wipe,
]


def _truncate(session) -> None:
    """Reset matrix + report tables for a clean fixture."""
    session.execute(
        text(
            "TRUNCATE TABLE sensitivity_classification_reports, "
            "permission_matrices RESTART IDENTITY CASCADE"
        )
    )
    session.commit()


def _seed_matrix(session) -> str:
    """Insert one ``permission_matrices`` row and return its UUID."""
    matrix = PermissionMatrix(
        role_clusters=[
            RoleCluster(
                cluster_id="cl-1",
                display_name="cl-1",
                access_rules=[
                    AccessRule(
                        resource_kind="ontology_module",
                        resource_label="finance",
                        action="view",
                        decision="allow",
                    )
                ],
            )
        ]
    )
    row = matrix_repo.insert_matrix(session, matrix=matrix)
    session.commit()
    return row["permission_matrix_id"]


def _make_report(matrix_id, *, generated_at: datetime | None = None,
                 tagged: bool = True) -> SensitivityClassificationReport:
    when = generated_at or datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)
    if tagged:
        return SensitivityClassificationReport(
            permission_matrix_id=matrix_id,
            generated_at=when,
            tag_inventory=[
                TagInventoryEntry(
                    tag_name="pii",
                    rule_count=1,
                    cluster_count=1,
                    framework_codes=[
                        FrameworkMapping(framework="gdpr_art_9", code="9.1")
                    ],
                )
            ],
            coverage_breakdown=[
                CoverageBreakdownEntry(
                    resource_kind="ontology_module",
                    action="view",
                    total_rule_count=2,
                    tagged_rule_count=1,
                )
            ],
            untagged_rules=[
                UntaggedRuleEntry(
                    cluster_id="cl-1",
                    cluster_display_name="cl-1",
                    resource_kind="ontology_module",
                    resource_label="finance",
                    action="edit",
                )
            ],
            tag_hygiene_findings=[
                TagHygieneFinding(tag_name="pii", similar_to="pii_", distance=1)
            ],
            truncated=False,
            coverage_band="medium",
            coverage_score=0.5,
            corpus_below_floor=False,
        )
    return SensitivityClassificationReport(
        permission_matrix_id=matrix_id,
        generated_at=when,
        truncated=False,
        coverage_band=None,
        coverage_score=None,
        corpus_below_floor=True,
    )


# ---------- INSERT happy path -------------------------------------------


def test_insert_report_persists_and_returns_row(db_session):
    _truncate(db_session)
    matrix_id = _seed_matrix(db_session)
    report = _make_report(matrix_id)
    row = repo.insert_report(db_session, report=report)
    db_session.commit()
    assert row["id"] is not None
    assert str(row["permission_matrix_id"]) == str(matrix_id)
    assert row["coverage_band"] == "medium"
    assert row["truncated"] is False
    assert row["corpus_below_floor"] is False


def test_insert_report_updates_denormalized_columns_on_matrix(db_session):
    _truncate(db_session)
    matrix_id = _seed_matrix(db_session)
    report = _make_report(matrix_id)
    repo.insert_report(db_session, report=report)
    db_session.commit()
    row = db_session.execute(
        text(
            "SELECT coverage_band, tag_count, untagged_rule_count "
            "FROM permission_matrices WHERE permission_matrix_id = :mid"
        ),
        {"mid": matrix_id},
    ).one()
    assert row[0] == "medium"
    assert row[1] == 1  # one tag inventory entry
    assert row[2] == 1  # one untagged rule entry


def test_insert_report_below_floor_persists_with_null_band(db_session):
    _truncate(db_session)
    matrix_id = _seed_matrix(db_session)
    report = _make_report(matrix_id, tagged=False)
    row = repo.insert_report(db_session, report=report)
    db_session.commit()
    assert row["coverage_band"] is None
    assert row["coverage_score"] is None
    assert row["corpus_below_floor"] is True


# ---------- Append-only trigger -----------------------------------------


def test_append_only_trigger_blocks_update(db_session):
    _truncate(db_session)
    matrix_id = _seed_matrix(db_session)
    row = repo.insert_report(db_session, report=_make_report(matrix_id))
    db_session.commit()
    with pytest.raises((DBAPIError, InternalError, ProgrammingError, Exception)):
        db_session.execute(
            text(
                "UPDATE sensitivity_classification_reports "
                "SET coverage_band='high' WHERE id = :id"
            ),
            {"id": row["id"]},
        )
        db_session.commit()
    db_session.rollback()


def test_append_only_trigger_blocks_delete(db_session):
    _truncate(db_session)
    matrix_id = _seed_matrix(db_session)
    row = repo.insert_report(db_session, report=_make_report(matrix_id))
    db_session.commit()
    with pytest.raises((DBAPIError, InternalError, ProgrammingError, Exception)):
        db_session.execute(
            text(
                "DELETE FROM sensitivity_classification_reports WHERE id = :id"
            ),
            {"id": row["id"]},
        )
        db_session.commit()
    db_session.rollback()


# ---------- FK integrity -------------------------------------------------


def test_insert_report_fk_violation_for_unknown_matrix(db_session):
    _truncate(db_session)
    bogus_matrix_id = uuid4()
    report = _make_report(bogus_matrix_id)
    with pytest.raises((IntegrityError, DBAPIError, Exception)):
        repo.insert_report(db_session, report=report)
        db_session.commit()
    db_session.rollback()


# ---------- Read paths ---------------------------------------------------


def test_get_latest_for_matrix_returns_newest(db_session):
    _truncate(db_session)
    matrix_id = _seed_matrix(db_session)
    older = _make_report(
        matrix_id,
        generated_at=datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc),
    )
    newer = _make_report(
        matrix_id,
        generated_at=datetime(2026, 5, 9, 0, 0, 0, tzinfo=timezone.utc),
    )
    repo.insert_report(db_session, report=older)
    db_session.commit()
    inserted_newer = repo.insert_report(db_session, report=newer)
    db_session.commit()

    latest = repo.get_latest_for_matrix(db_session, matrix_id)
    assert latest is not None
    assert str(latest["id"]) == str(inserted_newer["id"])


def test_get_latest_for_matrix_returns_none_when_no_reports(db_session):
    _truncate(db_session)
    matrix_id = _seed_matrix(db_session)
    assert repo.get_latest_for_matrix(db_session, matrix_id) is None


def test_get_report_by_id_lookup(db_session):
    _truncate(db_session)
    matrix_id = _seed_matrix(db_session)
    inserted = repo.insert_report(db_session, report=_make_report(matrix_id))
    db_session.commit()
    fetched = repo.get_report_by_id(db_session, inserted["id"])
    assert fetched is not None
    assert str(fetched["id"]) == str(inserted["id"])


def test_get_report_by_id_returns_none_for_unknown(db_session):
    _truncate(db_session)
    assert repo.get_report_by_id(db_session, uuid4()) is None


def test_list_reports_paginated_newest_first(db_session):
    _truncate(db_session)
    matrix_id = _seed_matrix(db_session)
    inserted_ids = []
    for i in range(3):
        when = datetime(2026, 5, 1 + i, 0, 0, 0, tzinfo=timezone.utc)
        row = repo.insert_report(
            db_session, report=_make_report(matrix_id, generated_at=when)
        )
        db_session.commit()
        inserted_ids.append(row["id"])
    rows = repo.list_reports_for_matrix(
        db_session, matrix_id=matrix_id, limit=10, offset=0
    )
    assert len(rows) == 3
    assert str(rows[0]["id"]) == str(inserted_ids[2])  # newest first
    assert str(rows[2]["id"]) == str(inserted_ids[0])


def test_list_reports_pagination_offset_skips_rows(db_session):
    _truncate(db_session)
    matrix_id = _seed_matrix(db_session)
    for i in range(3):
        when = datetime(2026, 5, 1 + i, 0, 0, 0, tzinfo=timezone.utc)
        repo.insert_report(
            db_session, report=_make_report(matrix_id, generated_at=when)
        )
        db_session.commit()
    page = repo.list_reports_for_matrix(
        db_session, matrix_id=matrix_id, limit=2, offset=1
    )
    assert len(page) == 2


def test_two_reports_for_same_matrix_both_visible(db_session):
    _truncate(db_session)
    matrix_id = _seed_matrix(db_session)
    repo.insert_report(
        db_session,
        report=_make_report(
            matrix_id,
            generated_at=datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc),
        ),
    )
    db_session.commit()
    repo.insert_report(
        db_session,
        report=_make_report(
            matrix_id,
            generated_at=datetime(2026, 5, 9, 0, 0, 0, tzinfo=timezone.utc),
        ),
    )
    db_session.commit()
    rows = repo.list_reports_for_matrix(
        db_session, matrix_id=matrix_id, limit=10, offset=0
    )
    assert len(rows) == 2


# ---------- hydrate_report ----------------------------------------------


def test_hydrate_report_round_trips(db_session):
    _truncate(db_session)
    matrix_id = _seed_matrix(db_session)
    original = _make_report(matrix_id)
    inserted = repo.insert_report(db_session, report=original)
    db_session.commit()
    fetched = repo.get_report_by_id(db_session, inserted["id"])
    hydrated = repo.hydrate_report(fetched)
    assert hydrated.permission_matrix_id == matrix_id
    assert hydrated.coverage_band == "medium"
    assert hydrated.tag_inventory[0].tag_name == "pii"
    assert hydrated.tag_inventory[0].framework_codes[0].framework == "gdpr_art_9"
    assert hydrated.untagged_rules[0].cluster_id == "cl-1"
    assert hydrated.tag_hygiene_findings[0].distance == 1
