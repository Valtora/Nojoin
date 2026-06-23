"""drop_trim_offsets_from_recordings

Revision ID: 4f7d2c1a9b80
Revises: f2b6d9a8c4e1
Create Date: 2026-05-25 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4f7d2c1a9b80"
down_revision: Union[str, Sequence[str], None] = "f2b6d9a8c4e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column("recordings", "trim_end_s")
    op.drop_column("recordings", "trim_start_s")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column("recordings", sa.Column("trim_start_s", sa.Float(), nullable=True))
    op.add_column("recordings", sa.Column("trim_end_s", sa.Float(), nullable=True))
