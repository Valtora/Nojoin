"""add_processing_progress

Revision ID: a1b2c3d4e5f6
Revises: 8589d4e81ea9
Create Date: 2025-12-12 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '8589d4e81ea9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('recordings', sa.Column('processing_progress', sa.Integer(), server_default='0', nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('recordings', 'processing_progress')
