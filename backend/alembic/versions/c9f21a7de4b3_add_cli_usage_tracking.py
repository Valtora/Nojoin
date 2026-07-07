"""add cli usage tracking

Revision ID: c9f21a7de4b3
Revises: b30103edc480
Create Date: 2026-07-07 15:40:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9f21a7de4b3"
down_revision: Union[str, Sequence[str], None] = "b30103edc480"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Latest-known rate-limit reading on the per-user credential (advisory).
    op.add_column(
        "cli_oauth_credentials",
        sa.Column("last_utilization", sa.Float(), nullable=True),
    )
    op.add_column(
        "cli_oauth_credentials",
        sa.Column("last_rate_limit_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "cli_oauth_credentials",
        sa.Column("last_rate_limit_type", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "cli_oauth_credentials",
        sa.Column("last_rate_limit_at", sa.DateTime(), nullable=True),
    )

    # Per-user, per-day token-usage rollup for the admin usage panel.
    op.create_table(
        "cli_usage_daily",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "cache_read_input_tokens",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "cache_creation_input_tokens",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("request_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_cost_usd", sa.Float(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "provider",
            "usage_date",
            name="uq_cli_usage_daily_user_provider_date",
        ),
    )
    op.create_index(
        op.f("ix_cli_usage_daily_user_id"),
        "cli_usage_daily",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cli_usage_daily_provider"),
        "cli_usage_daily",
        ["provider"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cli_usage_daily_usage_date"),
        "cli_usage_daily",
        ["usage_date"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_cli_usage_daily_usage_date"), table_name="cli_usage_daily")
    op.drop_index(op.f("ix_cli_usage_daily_provider"), table_name="cli_usage_daily")
    op.drop_index(op.f("ix_cli_usage_daily_user_id"), table_name="cli_usage_daily")
    op.drop_table("cli_usage_daily")
    op.drop_column("cli_oauth_credentials", "last_rate_limit_at")
    op.drop_column("cli_oauth_credentials", "last_rate_limit_type")
    op.drop_column("cli_oauth_credentials", "last_rate_limit_status")
    op.drop_column("cli_oauth_credentials", "last_utilization")
