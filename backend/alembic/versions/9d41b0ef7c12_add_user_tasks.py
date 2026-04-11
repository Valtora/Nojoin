"""add user tasks

Revision ID: 9d41b0ef7c12
Revises: c4f9a3d1b2e7
Create Date: 2026-04-11 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "9d41b0ef7c12"
down_revision: Union[str, Sequence[str], None] = "c4f9a3d1b2e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_tasks",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column("due_on", sa.Date(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_user_tasks_completed_at"), "user_tasks", ["completed_at"], unique=False)
    op.create_index(op.f("ix_user_tasks_user_id"), "user_tasks", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_user_tasks_user_id"), table_name="user_tasks")
    op.drop_index(op.f("ix_user_tasks_completed_at"), table_name="user_tasks")
    op.drop_table("user_tasks")
