"""add meeting uid to recordings

Revision ID: d4b6c8e9f1a2
Revises: af3c1b6e9d41
Create Date: 2026-04-12 22:45:00.000000

"""
from typing import Sequence, Union
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d4b6c8e9f1a2"
down_revision: Union[str, Sequence[str], None] = "af3c1b6e9d41"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("recordings", sa.Column("meeting_uid", sa.String(length=36), nullable=True))

    bind = op.get_bind()
    existing_recordings = bind.execute(sa.text("SELECT id FROM recordings WHERE meeting_uid IS NULL")).fetchall()
    for recording_id, in existing_recordings:
        bind.execute(
            sa.text(
                "UPDATE recordings SET meeting_uid = :meeting_uid WHERE id = :recording_id"
            ),
            {"meeting_uid": str(uuid4()), "recording_id": recording_id},
        )

    op.alter_column("recordings", "meeting_uid", existing_type=sa.String(length=36), nullable=False)
    op.create_index(op.f("ix_recordings_meeting_uid"), "recordings", ["meeting_uid"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_recordings_meeting_uid"), table_name="recordings")
    op.drop_column("recordings", "meeting_uid")