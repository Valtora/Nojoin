"""add companion pairings

Revision ID: e0b9f6c21a4d
Revises: d4b6c8e9f1a2
Create Date: 2026-04-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e0b9f6c21a4d"
down_revision: Union[str, Sequence[str], None] = "d4b6c8e9f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "companion_pairings",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("pairing_session_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("api_protocol", sa.String(length=16), nullable=False),
        sa.Column("api_host", sa.String(length=255), nullable=False),
        sa.Column("api_port", sa.Integer(), nullable=False),
        sa.Column("paired_web_origin", sa.String(length=2048), nullable=False),
        sa.Column("tls_fingerprint", sa.String(length=255), nullable=True),
        sa.Column("local_control_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("local_control_secret_version", sa.Integer(), nullable=False),
        sa.Column("supersedes_pairing_session_id", sa.String(length=64), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revocation_reason", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_companion_pairings_pairing_session_id"),
        "companion_pairings",
        ["pairing_session_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_companion_pairings_status"),
        "companion_pairings",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_companion_pairings_supersedes_pairing_session_id"),
        "companion_pairings",
        ["supersedes_pairing_session_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_companion_pairings_user_id"),
        "companion_pairings",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_companion_pairings_user_id"),
        table_name="companion_pairings",
    )
    op.drop_index(
        op.f("ix_companion_pairings_supersedes_pairing_session_id"),
        table_name="companion_pairings",
    )
    op.drop_index(
        op.f("ix_companion_pairings_status"),
        table_name="companion_pairings",
    )
    op.drop_index(
        op.f("ix_companion_pairings_pairing_session_id"),
        table_name="companion_pairings",
    )
    op.drop_table("companion_pairings")