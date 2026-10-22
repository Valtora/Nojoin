"""add recordings.capture_epoch

Names which tab or device holds a live capture. Init starts it at 0 and every
resume that takes the capture over issues the next one. A segment, pause or
finalize sent with an earlier epoch is refused, so a tab whose capture was
resumed elsewhere cannot overwrite the segments recorded since (issue #570).

Existing rows read 0, the epoch a tab on an older build never sends. Downgrade
drops the column, and the capture fence with it.

Revision ID: 8f7a09a9d7f3
Revises: edb5059e5d68
Create Date: 2026-10-22 18:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "8f7a09a9d7f3"
down_revision = "edb5059e5d68"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recordings",
        sa.Column("capture_epoch", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("recordings", "capture_epoch")
