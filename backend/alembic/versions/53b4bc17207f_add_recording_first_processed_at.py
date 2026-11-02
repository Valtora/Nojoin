"""Add recordings.first_processed_at, when a meeting first finished.

Discard deletes a recording outright, so it may act only on a meeting whose
capture or first processing never finished (issue #833). Status cannot say
which: notes regeneration, speaker inference and a reprocess each show a
finished meeting as QUEUED or PROCESSING. From here the model stamps this
column on the first flush that makes a recording PROCESSED and never clears it.

The backfill stamps every existing recording that may have finished, and
leaves only those that certainly have not: a capture or import still uploading
or paused, and a first pass whose transcript is still the capture's own
(``processing``) or does not exist yet. Stamping too many costs only Discard on
a first pass running during the upgrade, which can still go to the bin;
stamping too few would let Discard delete a finished meeting.

Revision ID: 53b4bc17207f
Revises: f2a6e1d9c735
"""

import sqlalchemy as sa
from alembic import op

revision = "53b4bc17207f"
down_revision = "f2a6e1d9c735"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recordings", sa.Column("first_processed_at", sa.DateTime(), nullable=True)
    )
    op.execute(
        """
        UPDATE recordings
        SET first_processed_at = COALESCE(processing_completed_at, updated_at)
        WHERE first_processed_at IS NULL
          AND (
            status::text NOT IN ('UPLOADING', 'PAUSED', 'QUEUED', 'PROCESSING')
            OR (
              status::text IN ('QUEUED', 'PROCESSING')
              AND EXISTS (
                SELECT 1 FROM transcripts t
                WHERE t.recording_id = recordings.id
                  AND t.transcript_status <> 'processing'
              )
            )
          )
        """
    )


def downgrade() -> None:
    op.drop_column("recordings", "first_processed_at")
