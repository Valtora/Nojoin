"""add meeting edge fields to transcripts

Revision ID: e6c2d7f8a901
Revises: c6e4b9f2a1d3
Create Date: 2026-05-19 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e6c2d7f8a901"
down_revision: Union[str, Sequence[str], None] = "c6e4b9f2a1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("transcripts", sa.Column("meeting_edge_focus", sa.Text(), nullable=True))
    op.add_column(
        "transcripts",
        sa.Column(
            "meeting_edge_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "transcripts",
        sa.Column(
            "meeting_edge_status",
            sa.String(),
            nullable=False,
            server_default=sa.text("'idle'"),
        ),
    )
    op.add_column(
        "transcripts",
        sa.Column("meeting_edge_error_message", sa.Text(), nullable=True),
    )
    op.add_column(
        "transcripts",
        sa.Column("meeting_edge_source_signature", sa.Text(), nullable=True),
    )
    op.alter_column("transcripts", "meeting_edge_status", server_default=None)


def downgrade() -> None:
    op.drop_column("transcripts", "meeting_edge_source_signature")
    op.drop_column("transcripts", "meeting_edge_error_message")
    op.drop_column("transcripts", "meeting_edge_status")
    op.drop_column("transcripts", "meeting_edge_payload")
    op.drop_column("transcripts", "meeting_edge_focus")