"""add calendar event location fields

Revision ID: 96b9c4f5d2aa
Revises: 62e4d8bbf3b1
Create Date: 2026-04-12 16:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '96b9c4f5d2aa'
down_revision: Union[str, Sequence[str], None] = '62e4d8bbf3b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('calendar_events', sa.Column('location_text', sa.Text(), nullable=True))
    op.add_column('calendar_events', sa.Column('meeting_url', sa.String(length=2048), nullable=True))
    op.execute("UPDATE calendar_sources SET sync_cursor = NULL")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('calendar_events', 'meeting_url')
    op.drop_column('calendar_events', 'location_text')