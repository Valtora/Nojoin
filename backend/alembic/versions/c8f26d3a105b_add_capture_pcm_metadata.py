"""Store exact PCM payload geometry for browser capture playback.

Revision ID: c8f26d3a105b
Revises: f2a6e1d9c735
"""

import sqlalchemy as sa
from alembic import op

revision = "c8f26d3a105b"
down_revision = "f2a6e1d9c735"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing captures finish normally; serving them before final output would
    # require probing every old chunk, so no media backfill runs in a migration.
    op.add_column(
        "recording_audio_chunks", sa.Column("pcm_data_offset", sa.BigInteger())
    )
    op.add_column("recording_audio_chunks", sa.Column("pcm_byte_size", sa.BigInteger()))


def downgrade() -> None:
    op.drop_column("recording_audio_chunks", "pcm_byte_size")
    op.drop_column("recording_audio_chunks", "pcm_data_offset")
