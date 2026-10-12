"""widen recordings.file_size_bytes to a 64-bit integer

The column was a 32-bit INTEGER, so finalising any import over 2 GiB failed
with "value out of int32 range" and the upload was lost. A video import, which
keeps the uploaded file until its audio is extracted, routinely passes that.

Revision ID: b182c2378829
Revises: f7a2c6d3b418
Create Date: 2026-10-10 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b182c2378829"
down_revision = "f7a2c6d3b418"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "recordings",
        "file_size_bytes",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )


def downgrade():
    # A size that no longer fits is dropped rather than failing the downgrade:
    # the column is informational, and nothing reads it to find the file.
    op.alter_column(
        "recordings",
        "file_size_bytes",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
        postgresql_using=(
            "CASE WHEN file_size_bytes > 2147483647 THEN NULL ELSE file_size_bytes END"
        ),
    )
