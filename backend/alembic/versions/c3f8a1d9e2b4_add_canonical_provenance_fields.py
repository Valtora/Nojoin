"""add canonical provenance fields

Revision ID: c3f8a1d9e2b4
Revises: 9ac66b1d2f40
Create Date: 2026-05-19 22:40:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3f8a1d9e2b4"
down_revision: Union[str, Sequence[str], None] = "9ac66b1d2f40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("recording_speakers", sa.Column("processing_run_id", sa.BigInteger(), nullable=True))
    op.add_column("recording_speakers", sa.Column("last_speaker_correction_event_id", sa.BigInteger(), nullable=True))
    op.add_column("recording_speakers", sa.Column("last_diarization_window_result_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_recording_speakers_processing_run_id",
        "recording_speakers",
        "processing_runs",
        ["processing_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_recording_speakers_last_speaker_correction_event_id",
        "recording_speakers",
        "speaker_correction_events",
        ["last_speaker_correction_event_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_recording_speakers_last_diarization_window_result_id",
        "recording_speakers",
        "diarization_window_results",
        ["last_diarization_window_result_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_recording_speakers_processing_run_id"), "recording_speakers", ["processing_run_id"], unique=False)
    op.create_index(op.f("ix_recording_speakers_last_speaker_correction_event_id"), "recording_speakers", ["last_speaker_correction_event_id"], unique=False)
    op.create_index(op.f("ix_recording_speakers_last_diarization_window_result_id"), "recording_speakers", ["last_diarization_window_result_id"], unique=False)

    op.add_column("transcript_utterances", sa.Column("last_utterance_event_id", sa.BigInteger(), nullable=True))
    op.add_column("transcript_utterances", sa.Column("last_diarization_window_result_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_transcript_utterances_last_utterance_event_id",
        "transcript_utterances",
        "transcript_utterance_events",
        ["last_utterance_event_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_transcript_utterances_last_diarization_window_result_id",
        "transcript_utterances",
        "diarization_window_results",
        ["last_diarization_window_result_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_transcript_utterances_last_utterance_event_id"), "transcript_utterances", ["last_utterance_event_id"], unique=False)
    op.create_index(op.f("ix_transcript_utterances_last_diarization_window_result_id"), "transcript_utterances", ["last_diarization_window_result_id"], unique=False)

    op.execute(
        """
        UPDATE transcript_utterances AS utterances
        SET last_utterance_event_id = latest.event_id
        FROM (
            SELECT utterance_id, MAX(id) AS event_id
            FROM transcript_utterance_events
            GROUP BY utterance_id
        ) AS latest
        WHERE utterances.id = latest.utterance_id
        """
    )

    op.execute(
        """
        UPDATE recording_speakers AS speakers
        SET processing_run_id = latest.run_id
        FROM (
            SELECT recording_id, MAX(id) AS run_id
            FROM processing_runs
            GROUP BY recording_id
        ) AS latest
        WHERE speakers.recording_id = latest.recording_id
          AND speakers.processing_run_id IS NULL
        """
    )

    op.execute(
        """
        UPDATE recording_speakers AS speakers
        SET last_speaker_correction_event_id = latest.event_id
        FROM (
            SELECT speaker_id, MAX(event_id) AS event_id
            FROM (
                SELECT target_recording_speaker_id AS speaker_id, id AS event_id
                FROM speaker_correction_events
                WHERE target_recording_speaker_id IS NOT NULL
                UNION ALL
                SELECT source_recording_speaker_id AS speaker_id, id AS event_id
                FROM speaker_correction_events
                WHERE source_recording_speaker_id IS NOT NULL
            ) AS events
            GROUP BY speaker_id
        ) AS latest
        WHERE speakers.id = latest.speaker_id
        """
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_transcript_utterances_last_diarization_window_result_id"), table_name="transcript_utterances")
    op.drop_index(op.f("ix_transcript_utterances_last_utterance_event_id"), table_name="transcript_utterances")
    op.drop_constraint("fk_transcript_utterances_last_diarization_window_result_id", "transcript_utterances", type_="foreignkey")
    op.drop_constraint("fk_transcript_utterances_last_utterance_event_id", "transcript_utterances", type_="foreignkey")
    op.drop_column("transcript_utterances", "last_diarization_window_result_id")
    op.drop_column("transcript_utterances", "last_utterance_event_id")

    op.drop_index(op.f("ix_recording_speakers_last_diarization_window_result_id"), table_name="recording_speakers")
    op.drop_index(op.f("ix_recording_speakers_last_speaker_correction_event_id"), table_name="recording_speakers")
    op.drop_index(op.f("ix_recording_speakers_processing_run_id"), table_name="recording_speakers")
    op.drop_constraint("fk_recording_speakers_last_diarization_window_result_id", "recording_speakers", type_="foreignkey")
    op.drop_constraint("fk_recording_speakers_last_speaker_correction_event_id", "recording_speakers", type_="foreignkey")
    op.drop_constraint("fk_recording_speakers_processing_run_id", "recording_speakers", type_="foreignkey")
    op.drop_column("recording_speakers", "last_diarization_window_result_id")
    op.drop_column("recording_speakers", "last_speaker_correction_event_id")
    op.drop_column("recording_speakers", "processing_run_id")