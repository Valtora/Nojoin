"""add task archive and tags

Revision ID: 2f6a9d1c8b40
Revises: f2b6d9a8c4e1, f4c2a9b7d1e3
Create Date: 2026-05-29 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2f6a9d1c8b40"
down_revision: Union[str, Sequence[str], None] = ("f2b6d9a8c4e1", "f4c2a9b7d1e3")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user_tasks", sa.Column("body", sa.Text(), nullable=True))
    op.add_column("user_tasks", sa.Column("archived_at", sa.DateTime(), nullable=True))
    op.create_index(
        op.f("ix_user_tasks_archived_at"),
        "user_tasks",
        ["archived_at"],
        unique=False,
    )

    op.create_table(
        "user_task_tags",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("tag_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["user_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "tag_id", name="unique_user_task_tag"),
    )
    op.create_index(
        op.f("ix_user_task_tags_tag_id"),
        "user_task_tags",
        ["tag_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_task_tags_task_id"),
        "user_task_tags",
        ["task_id"],
        unique=False,
    )

def downgrade() -> None:
    op.drop_index(op.f("ix_user_task_tags_task_id"), table_name="user_task_tags")
    op.drop_index(op.f("ix_user_task_tags_tag_id"), table_name="user_task_tags")
    op.drop_table("user_task_tags")
    op.drop_index(op.f("ix_user_tasks_archived_at"), table_name="user_tasks")
    op.drop_column("user_tasks", "archived_at")
    op.drop_column("user_tasks", "body")
