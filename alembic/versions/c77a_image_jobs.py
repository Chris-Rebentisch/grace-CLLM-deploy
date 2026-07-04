# D502 — Widen extraction_jobs.job_kind CHECK to include 'image'.
# Enables image processing jobs via existing POST /api/extraction/jobs route.
#
# Invariant carve-out: Alembic head change.
# (1) Invariant: Alembic migration chain.
# (2) Carve-out: new c77a_image_jobs migration.
# (3) Authorization: D502 / chunk-77b-spec-v2-FINAL.md §4.

"""c77a: widen job_kind for image jobs (D502)

Revision ID: c77a_image_jobs
Revises: c72a_extraction_jobs
Create Date: 2026-05-28
"""

from alembic import op

revision: str = "c77a_image_jobs"
down_revision: str = "c72a_extraction_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE extraction_jobs
            DROP CONSTRAINT IF EXISTS extraction_jobs_job_kind_check
    """)
    op.execute("""
        ALTER TABLE extraction_jobs
            ADD CONSTRAINT extraction_jobs_job_kind_check
            CHECK (job_kind IN ('document', 'batch', 'image'))
    """)


def downgrade() -> None:
    # Delete any image-typed rows before re-narrowing the CHECK constraint
    op.execute("DELETE FROM extraction_jobs WHERE job_kind = 'image'")
    op.execute("""
        ALTER TABLE extraction_jobs
            DROP CONSTRAINT IF EXISTS extraction_jobs_job_kind_check
    """)
    op.execute("""
        ALTER TABLE extraction_jobs
            ADD CONSTRAINT extraction_jobs_job_kind_check
            CHECK (job_kind IN ('document', 'batch'))
    """)
