"""Add the recording consent notice outcome without backfilling older rows.

Revision ID: a6c8e4f291b0
Revises: f2a6e1d9c735
"""

import sqlalchemy as sa
from alembic import op

revision = "a6c8e4f291b0"
down_revision = "f2a6e1d9c735"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recordings", sa.Column("consent_notice_outcome", sa.String(16), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("recordings", "consent_notice_outcome")
