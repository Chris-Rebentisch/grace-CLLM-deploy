"""D297 — find_covering_directives behavioral tests."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.api.change_directive_coverage import find_covering_directives
from src.change_directives.models import (
    ChangeDirectiveCreateRequest,
    DirectiveStatus,
)
from src.change_directives.repository import create, transition
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


def _create_active_directive(session, author, segment: str, **overrides):
    body = ChangeDirectiveCreateRequest(
        tier="Operational_Adjustment",
        title=f"Cover {segment}",
        description="X",
        affected_segments=[segment],
        **overrides,
    )
    directive = create(session, body, author)
    session._test_created_ids.append(str(directive["directive_id"]))  # type: ignore[attr-defined]
    session.commit()
    transition(
        session,
        directive["directive_id"],
        DirectiveStatus.ACTIVE,
        author,
        reason="ratified",
    )
    return directive


def test_returns_active_directives_covering_segment(session) -> None:
    author = uuid4()
    _create_active_directive(session, author, "finance")
    found = find_covering_directives(
        session,
        segment_id="finance",
        element_name=None,
        requesting_user=author,
        admin_key_present=False,
    )
    assert len(found) >= 1
    assert all(d.status == DirectiveStatus.ACTIVE for d in found)
    assert all("finance" in d.affected_segments for d in found)


def test_excludes_directives_for_other_segments(session) -> None:
    author = uuid4()
    _create_active_directive(session, author, "operations")
    found = find_covering_directives(
        session,
        segment_id="finance",
        element_name=None,
        requesting_user=author,
        admin_key_present=True,
    )
    # Only directives where 'finance' is in affected_segments.
    assert all("finance" in d.affected_segments for d in found)


def test_visibility_filters_other_users(session) -> None:
    author = uuid4()
    intruder = uuid4()
    _create_active_directive(
        session, author, "secrets", visibility="private_to_self"
    )
    found = find_covering_directives(
        session,
        segment_id="secrets",
        element_name=None,
        requesting_user=intruder,
        admin_key_present=False,
    )
    assert len(found) == 0
    # Author sees their own
    found2 = find_covering_directives(
        session,
        segment_id="secrets",
        element_name=None,
        requesting_user=author,
        admin_key_present=False,
    )
    assert len(found2) == 1
