"""merge_heads

Revision ID: 17ccdc4c7f69
Revises: 98c9675f9eaf, f8e2c3a4b5d6
Create Date: 2025-11-29 16:34:32.285034

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '17ccdc4c7f69'
down_revision: Union[str, Sequence[str], None] = ('98c9675f9eaf', 'f8e2c3a4b5d6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
