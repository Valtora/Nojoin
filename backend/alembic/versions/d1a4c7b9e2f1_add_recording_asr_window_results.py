"""add recording asr window results

Revision ID: d1a4c7b9e2f1
Revises: c7f4a9e2d1b3, c3f8a1d9e2b4
Create Date: 2026-05-20 12:40:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d1a4c7b9e2f1"
down_revision: Union[str, Sequence[str], None] = ("c7f4a9e2d1b3", "c3f8a1d9e2b4")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "recording_asr_window_results",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("recording_id", sa.BigInteger(), nullable=False),
        sa.Column("processing_run_id", sa.BigInteger(), nullable=True),
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("span_start_ms", sa.BigInteger(), nullable=False),
        sa.Column("span_end_ms", sa.BigInteger(), nullable=False),
        sa.Column("chunk_start_sequence", sa.BigInteger(), nullable=True),
        sa.Column("chunk_end_sequence", sa.BigInteger(), nullable=True),
        sa.Column("transcription_backend", sa.String(length=255), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=True),
        sa.Column("config_hash", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("error_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("produced_utterance_public_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["processing_run_id"], ["processing_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["recording_id"], ["recordings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recording_id",
            "idempotency_key",
            name="uq_recording_asr_window_results_recording_idempotency",
        ),
    )
    op.create_index(
        op.f("ix_recording_asr_window_results_public_id"),
        "recording_asr_window_results",
        ["public_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_recording_asr_window_results_recording_id"),
        "recording_asr_window_results",
        ["recording_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_recording_asr_window_results_processing_run_id"),
        "recording_asr_window_results",
        ["processing_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_recording_asr_window_results_idempotency_key"),
        "recording_asr_window_results",
        ["idempotency_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_recording_asr_window_results_idempotency_key"),
        table_name="recording_asr_window_results",
    )
    op.drop_index(
        op.f("ix_recording_asr_window_results_processing_run_id"),
        table_name="recording_asr_window_results",
    )
    op.drop_index(
        op.f("ix_recording_asr_window_results_recording_id"),
        table_name="recording_asr_window_results",
    )
    op.drop_index(
        op.f("ix_recording_asr_window_results_public_id"),
        table_name="recording_asr_window_results",
    )
    op.drop_table("recording_asr_window_results")