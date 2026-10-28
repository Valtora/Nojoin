"""Add runtime live settling metadata to transcripts.

Revision ID: f2a6e1d9c735
Revises: 7bb9c74f9946
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "f2a6e1d9c735"
down_revision = "7bb9c74f9946"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
