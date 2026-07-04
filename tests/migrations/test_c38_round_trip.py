"""D294 — c38 four-migration round-trip + append-only trigger tests."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import InternalError, IntegrityError, ProgrammingError

from src.shared.config import get_settings


@pytest.fixture(scope="module")
def engine():
    settings = get_settings()
    eng = create_engine(settings.database_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


def _seed_directive(conn) -> str:
    directive_id = str(uuid4())
    conn.execute(
        text(
            """
            INSERT INTO change_directives (
                directive_id, tier, title, description, authored_by,
                affected_segments
            ) VALUES (
                :directive_id, 'Operational_Adjustment', 'test', 'desc',
                :authored_by, '["finance"]'::jsonb
            )
            """
        ),
        {"directive_id": directive_id, "authored_by": str(uuid4())},
    )
    return directive_id


def test_all_four_tables_exist(engine) -> None:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT tablename FROM pg_tables
                WHERE tablename IN (
                  'change_directives',
                  'change_directive_state_transitions',
                  'change_directive_evidence_criteria',
                  'change_directive_realization_snapshots'
                )
                """
            )
        ).fetchall()
    names = {r[0] for r in rows}
    assert "change_directives" in names
    assert "change_directive_state_transitions" in names
    assert "change_directive_evidence_criteria" in names
    assert "change_directive_realization_snapshots" in names


def test_state_transitions_update_blocked(engine) -> None:
    """``BEFORE UPDATE`` trigger raises check_violation on
    ``change_directive_state_transitions``."""
    with engine.begin() as conn:
        directive_id = _seed_directive(conn)
        transition_id = str(uuid4())
        transitioned_by = str(uuid4())
        conn.execute(
            text(
                """
                INSERT INTO change_directive_state_transitions (
                    id, directive_id, from_state, to_state,
                    transitioned_at, transitioned_by, hash_chain
                ) VALUES (
                    :id, :directive_id, 'draft', 'active',
                    now(), :tb, 'h1'
                )
                """
            ),
            {
                "id": transition_id,
                "directive_id": directive_id,
                "tb": transitioned_by,
            },
        )

    with engine.begin() as conn:
        with pytest.raises((InternalError, IntegrityError, ProgrammingError)):
            conn.execute(
                text(
                    "UPDATE change_directive_state_transitions "
                    "SET reason = 'edit' WHERE id = :id"
                ),
                {"id": transition_id},
            )

    # Cleanup using the alembic.downgrading escape valve.
    with engine.begin() as conn:
        conn.execute(text("SELECT set_config('alembic.downgrading','true', true)"))
        conn.execute(
            text(
                "DELETE FROM change_directive_state_transitions "
                "WHERE directive_id = :did"
            ),
            {"did": directive_id},
        )
        conn.execute(
            text(
                "DELETE FROM change_directives WHERE directive_id = :did"
            ),
            {"did": directive_id},
        )


def test_state_transitions_delete_blocked(engine) -> None:
    with engine.begin() as conn:
        directive_id = _seed_directive(conn)
        transition_id = str(uuid4())
        conn.execute(
            text(
                """
                INSERT INTO change_directive_state_transitions (
                    id, directive_id, from_state, to_state,
                    transitioned_at, transitioned_by, hash_chain
                ) VALUES (
                    :id, :directive_id, 'draft', 'active',
                    now(), :tb, 'h1'
                )
                """
            ),
            {
                "id": transition_id,
                "directive_id": directive_id,
                "tb": str(uuid4()),
            },
        )

    with engine.begin() as conn:
        with pytest.raises((InternalError, IntegrityError, ProgrammingError)):
            conn.execute(
                text(
                    "DELETE FROM change_directive_state_transitions "
                    "WHERE id = :id"
                ),
                {"id": transition_id},
            )

    # Cleanup via raw SQL using set_config to bypass trigger.
    with engine.begin() as conn:
        conn.execute(text("SELECT set_config('alembic.downgrading','true', true)"))
        conn.execute(
            text(
                "DELETE FROM change_directive_state_transitions "
                "WHERE directive_id = :did"
            ),
            {"did": directive_id},
        )
        conn.execute(
            text(
                "DELETE FROM change_directives WHERE directive_id = :did"
            ),
            {"did": directive_id},
        )


def test_grace_readonly_grant_present(engine) -> None:
    """``GRANT SELECT`` to ``grace_readonly`` exists on all four tables
    when the role exists; if the role is absent (dev environment) the
    test is skipped."""
    with engine.connect() as conn:
        role_exists = conn.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly'")
        ).scalar()
        if not role_exists:
            pytest.skip("grace_readonly role not provisioned in dev DB")

        granted = conn.execute(
            text(
                """
                SELECT table_name FROM information_schema.role_table_grants
                WHERE grantee = 'grace_readonly'
                  AND privilege_type = 'SELECT'
                  AND table_name IN (
                    'change_directives',
                    'change_directive_state_transitions',
                    'change_directive_evidence_criteria',
                    'change_directive_realization_snapshots'
                  )
                """
            )
        ).fetchall()
        names = {r[0] for r in granted}
        assert names == {
            "change_directives",
            "change_directive_state_transitions",
            "change_directive_evidence_criteria",
            "change_directive_realization_snapshots",
        }
