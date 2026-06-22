"""add speaker name suggestions to transcripts

Revision ID: e5a7c3b1d9f4
Revises: d1a4c7b9e2f1
Create Date: 2026-05-20 16:30:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5a7c3b1d9f4"
down_revision: Union[str, Sequence[str], None] = "d1a4c7b9e2f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transcripts",
        sa.Column(
            "speaker_name_suggestions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.alter_column("transcripts", "speaker_name_suggestions", server_default=None)


def downgrade() -> None:
    op.drop_column("transcripts", "speaker_name_suggestions")
