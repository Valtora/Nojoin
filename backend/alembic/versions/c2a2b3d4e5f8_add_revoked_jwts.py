"""add revoked jwts table

Revision ID: c2a2b3d4e5f8
Revises: c1a2b3d4e5f7
Create Date: 2026-04-28 00:00:01.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c2a2b3d4e5f8"
down_revision: Union[str, Sequence[str], None] = "c1a2b3d4e5f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "revoked_jwts",
        sa.Column("jti", sa.String(length=64), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_type", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column(
            "revoked_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("reason", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_revoked_jwts_user_id",
        "revoked_jwts",
        ["user_id"],
    )
    op.create_index(
        "ix_revoked_jwts_expires_at",
        "revoked_jwts",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_revoked_jwts_expires_at", table_name="revoked_jwts")
    op.drop_index("ix_revoked_jwts_user_id", table_name="revoked_jwts")
    op.drop_table("revoked_jwts")
