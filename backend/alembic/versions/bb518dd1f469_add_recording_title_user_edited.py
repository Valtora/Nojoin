"""Add recordings.title_user_edited, set when the user chose the name.

Meeting intelligence named every recording after its AI title, so a name the
user typed at Import Audio, or gave by renaming before a reprocess, was
replaced (issue #837). From here a rename, and a name given at import or at
the start of a capture, set this column, and the AI title replaces only a name
nobody chose.

Nothing recorded which existing names were chosen, so the backfill errs
towards keeping them: every existing recording counts as named by its user,
except one still called by a default name such as "Sunday 1st Nov, Morning
Meeting", which only the server gives. A recording renamed before the upgrade
then keeps its name through a reprocess, at the cost of an older AI title
staying where a reprocess would have written a new one. A default name, left
by a capture whose notes never ran or failed, still takes the AI title.

The pattern copies ``DEFAULT_MEETING_NAME_PATTERN`` in
``backend/models/recording.py`` as it stood for this revision.

Revision ID: bb518dd1f469
Revises: 53b4bc17207f
"""

import sqlalchemy as sa
from alembic import op

revision = "bb518dd1f469"
down_revision = "53b4bc17207f"
branch_labels = None
depends_on = None

DEFAULT_MEETING_NAME = (
    "^(Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day [0-9]{1,2}(st|nd|rd|th) "
    "(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec), "
    "((Early |Late )?(Morning|Afternoon|Evening)|(Late )?Night) Meeting$"
)


def upgrade() -> None:
    op.add_column(
        "recordings",
        sa.Column(
            "title_user_edited", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.execute(
        sa.text(
            "UPDATE recordings SET title_user_edited = true WHERE name !~ :pattern"
        ).bindparams(pattern=DEFAULT_MEETING_NAME)
    )


def downgrade() -> None:
    op.drop_column("recordings", "title_user_edited")
