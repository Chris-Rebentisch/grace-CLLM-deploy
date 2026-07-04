"""Chunk 43 — c43a sensitivity_classification_reports + denorm columns round-trip.

Asserts:

* the new ``sensitivity_classification_reports`` table exists,
* the BEFORE UPDATE/DELETE trigger raises ``check_violation`` outside
  ``alembic.downgrading``,
* the ``permission_matrices`` denormalized columns are present and accept
  NULL,
* the ``coverage_band`` CHECK constraint on both tables only admits
  the three sanctioned band labels,
* ``GRANT SELECT`` to ``grace_readonly`` is present (when the role
  exists in the dev DB).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DataError, IntegrityError, InternalError, ProgrammingError

from src.shared.config import get_settings


@pytest.fixture(scope="module")
def engine():
    settings = get_settings()
    eng = create_engine(settings.database_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


def _seed_matrix(conn) -> str:
    matrix_id = str(uuid4())
    payload_hash = uuid4().hex + uuid4().hex  # 64 hex chars
    conn.execute(
        text(
            """
            INSERT INTO permission_matrices (
                permission_matrix_id, payload, payload_hash, created_by
            ) VALUES (
                :matrix_id, '{}'::jsonb, :payload_hash, :created_by
            )
            """
        ),
        {
            "matrix_id": matrix_id,
            "payload_hash": payload_hash,
            "created_by": "test-suite",
        },
    )
    return matrix_id


def _seed_report(conn, matrix_id: str) -> str:
    report_id = str(uuid4())
    conn.execute(
        text(
            """
            INSERT INTO sensitivity_classification_reports (
                id, permission_matrix_id, tag_inventory, coverage_breakdown,
                untagged_rules, tag_hygiene_findings, truncated, coverage_band,
                coverage_score, corpus_below_floor
            ) VALUES (
                :id, :matrix_id, '[]'::jsonb, '[]'::jsonb,
                '[]'::jsonb, '[]'::jsonb, false, 'high',
                0.92, false
            )
            """
        ),
        {"id": report_id, "matrix_id": matrix_id},
    )
    return report_id


def _cleanup(conn, matrix_id: str) -> None:
    conn.execute(text("SELECT set_config('alembic.downgrading','true', true)"))
    conn.execute(
        text(
            "DELETE FROM sensitivity_classification_reports "
            "WHERE permission_matrix_id = :mid"
        ),
        {"mid": matrix_id},
    )
    conn.execute(
        text(
            "DELETE FROM permission_matrices "
            "WHERE permission_matrix_id = :mid"
        ),
        {"mid": matrix_id},
    )


def test_sensitivity_reports_table_exists(engine) -> None:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT tablename FROM pg_tables
                WHERE tablename = 'sensitivity_classification_reports'
                """
            )
        ).fetchall()
    assert {r[0] for r in rows} == {"sensitivity_classification_reports"}


def test_permission_matrices_denorm_columns_present(engine) -> None:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT column_name, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'permission_matrices'
                  AND column_name IN ('coverage_band','tag_count','untagged_rule_count')
                """
            )
        ).fetchall()
    cols = {r[0]: r[1] for r in rows}
    assert cols == {
        "coverage_band": "YES",
        "tag_count": "YES",
        "untagged_rule_count": "YES",
    }


def test_sensitivity_report_update_blocked(engine) -> None:
    with engine.begin() as conn:
        matrix_id = _seed_matrix(conn)
        report_id = _seed_report(conn, matrix_id)

    with engine.begin() as conn:
        with pytest.raises((InternalError, IntegrityError, ProgrammingError)):
            conn.execute(
                text(
                    "UPDATE sensitivity_classification_reports "
                    "SET truncated = true WHERE id = :id"
                ),
                {"id": report_id},
            )

    with engine.begin() as conn:
        _cleanup(conn, matrix_id)


def test_sensitivity_report_delete_blocked(engine) -> None:
    with engine.begin() as conn:
        matrix_id = _seed_matrix(conn)
        report_id = _seed_report(conn, matrix_id)

    with engine.begin() as conn:
        with pytest.raises((InternalError, IntegrityError, ProgrammingError)):
            conn.execute(
                text(
                    "DELETE FROM sensitivity_classification_reports "
                    "WHERE id = :id"
                ),
                {"id": report_id},
            )

    with engine.begin() as conn:
        _cleanup(conn, matrix_id)


def test_coverage_band_check_constraint_rejects_invalid_label(engine) -> None:
    with engine.begin() as conn:
        matrix_id = _seed_matrix(conn)

    with engine.begin() as conn:
        with pytest.raises((IntegrityError, DataError, ProgrammingError, InternalError)):
            conn.execute(
                text(
                    """
                    INSERT INTO sensitivity_classification_reports (
                        id, permission_matrix_id, coverage_band
                    ) VALUES (:id, :mid, 'critical')
                    """
                ),
                {"id": str(uuid4()), "mid": matrix_id},
            )

    with engine.begin() as conn:
        _cleanup(conn, matrix_id)


def test_permission_matrices_coverage_band_check_rejects_invalid_label(engine) -> None:
    with engine.begin() as conn:
        matrix_id = _seed_matrix(conn)

    with engine.begin() as conn:
        conn.execute(text("SELECT set_config('alembic.downgrading','true', true)"))
        with pytest.raises((IntegrityError, DataError, ProgrammingError, InternalError)):
            conn.execute(
                text(
                    "UPDATE permission_matrices SET coverage_band = 'critical' "
                    "WHERE permission_matrix_id = :mid"
                ),
                {"mid": matrix_id},
            )

    with engine.begin() as conn:
        _cleanup(conn, matrix_id)


def test_grace_readonly_grant_on_sensitivity_reports(engine) -> None:
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
                  AND table_name = 'sensitivity_classification_reports'
                """
            )
        ).fetchall()
    assert {r[0] for r in granted} == {"sensitivity_classification_reports"}
