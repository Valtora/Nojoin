"""add speaker name events and recording_speakers.name_source

speaker_name_events holds every naming act on a recording's speakers, with the
source that made it, its authority and the span of the meeting it was given
for. Rows are only added; a later act supersedes an earlier one through
supersedes_event_id. recording_speakers.name_source says which source gave the
name a speaker shows. It is not backfilled: NULL reads as a name from before
sources were recorded, shown as it always was.

Downgrade drops both.

Revision ID: 0f3b502561a6
Revises: b13e4912f23a
Create Date: 2026-10-23 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0f3b502561a6"
down_revision: Union[str, Sequence[str], None] = "b13e4912f23a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "recording_speakers",
        sa.Column("name_source", sa.String(length=16), nullable=True),
    )
    op.create_table(
        "speaker_name_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("recording_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("global_speaker_id", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("authority", sa.SmallInteger(), nullable=False),
        sa.Column("surface", sa.String(length=16), nullable=True),
        sa.Column("anchor_start_ms", sa.BigInteger(), nullable=False),
        sa.Column("anchor_end_ms", sa.BigInteger(), nullable=False),
        sa.Column("anchor_label", sa.String(length=255), nullable=True),
        sa.Column("recording_speaker_id", sa.BigInteger(), nullable=True),
        sa.Column("supersedes_event_id", sa.BigInteger(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("evidence", postgresql.JSONB(), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "anchor_end_ms > anchor_start_ms",
            name="ck_speaker_name_events_anchor_span",
        ),
        sa.ForeignKeyConstraint(
            ["recording_id"], ["recordings.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["global_speaker_id"], ["global_speakers.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["recording_speaker_id"], ["recording_speakers.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_event_id"], ["speaker_name_events.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_index(
        "ix_speaker_name_events_recording_id",
        "speaker_name_events",
        ["recording_id"],
    )
    op.create_index(
        "ix_speaker_name_events_recording_speaker_id",
        "speaker_name_events",
        ["recording_speaker_id"],
    )
    op.create_index(
        "ix_speaker_name_events_supersedes_event_id",
        "speaker_name_events",
        ["supersedes_event_id"],
    )
    op.create_index(
        "ix_speaker_name_events_recording_anchor",
        "speaker_name_events",
        ["recording_id", "anchor_start_ms"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("speaker_name_events")
    op.drop_column("recording_speakers", "name_source")
