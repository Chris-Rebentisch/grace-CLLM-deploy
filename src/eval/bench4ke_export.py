"""Bench4KE CSV export (Chunk 34, D261).

CLI-only. Writes the upstream Bench4KE benchmarkdataset CSV schema:

    cq_id, ontology_project, ontology_iri, cq_text, expected_answer_type

Filters by ``verification_status IN {PASS, HUMAN_CONFIRMED}`` unless
``include_unverified=True``. Reads CQs through ``list_cqs`` so the
projection mirrors the existing CQ database conventions.
"""

from __future__ import annotations

import csv
from io import StringIO, TextIOBase
from pathlib import Path
from typing import Iterable

from src.discovery.cq_database import list_cqs
from src.discovery.cq_models import CQVerificationStatus
from src.shared.database import get_session_factory


_CSV_COLUMNS = (
    "cq_id",
    "ontology_project",
    "ontology_iri",
    "cq_text",
    "expected_answer_type",
)


_VERIFIED_STATUSES = frozenset({"PASS", "HUMAN_CONFIRMED"})


def _row_for_cq(cq) -> dict[str, str]:
    """Project a single CQ to the Bench4KE row.

    ``ontology_project`` and ``ontology_iri`` are sourced from
    ``metadata_extra`` when present; otherwise default to "grace" and
    an empty string respectively. ``expected_answer_type`` follows
    ``cq_type`` (the source-of-truth classification field).
    """
    extra = getattr(cq, "metadata_extra", {}) or {}
    return {
        "cq_id": str(cq.id),
        "ontology_project": str(extra.get("ontology_project", "grace")),
        "ontology_iri": str(extra.get("ontology_iri", "")),
        "cq_text": cq.canonical_text,
        "expected_answer_type": (
            cq.cq_type.value if hasattr(cq.cq_type, "value") else str(cq.cq_type)
        ),
    }


def _write_rows(rows: Iterable[dict[str, str]], target: TextIOBase) -> int:
    writer = csv.DictWriter(target, fieldnames=list(_CSV_COLUMNS))
    writer.writeheader()
    n = 0
    for row in rows:
        writer.writerow({col: row.get(col, "") for col in _CSV_COLUMNS})
        n += 1
    return n


def export_to_csv(
    output_path: Path,
    *,
    include_unverified: bool = False,
    db_session=None,
) -> int:
    """Export competency questions to a Bench4KE CSV.

    Args:
        output_path: Target CSV path on local disk.
        include_unverified: Include CQs whose ``verification_status`` is
            not ``PASS`` / ``HUMAN_CONFIRMED``. Default: False.
        db_session: Optional explicit SQLAlchemy session. Tests pass a
            session bound to a fixture engine.

    Returns:
        Number of rows written (excluding the header).
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rows = list(_collect_rows(include_unverified=include_unverified, db_session=db_session))

    with output_path.open("w", newline="", encoding="utf-8") as fh:
        return _write_rows(rows, fh)


def export_to_string(*, include_unverified: bool = False, db_session=None) -> str:
    """Same as ``export_to_csv`` but writes into an in-memory ``StringIO``.

    Used by ``test_bench4ke_export.py`` for the round-trip assertion.
    """
    rows = list(_collect_rows(include_unverified=include_unverified, db_session=db_session))
    buf = StringIO()
    _write_rows(rows, buf)
    return buf.getvalue()


def _collect_rows(
    *, include_unverified: bool, db_session=None
) -> Iterable[dict[str, str]]:
    own_session = db_session is None
    session = db_session or get_session_factory()()
    try:
        cqs = list_cqs(session)
        for cq in cqs:
            status_value = (
                cq.verification_status.value
                if hasattr(cq.verification_status, "value")
                else str(cq.verification_status)
            )
            if not include_unverified and status_value not in _VERIFIED_STATUSES:
                continue
            yield _row_for_cq(cq)
    finally:
        if own_session:
            session.close()
