"""add rolling diarization processing run kind

Revision ID: b7c1d9e4a8f2
Revises: 4a8e2f5b6c71
Create Date: 2026-05-26 16:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "b7c1d9e4a8f2"
down_revision: Union[str, Sequence[str], None] = "4a8e2f5b6c71"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE processingrunkind ADD VALUE IF NOT EXISTS 'rolling_diarization'"
    )


def downgrade() -> None:
    # PostgreSQL enum value removal is intentionally skipped.
    pass
