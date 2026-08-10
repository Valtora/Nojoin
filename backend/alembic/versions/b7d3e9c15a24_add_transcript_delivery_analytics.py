"""Store measured vocal-delivery analytics on the transcript

Revision ID: b7d3e9c15a24
Revises: e1c47a9f60b2
Create Date: 2026-08-10 00:00:00.000000

Only the delivery tier is stored. The rest of the analytics surface -- talk
share, turns, interruptions, turn-taking -- is derived per read from the
canonical utterances, so it needs no column here, no backfill, and no
invalidation when a speaker is merged or a line is edited.

Delivery is different because producing it costs: it reads and analyses the
recording's audio. Storing it is what keeps that off the request path, and what
lets a user ask for it once per meeting rather than paying for it on every tab
open.

No backfill. Every existing transcript starts at 'pending', which is the honest
state: nothing has analysed their audio yet. Backfilling would mean reading
every recording in every library at upgrade time, on hardware the user owns,
for a tier most meetings will never have looked at. The interface offers it per
recording instead.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b7d3e9c15a24"
down_revision: Union[str, Sequence[str], None] = "e1c47a9f60b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "transcripts",
        sa.Column("analytics_payload", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "transcripts",
        sa.Column(
            "analytics_status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "transcripts",
        sa.Column("analytics_error_message", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("transcripts", "analytics_error_message")
    op.drop_column("transcripts", "analytics_status")
    op.drop_column("transcripts", "analytics_payload")
