"""Repository for ``sensitivity_classification_reports`` (Chunk 43, CP3 / D344).

Append-only governance: the c43a migration installs a BEFORE UPDATE OR
DELETE trigger that raises ``check_violation`` outside of
``alembic.downgrading``, so this module exposes only INSERT + read
helpers. There is no UPDATE or DELETE entry point.

Persisted columns:

* ``id`` UUID PK (server-generated)
* ``permission_matrix_id`` UUID FK → ``permission_matrices``
* ``generated_at`` TIMESTAMPTZ
* ``tag_inventory`` JSONB
* ``coverage_breakdown`` JSONB
* ``untagged_rules`` JSONB
* ``tag_hygiene_findings`` JSONB
* ``truncated`` BOOL
* ``coverage_band`` TEXT NULL  (CHECK ∈ {high, medium, low})
* ``coverage_score`` REAL NULL (server-side only — never returned via
  API per D120/D217)
* ``corpus_below_floor`` BOOL
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text

from src.permissions.models import (
    SensitivityClassificationReport,
)


def _row_to_dict(row: Any) -> dict[str, Any]:
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    if isinstance(row, dict):
        return row
    return dict(row)


def _coerce_jsonb(value: Any) -> Any:
    """Postgres JSONB columns deserialize as Python objects via psycopg2,
    but some test fixtures return strings. Be defensive."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def insert_report(
    session,
    *,
    report: SensitivityClassificationReport,
) -> dict[str, Any]:
    """INSERT a new ``sensitivity_classification_reports`` row.

    Server-side defaults (``id``, ``generated_at``) are honored: the
    Pydantic ``report.report_id`` and ``report.generated_at`` are passed
    explicitly so callers retain identity over the round-trip.

    Also writes the three denormalized columns on the parent
    ``permission_matrices`` row (``coverage_band``, ``tag_count``,
    ``untagged_rule_count``) so the matrix list view can render
    coverage labels without joining the report.
    """
    sql = text(
        """
        INSERT INTO sensitivity_classification_reports (
            id,
            permission_matrix_id,
            generated_at,
            tag_inventory,
            coverage_breakdown,
            untagged_rules,
            tag_hygiene_findings,
            truncated,
            coverage_band,
            coverage_score,
            corpus_below_floor
        ) VALUES (
            :id,
            :permission_matrix_id,
            :generated_at,
            CAST(:tag_inventory AS JSONB),
            CAST(:coverage_breakdown AS JSONB),
            CAST(:untagged_rules AS JSONB),
            CAST(:tag_hygiene_findings AS JSONB),
            :truncated,
            :coverage_band,
            :coverage_score,
            :corpus_below_floor
        )
        RETURNING id, permission_matrix_id, generated_at, tag_inventory,
                  coverage_breakdown, untagged_rules, tag_hygiene_findings,
                  truncated, coverage_band, coverage_score,
                  corpus_below_floor
        """
    )
    row = session.execute(
        sql,
        {
            "id": report.report_id,
            "permission_matrix_id": report.permission_matrix_id,
            "generated_at": report.generated_at,
            "tag_inventory": json.dumps(
                [e.model_dump(mode="json") for e in report.tag_inventory]
            ),
            "coverage_breakdown": json.dumps(
                [e.model_dump(mode="json") for e in report.coverage_breakdown]
            ),
            "untagged_rules": json.dumps(
                [e.model_dump(mode="json") for e in report.untagged_rules]
            ),
            "tag_hygiene_findings": json.dumps(
                [e.model_dump(mode="json") for e in report.tag_hygiene_findings]
            ),
            "truncated": report.truncated,
            "coverage_band": report.coverage_band,
            "coverage_score": report.coverage_score,
            "corpus_below_floor": report.corpus_below_floor,
        },
    ).one()

    # Denormalize onto permission_matrices for cheap list-view rendering.
    session.execute(
        text(
            """
            UPDATE permission_matrices
               SET coverage_band = :coverage_band,
                   tag_count = :tag_count,
                   untagged_rule_count = :untagged_rule_count
             WHERE permission_matrix_id = :matrix_id
            """
        ),
        {
            "coverage_band": report.coverage_band,
            "tag_count": len(report.tag_inventory),
            "untagged_rule_count": len(report.untagged_rules),
            "matrix_id": report.permission_matrix_id,
        },
    )

    return _row_to_dict(row)


def hydrate_report(row: dict[str, Any]) -> SensitivityClassificationReport:
    """Rehydrate a stored row into a ``SensitivityClassificationReport``."""
    return SensitivityClassificationReport.model_validate(
        {
            "report_id": row["id"],
            "permission_matrix_id": row["permission_matrix_id"],
            "generated_at": row["generated_at"],
            "tag_inventory": _coerce_jsonb(row.get("tag_inventory") or []),
            "coverage_breakdown": _coerce_jsonb(
                row.get("coverage_breakdown") or []
            ),
            "untagged_rules": _coerce_jsonb(row.get("untagged_rules") or []),
            "tag_hygiene_findings": _coerce_jsonb(
                row.get("tag_hygiene_findings") or []
            ),
            "truncated": bool(row.get("truncated", False)),
            "coverage_band": row.get("coverage_band"),
            "coverage_score": row.get("coverage_score"),
            "corpus_below_floor": bool(row.get("corpus_below_floor", False)),
        }
    )


def get_latest_for_matrix(
    session, matrix_id: UUID
) -> dict[str, Any] | None:
    """Return the most-recent report row for a given matrix, or None."""
    sql = text(
        """
        SELECT id, permission_matrix_id, generated_at, tag_inventory,
               coverage_breakdown, untagged_rules, tag_hygiene_findings,
               truncated, coverage_band, coverage_score, corpus_below_floor
        FROM sensitivity_classification_reports
        WHERE permission_matrix_id = :matrix_id
        ORDER BY generated_at DESC
        LIMIT 1
        """
    )
    row = session.execute(sql, {"matrix_id": matrix_id}).one_or_none()
    return _row_to_dict(row) if row is not None else None


def get_report_by_id(
    session, report_id: UUID
) -> dict[str, Any] | None:
    sql = text(
        """
        SELECT id, permission_matrix_id, generated_at, tag_inventory,
               coverage_breakdown, untagged_rules, tag_hygiene_findings,
               truncated, coverage_band, coverage_score, corpus_below_floor
        FROM sensitivity_classification_reports
        WHERE id = :report_id
        """
    )
    row = session.execute(sql, {"report_id": report_id}).one_or_none()
    return _row_to_dict(row) if row is not None else None


def list_reports_for_matrix(
    session,
    *,
    matrix_id: UUID,
    limit: int = 25,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Paginated chronological-order list (newest first) of reports for
    one matrix. Cursor pagination is layered on top by the route."""
    sql = text(
        """
        SELECT id, permission_matrix_id, generated_at, tag_inventory,
               coverage_breakdown, untagged_rules, tag_hygiene_findings,
               truncated, coverage_band, coverage_score, corpus_below_floor
        FROM sensitivity_classification_reports
        WHERE permission_matrix_id = :matrix_id
        ORDER BY generated_at DESC, id DESC
        LIMIT :lim OFFSET :off
        """
    )
    rows = session.execute(
        sql, {"matrix_id": matrix_id, "lim": limit, "off": offset}
    ).all()
    return [_row_to_dict(r) for r in rows]


__all__ = [
    "get_latest_for_matrix",
    "get_report_by_id",
    "hydrate_report",
    "insert_report",
    "list_reports_for_matrix",
]
