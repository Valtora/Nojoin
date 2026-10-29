"""Own generation requests and snapshot coverage on each notes output.

Revision ID: b3e7c2a9d641
Revises: a8d3e6b1c942
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b3e7c2a9d641"
down_revision = "a8d3e6b1c942"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("transcripts", "notes_outputs"):
        op.add_column(
            table,
            sa.Column(
                "notes_generation_metadata",
                sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
                nullable=True,
            ),
        )


def downgrade() -> None:
    for table in ("notes_outputs", "transcripts"):
        op.drop_column(table, "notes_generation_metadata")
