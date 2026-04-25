"""add companion credential hash

Revision ID: f13c7b2a9d10
Revises: e0b9f6c21a4d
Create Date: 2026-04-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f13c7b2a9d10"
down_revision: Union[str, Sequence[str], None] = "e0b9f6c21a4d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "companion_pairings",
        sa.Column("companion_credential_hash", sa.String(length=128), nullable=True),
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
                revocation_reason = COALESCE(revocation_reason, 'manual_unpair')
            WHERE status <> 'revoked'
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE companion_pairings
            SET companion_credential_hash = NULL
            WHERE status = 'revoked'
            """
        )
    )


def downgrade() -> None:
    op.drop_column("companion_pairings", "companion_credential_hash")