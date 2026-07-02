"""add oauth mcp connector tables

Revision ID: a7d4e8f2c1b9
Revises: b91f9c2d4e6a
Create Date: 2026-07-01 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7d4e8f2c1b9"
down_revision: Union[str, Sequence[str], None] = "b91f9c2d4e6a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "oauth_clients",
        sa.Column("client_id", sa.String(length=64), primary_key=True),
        sa.Column("client_name", sa.String(length=256), nullable=True),
        sa.Column("redirect_uris", sa.Text(), nullable=False),
        sa.Column(
            "token_endpoint_auth_method",
            sa.String(length=32),
            nullable=False,
            server_default="none",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "oauth_authorization_codes",
        sa.Column("code_hash", sa.String(length=64), primary_key=True),
        sa.Column(
            "client_id",
            sa.String(length=64),
            sa.ForeignKey("oauth_clients.client_id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(length=256), nullable=False),
        sa.Column("code_challenge", sa.String(length=128), nullable=False),
        sa.Column(
            "code_challenge_method",
            sa.String(length=16),
            nullable=False,
            server_default="S256",
        ),
        sa.Column("resource", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False, index=True),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "oauth_refresh_tokens",
        sa.Column("token_hash", sa.String(length=64), primary_key=True),
        sa.Column("grant_id", sa.String(length=64), nullable=False, index=True),
        sa.Column(
            "client_id",
            sa.String(length=64),
            sa.ForeignKey("oauth_clients.client_id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("scope", sa.String(length=256), nullable=False),
        sa.Column("resource", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("oauth_refresh_tokens")
    op.drop_table("oauth_authorization_codes")
    op.drop_table("oauth_clients")
