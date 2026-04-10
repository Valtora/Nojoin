"""add processing eta and user notes

Revision ID: c4f9a3d1b2e7
Revises: a1b2c3d4e5f7
Create Date: 2026-04-10 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4f9a3d1b2e7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("transcripts", sa.Column("user_notes", sa.Text(), nullable=True))
    op.add_column("recordings", sa.Column("processing_started_at", sa.DateTime(), nullable=True))
    op.add_column("recordings", sa.Column("processing_completed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("recordings", "processing_completed_at")
    op.drop_column("recordings", "processing_started_at")
    op.drop_column("transcripts", "user_notes")