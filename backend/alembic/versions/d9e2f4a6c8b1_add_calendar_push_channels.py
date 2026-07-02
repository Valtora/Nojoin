"""add calendar push channels

Revision ID: d9e2f4a6c8b1
Revises: a7d4e8f2c1b9
Create Date: 2026-07-02 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9e2f4a6c8b1"
down_revision: Union[str, Sequence[str], None] = "a7d4e8f2c1b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "calendar_provider_configs",
        sa.Column(
            "push_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    op.create_table(
        "calendar_push_channels",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("connection_id", sa.BigInteger(), nullable=False),
        sa.Column("calendar_id", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_channel_id", sa.String(length=512), nullable=True),
        sa.Column("resource_id", sa.String(length=512), nullable=True),
        sa.Column("secret_encrypted", sa.Text(), nullable=True),
        sa.Column("notification_url", sa.String(length=2048), nullable=True),
        sa.Column("expiration", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_error", sa.String(length=512), nullable=True),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["calendar_connections.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["calendar_id"], ["calendar_sources.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "calendar_id",
            "provider",
            name="uq_calendar_push_channel_calendar_provider",
        ),
    )
    op.create_index(
        op.f("ix_calendar_push_channels_connection_id"),
        "calendar_push_channels",
        ["connection_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_calendar_push_channels_calendar_id"),
        "calendar_push_channels",
        ["calendar_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_calendar_push_channels_provider"),
        "calendar_push_channels",
        ["provider"],
        unique=False,
    )
    op.create_index(
        op.f("ix_calendar_push_channels_provider_channel_id"),
        "calendar_push_channels",
        ["provider_channel_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_calendar_push_channels_expiration"),
        "calendar_push_channels",
        ["expiration"],
        unique=False,
    )
    op.create_index(
        op.f("ix_calendar_push_channels_status"),
        "calendar_push_channels",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_calendar_push_channels_status"),
        table_name="calendar_push_channels",
    )
    op.drop_index(
        op.f("ix_calendar_push_channels_expiration"),
        table_name="calendar_push_channels",
    )
    op.drop_index(
        op.f("ix_calendar_push_channels_provider_channel_id"),
        table_name="calendar_push_channels",
    )
    op.drop_index(
        op.f("ix_calendar_push_channels_provider"),
        table_name="calendar_push_channels",
    )
    op.drop_index(
        op.f("ix_calendar_push_channels_calendar_id"),
        table_name="calendar_push_channels",
    )
    op.drop_index(
        op.f("ix_calendar_push_channels_connection_id"),
        table_name="calendar_push_channels",
    )
    op.drop_table("calendar_push_channels")
    op.drop_column("calendar_provider_configs", "push_enabled")
