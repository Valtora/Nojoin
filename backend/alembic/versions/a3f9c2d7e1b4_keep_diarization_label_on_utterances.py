"""keep the diariser's label on each utterance, and record unmerges

A merge rewrites an utterance's speaker_label to the survivor's, so once the
pipeline had joined two people there was nothing left to split them on. The
new column keeps the cluster label the diariser gave the span. The enum value
records the correction that gives those lines back to their own speaker.

Rows written before this revision stay NULL: their original labels were not
kept, and only reprocessing can recover them.

Revision ID: a3f9c2d7e1b4
Revises: d7e3a1b9c5f2
Create Date: 2026-10-16 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a3f9c2d7e1b4"
down_revision = "d7e3a1b9c5f2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "transcript_utterances",
        sa.Column("diarization_label", sa.String(length=255), nullable=True),
    )
    op.execute(
        "ALTER TYPE speakercorrectioneventtype ADD VALUE IF NOT EXISTS 'unmerge_speakers'"
    )


def downgrade():
    op.drop_column("transcript_utterances", "diarization_label")
    # PostgreSQL enum value removal is intentionally skipped.
