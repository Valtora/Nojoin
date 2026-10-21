"""add recording_speaker_drift_refusals

Records every person whose library voiceprint the drift guard refused a
recording voiceprint for, one row per (recording speaker, person), so that
rebuilding or pooling that person's voiceprint later leaves the link out
without running the guard again (against a voiceprint of another extraction
version, its cosine means nothing). It starts empty: nothing before this
revision recorded a refusal. Deleting the recording speaker or the person
deletes its rows.

Revision ID: 7ff0bbdadad8
Revises: 63005cac6492
Create Date: 2026-10-20 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "7ff0bbdadad8"
down_revision = "63005cac6492"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recording_speaker_drift_refusals",
        sa.Column("recording_speaker_id", sa.BigInteger(), nullable=False),
        sa.Column("global_speaker_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["recording_speaker_id"], ["recording_speakers.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["global_speaker_id"], ["global_speakers.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("recording_speaker_id", "global_speaker_id"),
    )
    # The primary key serves lookups by link; this serves those by person (a
    # merge, a recalibration, and the cascade when a person is deleted).
    op.create_index(
        op.f("ix_recording_speaker_drift_refusals_global_speaker_id"),
        "recording_speaker_drift_refusals",
        ["global_speaker_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_recording_speaker_drift_refusals_global_speaker_id"),
        table_name="recording_speaker_drift_refusals",
    )
    op.drop_table("recording_speaker_drift_refusals")
