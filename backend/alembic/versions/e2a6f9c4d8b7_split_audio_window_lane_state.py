"""split audio window lane state

Revision ID: e2a6f9c4d8b7
Revises: b7c1d9e4a8f2
Create Date: 2026-05-26 18:10:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e2a6f9c4d8b7"
down_revision: Union[str, Sequence[str], None] = "b7c1d9e4a8f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FK_RAWM_ASR_RUN_ID = "fk_rawm_asr_run_id"
FK_RAWM_DIAR_RUN_ID = "fk_rawm_diar_run_id"
FK_RAWM_DIAR_WINDOW_RESULT_ID = "fk_rawm_diar_window_result_id"

IX_RAWM_ASR_RUN_ID = "ix_rawm_asr_run_id"
IX_RAWM_DIAR_RUN_ID = "ix_rawm_diar_run_id"
IX_RAWM_DIAR_WINDOW_RESULT_ID = "ix_rawm_diar_window_result_id"


def upgrade() -> None:
    op.add_column(
        "recording_audio_window_manifests",
        sa.Column(
            "asr_status", sa.String(length=32), nullable=False, server_default="pending"
        ),
    )
    op.add_column(
        "recording_audio_window_manifests",
        sa.Column("asr_processing_run_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "recording_audio_window_manifests",
        sa.Column("asr_last_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "recording_audio_window_manifests",
        sa.Column(
            "diarization_status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "recording_audio_window_manifests",
        sa.Column("diarization_processing_run_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "recording_audio_window_manifests",
        sa.Column("diarization_config_hash", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "recording_audio_window_manifests",
        sa.Column("diarization_window_result_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "recording_audio_window_manifests",
        sa.Column("diarization_last_error", sa.Text(), nullable=True),
    )

    op.create_foreign_key(
        FK_RAWM_ASR_RUN_ID,
        "recording_audio_window_manifests",
        "processing_runs",
        ["asr_processing_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        FK_RAWM_DIAR_RUN_ID,
        "recording_audio_window_manifests",
        "processing_runs",
        ["diarization_processing_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        FK_RAWM_DIAR_WINDOW_RESULT_ID,
        "recording_audio_window_manifests",
        "diarization_window_results",
        ["diarization_window_result_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_index(
        IX_RAWM_ASR_RUN_ID,
        "recording_audio_window_manifests",
        ["asr_processing_run_id"],
        unique=False,
    )
    op.create_index(
        IX_RAWM_DIAR_RUN_ID,
        "recording_audio_window_manifests",
        ["diarization_processing_run_id"],
        unique=False,
    )
    op.create_index(
        IX_RAWM_DIAR_WINDOW_RESULT_ID,
        "recording_audio_window_manifests",
        ["diarization_window_result_id"],
        unique=False,
    )

    op.execute(
        """
        UPDATE recording_audio_window_manifests
        SET
            asr_status = CASE
                WHEN status = 'live_processed' THEN 'live_processed'
                WHEN status = 'catch_up_processed' THEN 'catch_up_processed'
                WHEN status = 'live_processing' THEN 'live_processed'
                ELSE 'pending'
            END,
            asr_processing_run_id = CASE
                WHEN status IN ('live_processed', 'catch_up_processed', 'live_processing')
                    THEN processing_run_id
                ELSE NULL
            END,
            diarization_status = CASE
                WHEN status = 'live_processing' THEN 'processing'
                WHEN status = 'failed' THEN 'failed'
                ELSE 'pending'
            END,
            diarization_processing_run_id = CASE
                WHEN status IN ('live_processing', 'failed') THEN processing_run_id
                ELSE NULL
            END,
            diarization_last_error = CASE
                WHEN status = 'failed' THEN last_error
                ELSE NULL
            END
        """
    )
    op.execute(
        """
        UPDATE recording_audio_window_manifests AS manifests
        SET
            diarization_status = 'processed',
            diarization_processing_run_id = (
                SELECT results.processing_run_id
                FROM diarization_window_results AS results
                WHERE results.recording_id = manifests.recording_id
                    AND results.window_index = manifests.window_index
                    AND results.status = 'completed'
                ORDER BY results.id DESC
                LIMIT 1
            ),
            diarization_config_hash = (
                SELECT results.config_hash
                FROM diarization_window_results AS results
                WHERE results.recording_id = manifests.recording_id
                    AND results.window_index = manifests.window_index
                    AND results.status = 'completed'
                ORDER BY results.id DESC
                LIMIT 1
            ),
            diarization_window_result_id = (
                SELECT results.id
                FROM diarization_window_results AS results
                WHERE results.recording_id = manifests.recording_id
                    AND results.window_index = manifests.window_index
                    AND results.status = 'completed'
                ORDER BY results.id DESC
                LIMIT 1
            ),
            diarization_last_error = NULL
        WHERE EXISTS (
            SELECT 1
            FROM diarization_window_results AS results
            WHERE results.recording_id = manifests.recording_id
                AND results.window_index = manifests.window_index
                AND results.status = 'completed'
        )
        """
    )

    op.alter_column(
        "recording_audio_window_manifests", "asr_status", server_default=None
    )
    op.alter_column(
        "recording_audio_window_manifests", "diarization_status", server_default=None
    )


def downgrade() -> None:
    op.drop_index(
        IX_RAWM_DIAR_WINDOW_RESULT_ID,
        table_name="recording_audio_window_manifests",
    )
    op.drop_index(
        IX_RAWM_DIAR_RUN_ID,
        table_name="recording_audio_window_manifests",
    )
    op.drop_index(
        IX_RAWM_ASR_RUN_ID,
        table_name="recording_audio_window_manifests",
    )

    op.drop_constraint(
        FK_RAWM_DIAR_WINDOW_RESULT_ID,
        "recording_audio_window_manifests",
        type_="foreignkey",
    )
    op.drop_constraint(
        FK_RAWM_DIAR_RUN_ID,
        "recording_audio_window_manifests",
        type_="foreignkey",
    )
    op.drop_constraint(
        FK_RAWM_ASR_RUN_ID,
        "recording_audio_window_manifests",
        type_="foreignkey",
    )

    op.drop_column("recording_audio_window_manifests", "diarization_last_error")
    op.drop_column("recording_audio_window_manifests", "diarization_window_result_id")
    op.drop_column("recording_audio_window_manifests", "diarization_config_hash")
    op.drop_column("recording_audio_window_manifests", "diarization_processing_run_id")
    op.drop_column("recording_audio_window_manifests", "diarization_status")
    op.drop_column("recording_audio_window_manifests", "asr_last_error")
    op.drop_column("recording_audio_window_manifests", "asr_processing_run_id")
    op.drop_column("recording_audio_window_manifests", "asr_status")
