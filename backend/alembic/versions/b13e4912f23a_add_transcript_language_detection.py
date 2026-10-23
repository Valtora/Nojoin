"""add transcripts.language_detection

What Whisper heard on the first 30 s of VAD speech, beside the language the
transcript used (#671): {detected, detected_label, probability, used, engine}.
The recording page asks when the two disagree. Written on every run, NULL when
no detection ran (VAD off, a failed detection, or an engine other than
Whisper). It lives on the transcript because it is a per-run output, and a
reprocess replaces it.

No backfill: existing transcripts were never detected on while a language was
set, so they never show the notice. Downgrade drops the column.

Revision ID: b13e4912f23a
Revises: 76243f67da02
Create Date: 2026-10-23 21:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "b13e4912f23a"
down_revision = "76243f67da02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transcripts",
        sa.Column(
            "language_detection",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("transcripts", "language_detection")
