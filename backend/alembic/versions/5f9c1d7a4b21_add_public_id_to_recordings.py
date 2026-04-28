"""add public id to recordings

Revision ID: 5f9c1d7a4b21
Revises: f13c7b2a9d10
Create Date: 2026-04-28 12:00:00.000000

"""
from typing import Sequence, Union
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "5f9c1d7a4b21"
down_revision: Union[str, Sequence[str], None] = "f13c7b2a9d10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("recordings", sa.Column("public_id", sa.String(length=36), nullable=True))

    bind = op.get_bind()
    existing_recordings = bind.execute(
        sa.text("SELECT id FROM recordings WHERE public_id IS NULL OR public_id = ''")
    ).fetchall()
    for recording_id, in existing_recordings:
        bind.execute(
            sa.text(
                "UPDATE recordings SET public_id = :public_id WHERE id = :recording_id"
            ),
            {"public_id": str(uuid4()), "recording_id": recording_id},
        )

    op.alter_column("recordings", "public_id", existing_type=sa.String(length=36), nullable=False)
    op.create_index(op.f("ix_recordings_public_id"), "recordings", ["public_id"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_recordings_public_id"), table_name="recordings")
    op.drop_column("recordings", "public_id")
