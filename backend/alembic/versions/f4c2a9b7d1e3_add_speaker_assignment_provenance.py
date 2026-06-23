"""add speaker assignment provenance

Revision ID: f4c2a9b7d1e3
Revises: e2a6f9c4d8b7
Create Date: 2026-05-27 23:10:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f4c2a9b7d1e3"
down_revision: Union[str, Sequence[str], None] = "e2a6f9c4d8b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transcript_utterances",
        sa.Column("speaker_assignment_source", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "transcript_utterances",
        sa.Column("speaker_assignment_authority", sa.String(length=32), nullable=True),
    )

    op.execute(
        """
        UPDATE transcript_utterances
        SET speaker_assignment_authority = CASE
            WHEN manual_speaker_locked THEN 'manual'
            WHEN state = 'finalized' THEN 'finalized'
            ELSE 'provisional'
        END
        """
    )
    op.execute(
        """
        UPDATE transcript_utterances
        SET speaker_assignment_source = CASE
            WHEN manual_speaker_locked THEN 'manual'
            WHEN state = 'finalized' THEN 'finalize'
            WHEN source_kind IS NOT NULL AND TRIM(source_kind) != '' THEN source_kind
            ELSE 'legacy'
        END
        """
    )

    op.alter_column(
        "transcript_utterances",
        "speaker_assignment_source",
        existing_type=sa.String(length=32),
        nullable=False,
    )
    op.alter_column(
        "transcript_utterances",
        "speaker_assignment_authority",
        existing_type=sa.String(length=32),
        nullable=False,
    )


def downgrade() -> None:
    op.drop_column("transcript_utterances", "speaker_assignment_authority")
    op.drop_column("transcript_utterances", "speaker_assignment_source")
