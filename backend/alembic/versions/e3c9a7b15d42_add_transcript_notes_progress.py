"""Add transcripts.notes_progress, the step a long meeting's notes are on.

The notes of a meeting too long for one model call take a call per part and
more to combine them, which can run for many minutes while the page says only
that notes are generating. The run writes its step here, beside the
``notes_status`` claim it belongs to. Regenerate Notes cannot use the
recording's ``processing_step``: a finished recording's status and step are not
a side run's to write (issue #828).

Nullable with no backfill: no run is in progress across an upgrade that
restarts the workers, and None reads as "no step to show".

Revision ID: e3c9a7b15d42
Revises: bb518dd1f469
"""

import sqlalchemy as sa
from alembic import op

revision = "e3c9a7b15d42"
down_revision = "bb518dd1f469"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transcripts",
        sa.Column("notes_progress", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transcripts", "notes_progress")
