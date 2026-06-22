"""add recording audio window manifests

Revision ID: c7f4a9e2d1b3
Revises: 9ac66b1d2f40
Create Date: 2026-05-19 12:15:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7f4a9e2d1b3"
down_revision: Union[str, Sequence[str], None] = "9ac66b1d2f40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "recording_audio_window_manifests",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("recording_id", sa.BigInteger(), nullable=False),
        sa.Column("window_index", sa.BigInteger(), nullable=False),
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("target_window_ms", sa.BigInteger(), nullable=False),
        sa.Column("hop_ms", sa.BigInteger(), nullable=False),
        sa.Column("window_start_ms", sa.BigInteger(), nullable=False),
        sa.Column("window_end_ms", sa.BigInteger(), nullable=False),
        sa.Column("chunk_start_sequence", sa.BigInteger(), nullable=False),
        sa.Column("chunk_end_sequence", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column(
            "is_partial", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "is_sealed", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("processing_run_id", sa.BigInteger(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["processing_run_id"], ["processing_runs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["recording_id"], ["recordings.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recording_id",
            "window_index",
            name="uq_recording_audio_window_manifests_recording_window",
        ),
    )
    op.create_index(
        op.f("ix_recording_audio_window_manifests_public_id"),
        "recording_audio_window_manifests",
        ["public_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_recording_audio_window_manifests_recording_id"),
        "recording_audio_window_manifests",
        ["recording_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_recording_audio_window_manifests_processing_run_id"),
        "recording_audio_window_manifests",
        ["processing_run_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_recording_audio_window_manifests_processing_run_id"),
        table_name="recording_audio_window_manifests",
    )
    op.drop_index(
        op.f("ix_recording_audio_window_manifests_recording_id"),
        table_name="recording_audio_window_manifests",
    )
    op.drop_index(
        op.f("ix_recording_audio_window_manifests_public_id"),
        table_name="recording_audio_window_manifests",
    )
    op.drop_table("recording_audio_window_manifests")
