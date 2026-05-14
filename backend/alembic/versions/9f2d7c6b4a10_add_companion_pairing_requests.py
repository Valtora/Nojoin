"""add companion pairing requests

Revision ID: 9f2d7c6b4a10
Revises: c2a2b3d4e5f8
Create Date: 2026-05-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9f2d7c6b4a10"
down_revision: Union[str, Sequence[str], None] = "c2a2b3d4e5f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "companion_pairing_requests",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("request_secret_hash", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("api_protocol", sa.String(length=16), nullable=False),
        sa.Column("api_host", sa.String(length=255), nullable=False),
        sa.Column("api_port", sa.Integer(), nullable=False),
        sa.Column("paired_web_origin", sa.String(length=2048), nullable=False),
        sa.Column("replacement_pairing_session_id", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("opened_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("status_detail", sa.Text(), nullable=True),
        sa.Column("failure_reason", sa.String(length=64), nullable=True),
        sa.Column("completed_pairing_session_id", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_companion_pairing_requests_user_id"),
        "companion_pairing_requests",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_companion_pairing_requests_request_id"),
        "companion_pairing_requests",
        ["request_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_companion_pairing_requests_status"),
        "companion_pairing_requests",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_companion_pairing_requests_replacement_pairing_session_id"),
        "companion_pairing_requests",
        ["replacement_pairing_session_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_companion_pairing_requests_expires_at"),
        "companion_pairing_requests",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_companion_pairing_requests_completed_pairing_session_id"),
        "companion_pairing_requests",
        ["completed_pairing_session_id"],
        unique=False,
    )

    op.execute(
        sa.text(
            """
            UPDATE companion_pairings
            SET
                status = 'revoked',
                companion_credential_hash = NULL,
                local_control_secret_encrypted = NULL,
                supersedes_pairing_session_id = NULL,
                revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP),
                revocation_reason = COALESCE(revocation_reason, 'pending_cancelled')
            WHERE status = 'pending'
            """
        )
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_companion_pairing_requests_completed_pairing_session_id"),
        table_name="companion_pairing_requests",
    )
    op.drop_index(
        op.f("ix_companion_pairing_requests_expires_at"),
        table_name="companion_pairing_requests",
    )
    op.drop_index(
        op.f("ix_companion_pairing_requests_replacement_pairing_session_id"),
        table_name="companion_pairing_requests",
    )
    op.drop_index(
        op.f("ix_companion_pairing_requests_status"),
        table_name="companion_pairing_requests",
    )
    op.drop_index(
        op.f("ix_companion_pairing_requests_request_id"),
        table_name="companion_pairing_requests",
    )
    op.drop_index(
        op.f("ix_companion_pairing_requests_user_id"),
        table_name="companion_pairing_requests",
    )
    op.drop_table("companion_pairing_requests")