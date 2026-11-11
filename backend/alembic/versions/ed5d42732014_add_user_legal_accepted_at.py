"""Add users.legal_accepted_at, when each user accepted the legal disclaimer.

Only the owner saw the disclaimer, during first-run setup, and nothing recorded
that anyone accepted it (issue #1004). From here every user accepts it once,
and the server stamps the time: setup for the owner it creates, and
``POST /api/v1/users/me/legal-acceptance`` for everyone else.

No backfill. Existing users have no acceptance to record, so they are asked at
their next page load. The owner of an existing install accepted during setup,
but no time was kept and a made-up one would be worse than none, so theirs
stays empty and the setup account is not asked again.

Naive UTC, as every other datetime column in the schema.

Revision ID: ed5d42732014
Revises: bb518dd1f469
"""

import sqlalchemy as sa
from alembic import op

revision = "ed5d42732014"
down_revision = "bb518dd1f469"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("legal_accepted_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "legal_accepted_at")
