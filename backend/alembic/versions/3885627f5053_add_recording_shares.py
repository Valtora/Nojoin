"""Add recording_shares.

``recording_shares`` grants a user, or every member of an identity-provider
group, ``view`` or ``edit`` access to a recording. The owner is never a row.
Exactly one of ``user_id`` and ``group_name`` is set; each grantee appears at
most once per recording (partial unique indexes).

No backfill: nothing is shared yet.

Revision ID: 3885627f5053
Revises: 04b28b70a1fb
"""

import sqlalchemy as sa
from alembic import op

revision = "3885627f5053"
down_revision = "04b28b70a1fb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recording_shares",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("recording_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("group_name", sa.String(), nullable=True),
        sa.Column("level", sa.String(length=8), nullable=False),
        sa.Column("created_by_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["recording_id"], ["recordings.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "(user_id IS NULL) <> (group_name IS NULL)",
            name="ck_recording_shares_one_grantee",
        ),
        sa.CheckConstraint(
            "level IN ('view', 'edit')", name="ck_recording_shares_level"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_recording_shares_recording_id", "recording_shares", ["recording_id"]
    )
    op.create_index("ix_recording_shares_user_id", "recording_shares", ["user_id"])
    op.create_index(
        "ix_recording_shares_group_name", "recording_shares", ["group_name"]
    )
    op.create_index(
        "uq_recording_shares_user",
        "recording_shares",
        ["recording_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.create_index(
        "uq_recording_shares_group",
        "recording_shares",
        ["recording_id", "group_name"],
        unique=True,
        postgresql_where=sa.text("group_name IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_recording_shares_group", table_name="recording_shares")
    op.drop_index("uq_recording_shares_user", table_name="recording_shares")
    op.drop_index("ix_recording_shares_group_name", table_name="recording_shares")
    op.drop_index("ix_recording_shares_user_id", table_name="recording_shares")
    op.drop_index("ix_recording_shares_recording_id", table_name="recording_shares")
    op.drop_table("recording_shares")
