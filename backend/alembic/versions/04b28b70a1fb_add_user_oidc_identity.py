"""Add user_identities, an account's identity at an OpenID Connect provider.

One row per linked account, unique on ``(issuer, subject)`` and on
``user_id``. ``email``, ``display_name`` and ``groups`` are refreshed at every
sign-in through the provider. ``created_account`` marks an account the
provider's first sign-in created, which has no usable password.

No backfill: existing accounts stay local and unlinked.

Revision ID: 04b28b70a1fb
Revises: ed5d42732014
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "04b28b70a1fb"
down_revision = "ed5d42732014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_identities",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("issuer", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column(
            "groups",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("created_account", sa.Boolean(), nullable=False),
        sa.Column("last_sign_in_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
        sa.UniqueConstraint(
            "issuer", "subject", name="uq_user_identities_issuer_subject"
        ),
    )


def downgrade() -> None:
    op.drop_table("user_identities")
