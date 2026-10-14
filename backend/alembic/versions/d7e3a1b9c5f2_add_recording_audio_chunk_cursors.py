"""add the per-recording chunk placement cursor

Every live capture segment used to re-sync the whole recording: every chunk
row and every audio window manifest, under a row lock on the recording. The
cost of a segment grew with the meeting, and past about 100 minutes uploads
fell behind the capture (issue #417). The cursor records how far the chunks
are placed, so a segment touches only what it unblocks.

The table holds live scratch state only. A recording in flight during the
upgrade has no cursor and builds one from its chunk rows on its next segment.

Revision ID: d7e3a1b9c5f2
Revises: c4d8e1f2a3b5
Create Date: 2026-10-14 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d7e3a1b9c5f2"
down_revision = "c4d8e1f2a3b5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "recording_audio_chunk_cursors",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("recording_id", sa.BigInteger(), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("through_sequence", sa.BigInteger(), nullable=False),
        sa.Column("end_ms", sa.BigInteger(), nullable=False),
        sa.Column("group_start_sequence", sa.BigInteger(), nullable=False),
        sa.Column("group_start_ms", sa.BigInteger(), nullable=False),
        sa.Column("group_first_window_index", sa.BigInteger(), nullable=False),
        sa.Column("target_window_ms", sa.BigInteger(), nullable=False),
        sa.Column("hop_ms", sa.BigInteger(), nullable=False),
        sa.Column("sealed", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["recording_id"], ["recordings.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recording_id",
            "source_kind",
            name="uq_recording_audio_chunk_cursors_recording_source",
        ),
    )
    op.create_index(
        op.f("ix_recording_audio_chunk_cursors_recording_id"),
        "recording_audio_chunk_cursors",
        ["recording_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        op.f("ix_recording_audio_chunk_cursors_recording_id"),
        table_name="recording_audio_chunk_cursors",
    )
    op.drop_table("recording_audio_chunk_cursors")
