"""add calendar sync cursor

Revision ID: 62e4d8bbf3b1
Revises: b5bedab42e47
Create Date: 2026-04-12 14:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '62e4d8bbf3b1'
down_revision: Union[str, Sequence[str], None] = 'b5bedab42e47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('calendar_sources', sa.Column('sync_cursor', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('calendar_sources', 'sync_cursor')