"""add has_seen_companion_retirement_notice to users

Revision ID: b3d7e2f14a05
Revises: c9f21a7de4b3
Create Date: 2026-07-17 10:05:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b3d7e2f14a05"
down_revision = "c9f21a7de4b3"
branch_labels = None
depends_on = None

COMPANION_RETIREMENT_NOTICE_TITLE = (
    "Companion app retired. Recording is now browser-only. See docs/CAPTURE.md."
)


def upgrade():
    op.add_column(
        "users",
        sa.Column("has_seen_companion_retirement_notice", sa.Boolean(), nullable=True),
    )
    op.execute("UPDATE users SET has_seen_companion_retirement_notice = false")

    # Seed the new marker from the old one. The startup cutover used to treat the
    # presence of the notice task as proof of delivery; anyone who still has that
    # task has already been notified and must not be notified a second time once
    # the cutover starts trusting this column instead.
    op.execute(
        sa.text(
            "UPDATE users SET has_seen_companion_retirement_notice = true "
            "WHERE id IN (SELECT user_id FROM user_tasks WHERE title = :title)"
        ).bindparams(title=COMPANION_RETIREMENT_NOTICE_TITLE)
    )

    op.alter_column(
        "users",
        "has_seen_companion_retirement_notice",
        existing_type=sa.Boolean(),
        nullable=False,
        server_default=sa.text("false"),
    )


def downgrade():
    op.drop_column("users", "has_seen_companion_retirement_notice")
