"""hold each tag on a meeting once

recording_tags had no unique (recording_id, tag_id), unlike user_task_tags and
people_tags. The routes look for a link before they insert one, so two requests
tagging the same meeting at once could both insert it, and untagging that
meeting then failed. This revision deletes every repeated link, keeping the
oldest (lowest id) of each pair, then adds the unique constraint
unique_recording_tag, so a race ends at the constraint instead of in a second
link.

A link with no meeting or no tag is left alone; the constraint treats each NULL
as distinct. The log gives the number of links deleted, never names. A second
run deletes nothing.

Downgrade drops the constraint and leaves the deleted links deleted.

Revision ID: fb5a849e7f5b
Revises: 8f7a09a9d7f3
Create Date: 2026-10-22 22:00:00.000000

"""

import logging

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "fb5a849e7f5b"
down_revision = "8f7a09a9d7f3"
branch_labels = None
depends_on = None

# Alembic's own logger, so the count prints beside "Running upgrade".
logger = logging.getLogger("alembic.runtime.migration")

CONSTRAINT = "unique_recording_tag"


def upgrade() -> None:
    deleted = (
        op.get_bind()
        .execute(
            sa.text(
                "DELETE FROM recording_tags AS link USING recording_tags AS older"
                " WHERE older.recording_id = link.recording_id"
                " AND older.tag_id = link.tag_id AND older.id < link.id"
            )
        )
        .rowcount
    )
    logger.info("recording_tags: %d repeated links deleted", deleted)
    op.create_unique_constraint(
        CONSTRAINT, "recording_tags", ["recording_id", "tag_id"]
    )


def downgrade() -> None:
    # See the module docstring: the deleted links stay deleted.
    op.drop_constraint(CONSTRAINT, "recording_tags", type_="unique")
