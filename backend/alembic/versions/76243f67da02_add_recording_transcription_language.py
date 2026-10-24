"""add recordings.transcription_language

The language a recording is transcribed in, chosen at import or reprocess, or
by answering the language notice (#671). NULL means the owner's setting, which
is how every existing row reads, so nothing is backfilled and existing
recordings behave as before. Otherwise "auto" or one of the listed language
codes. It lives on the recording, not the transcript, because reprocessing
deletes the transcript and the choice has to survive that.

Downgrade drops the column, and every recording follows its owner's setting
again.

Revision ID: 76243f67da02
Revises: fb5a849e7f5b
Create Date: 2026-10-23 20:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "76243f67da02"
down_revision = "fb5a849e7f5b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recordings",
        sa.Column("transcription_language", sa.String(16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("recordings", "transcription_language")
