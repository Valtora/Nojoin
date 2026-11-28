"""Add calendar sync progress.

A connection's first Proton sync runs in the background after sign-in, so
the connect dialog and the Calendars card can show how far it has got:
``sync_progress_done`` out of ``sync_progress_total`` steps (a calendar's
93-day windows, or its one change-feed read). Both are null when no sync is
under way. No backfill.

Revision ID: 1af393a1ca36
Revises: ac4dec3c4483
"""

import sqlalchemy as sa
from alembic import op

revision = "1af393a1ca36"
down_revision = "ac4dec3c4483"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "calendar_connections",
        sa.Column("sync_progress_done", sa.Integer(), nullable=True),
    )
    op.add_column(
        "calendar_connections",
        sa.Column("sync_progress_total", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("calendar_connections", "sync_progress_total")
    op.drop_column("calendar_connections", "sync_progress_done")
