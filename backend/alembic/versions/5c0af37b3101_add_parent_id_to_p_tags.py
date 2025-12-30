"""add parent_id to p_tags

Revision ID: 5c0af37b3101
Revises: 6d60823e9b27
Create Date: 2025-12-29 21:39:45.900827

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5c0af37b3101'
down_revision: Union[str, Sequence[str], None] = '6d60823e9b27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('p_tags', sa.Column('parent_id', sa.BigInteger(), nullable=True))
    op.create_foreign_key('fk_p_tags_parent_id_p_tags', 'p_tags', 'p_tags', ['parent_id'], ['id'], ondelete='CASCADE')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_p_tags_parent_id_p_tags', 'p_tags', type_='foreignkey')
    op.drop_column('p_tags', 'parent_id')
