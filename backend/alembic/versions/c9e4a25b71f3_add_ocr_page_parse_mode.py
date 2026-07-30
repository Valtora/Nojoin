"""Add the OCR value to the page parse mode

Revision ID: c9e4a25b71f3
Revises: b7d3f1a02c94
Create Date: 2026-07-30 00:00:00.000000

Local OCR becomes the middle tier between visual analysis and a format's own
text layer, so a page needs to be able to record that it came from OCR.

``ALTER TYPE ... ADD VALUE`` cannot run inside a transaction block on older
PostgreSQL, and Alembic wraps migrations in one, so this commits first. The
statement is idempotent via IF NOT EXISTS, which matters because a partially
applied migration would otherwise be unrepeatable.

There is no downgrade. PostgreSQL cannot drop a value from an enum type, and
rebuilding the type would mean rewriting every row that references it -- far
more destructive than the forward change. Rows written as OCR simply become
unreachable through the Python enum on an older build.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9e4a25b71f3"
down_revision: Union[str, Sequence[str], None] = "b7d3f1a02c94"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("COMMIT")
    op.execute("ALTER TYPE pageparsemode ADD VALUE IF NOT EXISTS 'OCR'")


def downgrade() -> None:
    """Downgrade schema.

    Intentionally a no-op: PostgreSQL offers no way to remove an enum value, and
    recreating the type to drop one would rewrite the table.
    """
