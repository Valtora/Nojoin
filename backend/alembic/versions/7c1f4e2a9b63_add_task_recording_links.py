"""add task recording links

Revision ID: 7c1f4e2a9b63
Revises: 2f6a9d1c8b40
Create Date: 2026-05-29 00:00:01.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7c1f4e2a9b63"
down_revision: Union[str, Sequence[str], None] = "2f6a9d1c8b40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_task_recordings",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("recording_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["recording_id"], ["recordings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["user_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "recording_id", name="unique_user_task_recording"),
    )
    op.create_index(
        op.f("ix_user_task_recordings_recording_id"),
        "user_task_recordings",
        ["recording_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_task_recordings_task_id"),
        "user_task_recordings",
        ["task_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_user_task_recordings_task_id"),
        table_name="user_task_recordings",
    )
    op.drop_index(
        op.f("ix_user_task_recordings_recording_id"),
        table_name="user_task_recordings",
    )
    op.drop_table("user_task_recordings")
