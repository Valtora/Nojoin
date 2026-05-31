"""add async task ownerships

Revision ID: 8ea23b91dd75
Revises: 7c1f4e2a9b63
Create Date: 2026-05-31 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "8ea23b91dd75"
down_revision: Union[str, Sequence[str], None] = "7c1f4e2a9b63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "async_task_ownerships",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("task_id", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id"),
    )
    op.create_index(
        op.f("ix_async_task_ownerships_task_id"),
        "async_task_ownerships",
        ["task_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_async_task_ownerships_user_id"),
        "async_task_ownerships",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_async_task_ownerships_user_id"),
        table_name="async_task_ownerships",
    )
    op.drop_index(
        op.f("ix_async_task_ownerships_task_id"),
        table_name="async_task_ownerships",
    )
    op.drop_table("async_task_ownerships")
