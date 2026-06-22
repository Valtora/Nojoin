"""merge_phase0_heads

Revision ID: b91f9c2d4e6a
Revises: 0a1f6b8e4c2d, 9d4b8e2f1a6c
Create Date: 2026-06-22 00:00:00.000000

"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "b91f9c2d4e6a"
down_revision: Union[str, Sequence[str], None] = ("0a1f6b8e4c2d", "9d4b8e2f1a6c")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
