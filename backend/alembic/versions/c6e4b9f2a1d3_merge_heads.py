"""merge_heads

Revision ID: c6e4b9f2a1d3
Revises: a3f1c8d92e64, a3f1c9d2e6b0
Create Date: 2026-05-18 15:50:00.000000

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "c6e4b9f2a1d3"
down_revision: Union[str, Sequence[str], None] = ("a3f1c8d92e64", "a3f1c9d2e6b0")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
