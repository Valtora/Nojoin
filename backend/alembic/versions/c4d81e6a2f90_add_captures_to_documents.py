"""add captures to documents

A capture is an image taken during a meeting (a frame of the shared screen, or
a pasted image) and stored as a document of kind 'capture' at capture_ms on
the recording's audio timeline (#653). It is parsed, indexed and archived as
every document is; notes generation reads it only while use_in_notes is on.

Every existing row becomes kind 'document', with no time and use_in_notes
false, which is how documents behaved before. Downgrade drops the three
columns and their constraints, so captures become ordinary documents attached
to their meeting, read by every notes run from then on.

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
    op.add_column(
        "documents",
        sa.Column(
            "kind", sa.String(length=16), nullable=False, server_default="document"
        ),
    )
    op.add_column("documents", sa.Column("capture_ms", sa.BigInteger(), nullable=True))
    op.add_column(
        "documents",
        sa.Column(
            "use_in_notes", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.create_check_constraint(
        "ck_documents_kind", "documents", "kind IN ('document', 'capture')"
    )
    op.create_check_constraint(
        "ck_documents_capture_shape",
        "documents",
        "(kind = 'capture' AND capture_ms IS NOT NULL AND capture_ms >= 0)"
        " OR (kind = 'document' AND capture_ms IS NULL AND use_in_notes = false)",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_documents_capture_shape", "documents", type_="check")
    op.drop_constraint("ck_documents_kind", "documents", type_="check")
    op.drop_column("documents", "use_in_notes")
    op.drop_column("documents", "capture_ms")
    op.drop_column("documents", "kind")
