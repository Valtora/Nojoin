"""Add transcript publication sequence and runtime settling metadata.

Revision ID: f2a6e1d9c735
Revises: c4d81e6a2f90
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "f2a6e1d9c735"
down_revision = "c4d81e6a2f90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transcripts",
        sa.Column(
            "change_sequence", sa.BigInteger(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "transcripts",
        sa.Column(
            "settling_state",
            sa.JSON(none_as_null=True).with_variant(
                JSONB(none_as_null=True), "postgresql"
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("transcripts", "settling_state")
    op.drop_column("transcripts", "change_sequence")
