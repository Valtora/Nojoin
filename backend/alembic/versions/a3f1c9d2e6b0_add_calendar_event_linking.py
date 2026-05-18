"""add calendar event linking

Revision ID: a3f1c9d2e6b0
Revises: 9f2d7c6b4a10
Create Date: 2026-05-16 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3f1c9d2e6b0'
down_revision: Union[str, Sequence[str], None] = '9f2d7c6b4a10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('recordings', sa.Column('calendar_event_id', sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        'fk_recordings_calendar_event_id_calendar_events',
        'recordings',
        'calendar_events',
        ['calendar_event_id'],
        ['id'],
        ondelete='SET NULL',
    )
    op.add_column('calendar_events', sa.Column('description', sa.Text(), nullable=True))
    op.add_column('calendar_events', sa.Column('attendees', sa.JSON(), nullable=True))
    op.execute("UPDATE calendar_sources SET sync_cursor = NULL")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('calendar_events', 'attendees')
    op.drop_column('calendar_events', 'description')
    op.drop_constraint('fk_recordings_calendar_event_id_calendar_events', 'recordings', type_='foreignkey')
    op.drop_column('recordings', 'calendar_event_id')
