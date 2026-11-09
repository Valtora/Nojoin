"""Fence notes generation with a run identity.

Revision ID: f1a6c9d2e743
Revises: e3c9a7b15d42

Workers must restart together: tokenless queued work cannot adopt a claim.
Existing notes and provenance survive retiring legacy generating claims.
"""

import sqlalchemy as sa
from alembic import op

revision = "f1a6c9d2e743"
down_revision = "e3c9a7b15d42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transcripts", sa.Column("notes_run_id", sa.Uuid(), nullable=True))
    op.execute(
        "UPDATE transcripts SET notes_status = CASE "
        "WHEN coalesce(notes, '') ~ '[^[:space:]]' THEN 'completed' ELSE 'pending' END, "
        "notes_progress = NULL, "
        "error_message = CASE WHEN transcript_status = 'error' "
        "THEN error_message ELSE NULL END "
        "WHERE notes_status = 'generating' AND notes_run_id IS NULL"
    )


def downgrade() -> None:
    op.drop_column("transcripts", "notes_run_id")
