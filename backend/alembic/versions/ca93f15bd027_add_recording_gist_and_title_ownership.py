"""Store meeting gist and preserve manually edited titles.

Existing titles lack provenance, so preserve every existing title. New recordings
allow automatic titles until their first explicit rename.

Revision ID: ca93f15bd027
Revises: f2a6e1d9c735
"""

import sqlalchemy as sa
from alembic import op

revision = "ca93f15bd027"
down_revision = "f2a6e1d9c735"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recordings", sa.Column("gist", sa.Text(), nullable=True))
    op.add_column(
        "recordings",
        sa.Column(
            "title_user_edited", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.execute(sa.text("UPDATE recordings SET title_user_edited = true"))


def downgrade() -> None:
    op.drop_column("recordings", "title_user_edited")
    op.drop_column("recordings", "gist")
