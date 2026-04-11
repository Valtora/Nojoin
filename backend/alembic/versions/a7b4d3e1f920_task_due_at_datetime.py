"""convert user task deadlines to datetime

Revision ID: a7b4d3e1f920
Revises: 9d41b0ef7c12
Create Date: 2026-04-11 00:00:01.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7b4d3e1f920"
down_revision: Union[str, Sequence[str], None] = "9d41b0ef7c12"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user_tasks", sa.Column("due_at", sa.DateTime(), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE user_tasks
            SET due_at = due_on::timestamp + INTERVAL '23 hours 59 minutes'
            WHERE due_on IS NOT NULL
            """
        )
    )
    op.drop_column("user_tasks", "due_on")


def downgrade() -> None:
    op.add_column("user_tasks", sa.Column("due_on", sa.Date(), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE user_tasks
            SET due_on = DATE(due_at)
            WHERE due_at IS NOT NULL
            """
        )
    )
    op.drop_column("user_tasks", "due_at")