"""Add merged_into_id to recording_speakers

Revision ID: a1b2c3d4e5f7
Revises: 2c696b1d8401
Create Date: 2026-01-14 22:21:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f7'
down_revision: Union[str, Sequence[str], None] = ('2c696b1d8401', '2d3f24296534')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('recording_speakers', sa.Column('merged_into_id', sa.BigInteger(), nullable=True))
    op.create_foreign_key(None, 'recording_speakers', 'recording_speakers', ['merged_into_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint(None, 'recording_speakers', type_='foreignkey')
    op.drop_column('recording_speakers', 'merged_into_id')
