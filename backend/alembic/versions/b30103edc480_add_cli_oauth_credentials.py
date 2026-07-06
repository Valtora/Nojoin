"""add cli_oauth_credentials

Revision ID: b30103edc480
Revises: d9e2f4a6c8b1
Create Date: 2026-07-06 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b30103edc480"
down_revision: Union[str, Sequence[str], None] = "d9e2f4a6c8b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "cli_oauth_credentials",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=True),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(), nullable=True),
        sa.Column("oauth_client_id", sa.String(length=512), nullable=True),
        sa.Column("last_refreshed_at", sa.DateTime(), nullable=True),
        sa.Column("usage_limited_until", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "provider",
            name="uq_cli_oauth_credential_user_provider",
        ),
    )
    op.create_index(
        op.f("ix_cli_oauth_credentials_provider"),
        "cli_oauth_credentials",
        ["provider"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cli_oauth_credentials_status"),
        "cli_oauth_credentials",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cli_oauth_credentials_user_id"),
        "cli_oauth_credentials",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_cli_oauth_credentials_user_id"),
        table_name="cli_oauth_credentials",
    )
    op.drop_index(
        op.f("ix_cli_oauth_credentials_status"),
        table_name="cli_oauth_credentials",
    )
    op.drop_index(
        op.f("ix_cli_oauth_credentials_provider"),
        table_name="cli_oauth_credentials",
    )
    op.drop_table("cli_oauth_credentials")
