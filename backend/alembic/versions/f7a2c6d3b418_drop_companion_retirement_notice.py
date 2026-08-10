"""drop the companion retirement notice and its delivery flag

The startup cutover used to hand every admin a one-time task announcing that the
companion app had been replaced by browser capture. The announcement has served
its purpose: the app was retired in the 2026-05-26 release, and the notice was
only ever noise on installs created since. Nothing generates it any more, so the
outstanding tasks and the column that tracked their delivery both go.

Revision ID: f7a2c6d3b418
Revises: c4a81f5d20b6
Create Date: 2026-08-10 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f7a2c6d3b418"
down_revision = "c4a81f5d20b6"
branch_labels = None
depends_on = None

COMPANION_RETIREMENT_NOTICE_TITLE = (
    "Companion app retired. Recording is now browser-only. See docs/CAPTURE.md."
)


def upgrade():
    op.execute(
        sa.text("DELETE FROM user_tasks WHERE title = :title").bindparams(
            title=COMPANION_RETIREMENT_NOTICE_TITLE
        )
    )
    op.drop_column("users", "has_seen_companion_retirement_notice")


def downgrade():
    # The notice tasks are not recreated: the column comes back as a delivery
    # record for a notice that no longer exists in any released code path.
    op.add_column(
        "users",
        sa.Column(
            "has_seen_companion_retirement_notice",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
