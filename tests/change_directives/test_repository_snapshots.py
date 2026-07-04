"""Chunk 39 CP3 — snapshot repository reads + band derivation."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.change_directives.models import ChangeDirectiveCreateRequest, DirectiveStatus
from src.change_directives.repository import (
    compute_is_stalled_for_directive,
    compute_velocity_band,
    create,
    get_by_id,
    get_latest_snapshot,
    insert_realization_snapshot,
    list_snapshot_history,
    transition,
)
from src.shared.config import get_settings


@pytest.fixture(scope="module")
def engine():
    settings = get_settings()
    eng = create_engine(settings.database_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture()
def session(engine):
    conn = engine.connect()
    txn = conn.begin()
    sess = Session(bind=conn, expire_on_commit=False)
    created_ids: list[str] = []
    sess._test_created_ids = created_ids  # type: ignore[attr-defined]
    try:
        yield sess
    finally:
        sess.close()
        if created_ids:
            with engine.begin() as cleanup:
                cleanup.execute(
                    text("SELECT set_config('alembic.downgrading','true', true)")
                )
                cleanup.execute(
                    text(
                        "DELETE FROM change_directive_realization_snapshots "
                        "WHERE directive_id = ANY(CAST(:ids AS uuid[]))"
                    ),
                    {"ids": created_ids},
                )
                cleanup.execute(
                    text(
                        "DELETE FROM change_directive_state_transitions "
                        "WHERE directive_id = ANY(CAST(:ids AS uuid[]))"
                    ),
                    {"ids": created_ids},
                )
                cleanup.execute(
                    text(
                        "DELETE FROM change_directives "
                        "WHERE directive_id = ANY(CAST(:ids AS uuid[]))"
                    ),
                    {"ids": created_ids},
                )
        try:
            txn.rollback()
        except Exception:  # noqa: BLE001
            pass
        conn.close()


def _active_directive(session: Session, author: UUID) -> dict:
    body = ChangeDirectiveCreateRequest(
        tier="Operational_Adjustment",
        title="snap-test",
        description="x",
        affected_segments=["finance"],
    )
    d = create(session, body, author)
    session._test_created_ids.append(str(d["directive_id"]))  # type: ignore[attr-defined]
    session.commit()
    transition(
        session,
        d["directive_id"],
        DirectiveStatus.ACTIVE,
        author,
        reason="activate-for-snapshot-test",
    )
    row = get_by_id(session, d["directive_id"])
    assert row is not None
    return row


def test_get_latest_snapshot_round_trip(session):
    author = uuid4()
    d = _active_directive(session, author)
    did = d["directive_id"]
    ts = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
    insert_realization_snapshot(
        session,
        directive_id=did,
        snapshot_at=ts,
        criteria_results=[
            {"criterion_id": str(uuid4()), "satisfied": False}
        ],
        progress_percentage=0.25,
        velocity=None,
        evidence_count_consistent=None,
        evidence_count_counter=None,
        first_evidence_seen_at=None,
        last_counter_evidence_seen_at=None,
        criteria_all_satisfied=None,
    )
    latest = get_latest_snapshot(session, did)
    assert latest is not None
    assert latest["directive_id"] == did
    assert float(latest["progress_percentage"]) == 0.25


def test_list_snapshot_history_order(session):
    author = uuid4()
    d = _active_directive(session, author)
    did = d["directive_id"]
    for day in (1, 2):
        insert_realization_snapshot(
            session,
            directive_id=did,
            snapshot_at=datetime(2026, 5, day, tzinfo=timezone.utc),
            criteria_results=[],
            progress_percentage=float(day) / 10,
            velocity=0.01,
            evidence_count_consistent=None,
            evidence_count_counter=None,
            first_evidence_seen_at=None,
            last_counter_evidence_seen_at=None,
            criteria_all_satisfied=None,
        )
    hist = list_snapshot_history(session, did, limit=10)
    assert len(hist) == 2
    assert hist[0]["snapshot_at"] <= hist[1]["snapshot_at"]


def test_compute_velocity_band_table():
    row = {"velocity": 0.6}
    assert compute_velocity_band(row, False) == "accelerating"
    assert compute_velocity_band(row, True) == "stalled"
    assert compute_velocity_band(None, False) is None


def test_compute_is_stalled_for_directive_delegates(session):
    author = uuid4()
    d = _active_directive(session, author)
    did = d["directive_id"]
    assert isinstance(
        compute_is_stalled_for_directive(session, did, d), bool
    )
