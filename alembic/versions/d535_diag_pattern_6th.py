"""D535 — widen ck_diagnostic_records_pattern to admit the sixth
correlation pattern (`ontology_constraint_conflict`).

D356 capture-the-why:
  Invariant: D250 locked the correlation pattern catalog at FIVE; the
    `ck_diagnostic_records_pattern` CHECK (migration c33) enumerates the
    five allowed `pattern_name` values.
  Carve-out: add a sixth value, `ontology_constraint_conflict`.
  Authorization: D535 (amends D250). Surfaced by the Claude-as-LLM A4
    correlation probe (Signal E domain/range violation + Signal B
    missing-edge, same module → ontology root cause).

Drop + re-add the CHECK (runbook Pattern C). No data rewrite — the
existing five values remain valid; the migration only widens the
allowlist.

Revision ID: d535_diag_pattern_6th
Revises: c80b_proc_docs_origin
Create Date: 2026-06-22
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d535_diag_pattern_6th"
down_revision: str | Sequence[str] | None = "c80b_proc_docs_origin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "ck_diagnostic_records_pattern"
_TABLE = "diagnostic_records"

_PATTERNS_5 = [
    "extraction_quality_problem",
    "graph_or_index_problem",
    "schema_drift_per_module",
    "cq_regression_pre_extraction",
    "relationship_gap_propagation",
]
_PATTERNS_6 = _PATTERNS_5 + ["ontology_constraint_conflict"]


def _check(names: list[str]) -> str:
    return "pattern_name IN (" + ",".join(f"'{p}'" for p in names) + ")"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _check(_PATTERNS_6))


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _check(_PATTERNS_5))
