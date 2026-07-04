"""F-49: namespace readiness gate — additive `is_ready` on graph_namespaces.

validation-run F-49: registering ANY child federation namespace silently
rerouted ALL retrieval through per-namespace paths whose indexes were never
built — a global 200-but-empty outage. The fix gates query routing on an
explicit readiness flag:

- mother namespaces backfill to is_ready = TRUE (the default single-graph
  path must keep working after upgrade);
- child namespaces backfill to is_ready = FALSE (fail-closed: any child that
  exists today was, by definition, causing the F-49 outage — the operator
  re-enables intentionally via PATCH /api/federation/namespaces/{id});
- new registrations set is_ready explicitly in code (mother TRUE, child FALSE).

Revision ID: f49a_ns_readiness
Revises: f57_prune_children_first
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic. (<=32 chars per D350)
revision: str = "f49a_ns_readiness"
down_revision: str = "f57_prune_children_first"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "graph_namespaces",
        sa.Column(
            "is_ready",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # Fail-closed backfill: mother ready, children not (see module docstring).
    op.execute(
        "UPDATE graph_namespaces SET is_ready = true WHERE namespace_type = 'mother'"
    )


def downgrade() -> None:
    op.drop_column("graph_namespaces", "is_ready")
