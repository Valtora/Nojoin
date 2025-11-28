"""add notes to transcript

Revision ID: f8e2c3a4b5d6
Revises: da065320c05b
Create Date: 2024-01-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f8e2c3a4b5d6'
down_revision = 'da065320c05b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('transcripts', sa.Column('notes', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('transcripts', 'notes')
