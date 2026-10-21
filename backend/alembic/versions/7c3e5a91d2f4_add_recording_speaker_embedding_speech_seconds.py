"""add recording_speakers.embedding_speech_seconds

Records how many seconds of selected crop speech each recording voiceprint was
built from, so that a person's library voiceprint is only ever created from a
recording voiceprint that holds enough speech (LIBRARY_VOICEPRINT_MIN_SPEECH_S).
Existing rows stay NULL: the figure needs the audio and the diarisation that
built the voiceprint, so it cannot be backfilled here. A voiceprint with a NULL
figure behaves as it did before the floor, founding a person's voiceprint
included, until reprocessing or re-extracting it measures it.

Revision ID: 7c3e5a91d2f4
Revises: 3b804f4435a1
Create Date: 2026-10-20 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "7c3e5a91d2f4"
down_revision = "3b804f4435a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recording_speakers",
        sa.Column("embedding_speech_seconds", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("recording_speakers", "embedding_speech_seconds")
