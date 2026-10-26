"""fence a removed note file

A file can be removed from its note (#734). When the note stays, as a text
note, timeline_notes.file_removed is set, and the note takes no other file:
an upload still on its way for it, such as a retry whose first attempt had
landed, is refused rather than bringing the file back. Every existing note
holds its file or never had one, so the column starts false everywhere.

Downgrade drops the column, and with it the fence: a stale upload could then
attach a file to a note whose file was removed.

Revision ID: 7bb9c74f9946
Revises: c4d81e6a2f90
Create Date: 2026-10-26 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7bb9c74f9946"
down_revision: Union[str, Sequence[str], None] = "c4d81e6a2f90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "timeline_notes",
        sa.Column(
            "file_removed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("timeline_notes", "file_removed")
