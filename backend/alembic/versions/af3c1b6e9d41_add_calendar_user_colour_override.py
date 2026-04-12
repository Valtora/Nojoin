"""add calendar user colour override

Revision ID: af3c1b6e9d41
Revises: 96b9c4f5d2aa
Create Date: 2026-04-12 21:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'af3c1b6e9d41'
down_revision: Union[str, Sequence[str], None] = '96b9c4f5d2aa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('calendar_sources', sa.Column('user_colour', sa.String(length=32), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('calendar_sources', 'user_colour')