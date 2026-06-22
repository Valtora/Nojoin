"""constrain invitation roles

Revision ID: 1f2e3d4c5b6a
Revises: 7c1f4e2a9b63
Create Date: 2026-05-30 00:00:02.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1f2e3d4c5b6a"
down_revision: Union[str, Sequence[str], None] = "7c1f4e2a9b63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INVITATION_ROLE_ENUM = sa.Enum("user", "admin", name="invitationrole")


def upgrade() -> None:
    op.execute(
        """
        UPDATE invitations
        SET role = lower(trim(role))
        WHERE role IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE invitations
        SET
            is_revoked = TRUE,
            role = 'user'
        WHERE role IS NULL OR role NOT IN ('user', 'admin')
        """
    )

    bind = op.get_bind()
    INVITATION_ROLE_ENUM.create(bind, checkfirst=True)
    op.alter_column(
        "invitations",
        "role",
        existing_type=sa.String(),
        type_=INVITATION_ROLE_ENUM,
        existing_nullable=False,
        server_default="user",
        postgresql_using="role::invitationrole",
    )


def downgrade() -> None:
    op.alter_column(
        "invitations",
        "role",
        existing_type=INVITATION_ROLE_ENUM,
        type_=sa.String(),
        existing_nullable=False,
        server_default="user",
        postgresql_using="role::text",
    )
    bind = op.get_bind()
    INVITATION_ROLE_ENUM.drop(bind, checkfirst=True)
