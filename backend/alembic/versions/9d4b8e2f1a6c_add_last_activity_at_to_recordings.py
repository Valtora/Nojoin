"""add last_activity_at to recordings

Revision ID: 9d4b8e2f1a6c
Revises: 8ea23b91dd75
Create Date: 2026-06-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9d4b8e2f1a6c'
down_revision: Union[str, Sequence[str], None] = '8ea23b91dd75'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('recordings', sa.Column('last_activity_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('recordings', 'last_activity_at')
