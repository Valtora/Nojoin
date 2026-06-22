"""add paused recording status

Revision ID: 0a1f6b8e4c2d
Revises: 4f7d2c1a9b80
Create Date: 2026-05-25 18:30:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0a1f6b8e4c2d"
down_revision: Union[str, Sequence[str], None] = "4f7d2c1a9b80"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE recordingstatus ADD VALUE IF NOT EXISTS 'PAUSED'")


def downgrade() -> None:
    # PostgreSQL enum value removal is intentionally skipped.
    pass
