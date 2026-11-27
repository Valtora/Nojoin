"""Add recording_restore_requests.

Someone a meeting is shared with can ask its owner to unarchive or restore it.
The ask is a task in the owner's list; a ``recording_restore_requests`` row ties
that task to the meeting and the kind of ask (``unarchive`` or ``restore``), at
most one per meeting and kind, so the ask is not repeated under a new meeting
name and its task is completed when the meeting comes back.

No backfill: asks made before this revision were deduplicated by task title and
stay ordinary tasks.

Revision ID: 7c1d2e9a4b60
Revises: 3885627f5053
"""

import sqlalchemy as sa
from alembic import op

revision = "7c1d2e9a4b60"
down_revision = "3885627f5053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recording_restore_requests",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("recording_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("requested_by_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["recording_id"], ["recordings.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["task_id"], ["user_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "kind IN ('unarchive', 'restore')",
            name="ck_recording_restore_requests_kind",
        ),
        sa.UniqueConstraint(
            "recording_id", "kind", name="uq_recording_restore_requests_recording_kind"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_recording_restore_requests_recording_id",
        "recording_restore_requests",
        ["recording_id"],
    )
    op.create_index(
        "ix_recording_restore_requests_task_id",
        "recording_restore_requests",
        ["task_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_recording_restore_requests_task_id",
        table_name="recording_restore_requests",
    )
    op.drop_index(
        "ix_recording_restore_requests_recording_id",
        table_name="recording_restore_requests",
    )
    op.drop_table("recording_restore_requests")
