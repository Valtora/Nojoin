"""add note attachments to documents

A file attached to a timeline note, such as a still of the shared screen taken
during a meeting, is a documents row with note_id set (#653). It takes its time
from the note and is deleted with it. use_in_notes, off by default, decides
whether the file is parsed and read by notes, Meeting Chat and search; a
document with no note ignores it and is read as before.

Every existing row keeps note_id NULL, so documents behave as they did.
Downgrade drops both columns, so attachments become ordinary documents of
their meeting, read by every notes run from then on.

Revision ID: c4d81e6a2f90
Revises: 0f3b502561a6
Create Date: 2026-10-24 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d81e6a2f90"
down_revision: Union[str, Sequence[str], None] = "0f3b502561a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("documents", sa.Column("note_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "documents_note_id_fkey",
        "documents",
        "timeline_notes",
        ["note_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint("documents_note_id_key", "documents", ["note_id"])
    op.add_column(
        "documents",
        sa.Column(
            "use_in_notes", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("documents", "use_in_notes")
    op.drop_constraint("documents_note_id_key", "documents", type_="unique")
    op.drop_constraint("documents_note_id_fkey", "documents", type_="foreignkey")
    op.drop_column("documents", "note_id")
