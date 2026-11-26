"""Add OpenID Connect identity columns to users.

``auth_source`` says how an account signs in: ``local`` (password, the default
for every existing account) or ``oidc`` (created by the identity provider, no
usable password). ``oidc_issuer`` and ``oidc_subject`` hold the identity an
account is linked to, unique together; Postgres treats NULLs as distinct, so
the many unlinked local accounts do not collide. ``email`` and
``display_name`` come from the provider and are shown in the UI only.
``oidc_groups`` is the provider's group list as of the last sign-in.

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
    op.add_column(
        "users",
        sa.Column(
            "auth_source",
            sa.String(length=16),
            nullable=False,
            server_default="local",
        ),
    )
    op.add_column("users", sa.Column("oidc_issuer", sa.String(), nullable=True))
    op.add_column("users", sa.Column("oidc_subject", sa.String(), nullable=True))
    op.add_column("users", sa.Column("email", sa.String(), nullable=True))
    op.add_column("users", sa.Column("display_name", sa.String(), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "oidc_groups",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )
    op.create_unique_constraint(
        "uq_users_oidc_identity", "users", ["oidc_issuer", "oidc_subject"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_users_oidc_identity", "users", type_="unique")
    op.drop_column("users", "oidc_groups")
    op.drop_column("users", "display_name")
    op.drop_column("users", "email")
    op.drop_column("users", "oidc_subject")
    op.drop_column("users", "oidc_issuer")
    op.drop_column("users", "auth_source")
