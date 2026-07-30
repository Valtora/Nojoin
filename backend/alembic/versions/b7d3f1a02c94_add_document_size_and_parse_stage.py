"""Add document file size and parse stage

Revision ID: b7d3f1a02c94
Revises: a4f1e9c72b58
Create Date: 2026-07-30 00:00:00.000000

Two columns behind the documents table view.

``file_size_bytes`` is recorded at upload rather than stat()-ed on read: the
documents list is a hot path, and the file may have been removed from disk
since. Existing rows are left NULL and the UI omits the size for them, which is
honest -- backfilling by stat() would report the current file, not the upload.

``parse_stage`` names the phase a running parse is in. Page counters alone are
ambiguous: they reach "7 of 7" while indexing is still running, which reads as a
hang rather than as progress.
"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d3f1a02c94"
down_revision: Union[str, Sequence[str], None] = "a4f1e9c72b58"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "documents", sa.Column("file_size_bytes", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "documents",
        sa.Column("parse_stage", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("documents", "parse_stage")
    op.drop_column("documents", "file_size_bytes")
