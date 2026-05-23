"""add_recording_pipeline_generation

Revision ID: f2b6d9a8c4e1
Revises: e5a7c3b1d9f4
Create Date: 2026-05-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f2b6d9a8c4e1"
down_revision: Union[str, Sequence[str], None] = "e5a7c3b1d9f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "recordings",
        sa.Column(
            "pipeline_generation",
            sa.String(length=32),
            nullable=True,
        ),
    )
    op.create_index(
        op.f("ix_recordings_pipeline_generation"),
        "recordings",
        ["pipeline_generation"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_recordings_pipeline_generation"), table_name="recordings")
    op.drop_column("recordings", "pipeline_generation")