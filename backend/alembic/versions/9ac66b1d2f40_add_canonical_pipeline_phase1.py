"""add canonical pipeline phase 1

Revision ID: 9ac66b1d2f40
Revises: e6c2d7f8a901
Create Date: 2026-05-19 00:45:00.000000
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "9ac66b1d2f40"
down_revision: Union[str, Sequence[str], None] = "e6c2d7f8a901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


processing_run_kind_enum = postgresql.ENUM(
    "live",
    "catch_up",
    "finalize",
    "reprocess",
    "import",
    "backfill",
    name="processingrunkind",
    create_type=False,
)
processing_run_status_enum = postgresql.ENUM(
    "pending",
    "running",
    "completed",
    "failed",
    "cancelled",
    name="processingrunstatus",
    create_type=False,
)
transcript_utterance_state_enum = postgresql.ENUM(
    "provisional",
    "stable",
    "superseded",
    "finalized",
    "deleted",
    name="transcriptutterancestate",
    create_type=False,
)
recording_speaker_alias_type_enum = postgresql.ENUM(
    "diarization_label",
    "live_label",
    "manual_label",
    "display_name",
    "global_name",
    "import_label",
    name="recordingspeakeraliastype",
    create_type=False,
)
speaker_correction_scope_enum = postgresql.ENUM(
    "utterance_only",
    "speaker_everywhere_in_recording",
    "from_this_utterance_forward",
    "merge_into_speaker",
    name="speakercorrectionscope",
    create_type=False,
)
speaker_correction_event_type_enum = postgresql.ENUM(
    "rename",
    "assign_utterance",
    "assign_recording_speaker",
    "assign_from_now_on",
    "merge_speakers",
    "link_global_speaker",
    "promote_global_speaker",
    name="speakercorrectioneventtype",
    create_type=False,
)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _segment_to_ms(value: Any) -> int:
    return int(round(float(value or 0.0) * 1000.0))


def _normalize_name(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _label_like(value: str) -> bool:
    if value.startswith("LIVE_"):
        return True
    if value.startswith("SPEAKER_"):
        return True
    if value.startswith("MANUAL_"):
        return True
    return False


def _alias_type_for_value(value: str) -> str:
    if value.startswith("LIVE_"):
        return "live_label"
    if value.startswith("MANUAL_"):
        return "manual_label"
    if value.startswith("SPEAKER_"):
        return "diarization_label"
    return "import_label"


def _speaker_kind_for_label(label: str, source_segment: dict[str, Any]) -> str:
    if label.startswith("LIVE_"):
        return "live"
    if label.startswith("MANUAL_"):
        return "manual"
    if str(source_segment.get("segment_source") or "") == "import":
        return "imported"
    return "automated"


def _segments_overlap(first: dict[str, Any], second: dict[str, Any]) -> bool:
    first_start = float(first.get("start", 0.0))
    first_end = float(first.get("end", 0.0))
    second_start = float(second.get("start", 0.0))
    second_end = float(second.get("end", 0.0))
    return first_start < second_end and second_start < first_end


def _build_overlap_groups(segments: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    adjacency: dict[int, set[int]] = {}
    for index in range(len(segments)):
        adjacency[index] = set()

    for index, segment in enumerate(segments):
        for other_index in range(index + 1, len(segments)):
            other_segment = segments[other_index]
            if _segments_overlap(segment, other_segment):
                adjacency[index].add(other_index)
                adjacency[other_index].add(index)

    groups: dict[int, dict[str, Any]] = {}
    visited: set[int] = set()
    for index in range(len(segments)):
        if index in visited or not adjacency.get(index):
            continue
        if not adjacency[index]:
            continue
        stack = [index]
        members: list[int] = []
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            members.append(current)
            stack.extend(adjacency.get(current, []))
        members.sort(key=lambda item: (float(segments[item].get("start", 0.0)), item))
        group_id = str(uuid4())
        for rank, member in enumerate(members):
            groups[member] = {"group_id": group_id, "rank": rank, "members": members}
    return groups


def _projection_overlap_labels(
    segments: list[dict[str, Any]],
    index: int,
    overlap_groups: dict[int, dict[str, Any]],
) -> list[str]:
    existing = list(segments[index].get("overlapping_speakers") or [])
    if existing:
        return existing
    overlap_group = overlap_groups.get(index)
    if not overlap_group:
        return []
    labels: list[str] = []
    current_label = str(segments[index].get("speaker") or "UNKNOWN")
    for member in overlap_group["members"]:
        if member == index:
            continue
        other_label = str(segments[member].get("speaker") or "UNKNOWN")
        if other_label != current_label and other_label not in labels:
            labels.append(other_label)
    return labels


def _segment_state(recording_status: str, segment: dict[str, Any]) -> str:
    if segment.get("provisional") is True:
        return "provisional"
    if recording_status == "PROCESSED":
        return "finalized"
    return "stable"


def _speaker_matches(row: dict[str, Any], speaker_value: str) -> bool:
    if row["diarization_label"] == speaker_value:
        return True
    normalized_value = _normalize_name(speaker_value)
    if normalized_value and _normalize_name(row.get("local_name")) == normalized_value:
        return True
    if normalized_value and _normalize_name(row.get("name")) == normalized_value:
        return True
    if normalized_value and _normalize_name(row.get("global_name")) == normalized_value:
        return True
    return False


def _insert_alias_rows(
    bind,
    aliases_table,
    *,
    recording_speaker_id: int,
    diarization_label: str | None,
    local_name: str | None,
    name: str | None,
    global_name: str | None,
    source_run_id: int | None,
) -> None:
    now = _utcnow()
    seen: set[tuple[str, str]] = set()
    candidates: list[tuple[str, str]] = []
    if diarization_label:
        candidates.append((_alias_type_for_value(diarization_label), diarization_label))
    if local_name:
        candidates.append(("display_name", local_name))
    if name and name != local_name:
        candidates.append(("display_name", name))
    if global_name:
        candidates.append(("global_name", global_name))

    for alias_type, alias_value in candidates:
        if not alias_value or (alias_type, alias_value) in seen:
            continue
        seen.add((alias_type, alias_value))
        bind.execute(
            aliases_table.insert().values(
                created_at=now,
                updated_at=now,
                recording_speaker_id=recording_speaker_id,
                alias_type=alias_type,
                alias_value=alias_value,
                source_run_id=source_run_id,
                active=True,
                valid_from_ms=None,
                valid_to_ms=None,
                confidence=None,
            )
        )


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in (
        processing_run_kind_enum,
        processing_run_status_enum,
        transcript_utterance_state_enum,
        recording_speaker_alias_type_enum,
        speaker_correction_scope_enum,
        speaker_correction_event_type_enum,
    ):
        enum_type.create(bind, checkfirst=True)

    op.add_column(
        "recording_speakers",
        sa.Column("public_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "recording_speakers",
        sa.Column(
            "speaker_status",
            sa.String(),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
    )
    op.add_column(
        "recording_speakers",
        sa.Column(
            "speaker_kind",
            sa.String(),
            nullable=False,
            server_default=sa.text("'automated'"),
        ),
    )
    op.add_column(
        "recording_speakers", sa.Column("first_seen_ms", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "recording_speakers", sa.Column("last_seen_ms", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "recording_speakers",
        sa.Column("identity_confidence", sa.Float(), nullable=True),
    )
    op.add_column(
        "recording_speakers",
        sa.Column(
            "identity_locked",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    recording_audio_chunks = op.create_table(
        "recording_audio_chunks",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("recording_id", sa.BigInteger(), nullable=False),
        sa.Column("sequence_no", sa.BigInteger(), nullable=False),
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("absolute_start_ms", sa.BigInteger(), nullable=False),
        sa.Column("absolute_end_ms", sa.BigInteger(), nullable=False),
        sa.Column("duration_ms", sa.BigInteger(), nullable=False),
        sa.Column("sample_rate_hz", sa.BigInteger(), nullable=False),
        sa.Column("channel_count", sa.BigInteger(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=128), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("upload_status", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("cleanup_eligible_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["recording_id"], ["recordings.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recording_id",
            "sequence_no",
            name="uq_recording_audio_chunks_recording_sequence",
        ),
        sa.UniqueConstraint(
            "recording_id",
            "idempotency_key",
            name="uq_recording_audio_chunks_recording_idempotency",
        ),
    )
    op.create_index(
        op.f("ix_recording_audio_chunks_public_id"),
        "recording_audio_chunks",
        ["public_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_recording_audio_chunks_recording_id"),
        "recording_audio_chunks",
        ["recording_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_recording_audio_chunks_idempotency_key"),
        "recording_audio_chunks",
        ["idempotency_key"],
        unique=False,
    )

    processing_runs = op.create_table(
        "processing_runs",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("recording_id", sa.BigInteger(), nullable=False),
        sa.Column("parent_run_id", sa.BigInteger(), nullable=True),
        sa.Column("run_kind", processing_run_kind_enum, nullable=False),
        sa.Column("trigger_source", sa.String(), nullable=False),
        sa.Column("requested_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("status", processing_run_status_enum, nullable=False),
        sa.Column("config_hash", sa.String(length=255), nullable=True),
        sa.Column("transcription_backend", sa.String(), nullable=True),
        sa.Column("diarization_backend", sa.String(), nullable=True),
        sa.Column(
            "model_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("span_start_ms", sa.BigInteger(), nullable=True),
        sa.Column("span_end_ms", sa.BigInteger(), nullable=True),
        sa.Column("reused_live_asr", sa.Boolean(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["parent_run_id"], ["processing_runs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["recording_id"], ["recordings.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recording_id",
            "idempotency_key",
            name="uq_processing_runs_recording_idempotency",
        ),
    )
    op.create_index(
        op.f("ix_processing_runs_public_id"),
        "processing_runs",
        ["public_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_processing_runs_recording_id"),
        "processing_runs",
        ["recording_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_processing_runs_idempotency_key"),
        "processing_runs",
        ["idempotency_key"],
        unique=False,
    )

    transcript_utterances = op.create_table(
        "transcript_utterances",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("recording_id", sa.BigInteger(), nullable=False),
        sa.Column("sort_key", sa.String(length=64), nullable=False),
        sa.Column("start_ms", sa.BigInteger(), nullable=False),
        sa.Column("end_ms", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("speaker_label", sa.String(length=255), nullable=True),
        sa.Column("recording_speaker_id", sa.BigInteger(), nullable=True),
        sa.Column("state", transcript_utterance_state_enum, nullable=False),
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("processing_run_id", sa.BigInteger(), nullable=True),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("overlap_group_id", sa.String(length=64), nullable=True),
        sa.Column("overlap_rank", sa.BigInteger(), nullable=False),
        sa.Column("manual_text_locked", sa.Boolean(), nullable=False),
        sa.Column("manual_speaker_locked", sa.Boolean(), nullable=False),
        sa.Column("text_confidence", sa.Float(), nullable=True),
        sa.Column("speaker_confidence", sa.Float(), nullable=True),
        sa.Column(
            "confidence_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["processing_run_id"], ["processing_runs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["recording_id"], ["recordings.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["recording_speaker_id"], ["recording_speakers.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_transcript_utterances_public_id"),
        "transcript_utterances",
        ["public_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_transcript_utterances_recording_id"),
        "transcript_utterances",
        ["recording_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_transcript_utterances_sort_key"),
        "transcript_utterances",
        ["sort_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_transcript_utterances_speaker_label"),
        "transcript_utterances",
        ["speaker_label"],
        unique=False,
    )
    op.create_index(
        op.f("ix_transcript_utterances_recording_speaker_id"),
        "transcript_utterances",
        ["recording_speaker_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_transcript_utterances_processing_run_id"),
        "transcript_utterances",
        ["processing_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_transcript_utterances_overlap_group_id"),
        "transcript_utterances",
        ["overlap_group_id"],
        unique=False,
    )

    transcript_utterance_events = op.create_table(
        "transcript_utterance_events",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("recording_id", sa.BigInteger(), nullable=False),
        sa.Column("utterance_id", sa.BigInteger(), nullable=False),
        sa.Column("processing_run_id", sa.BigInteger(), nullable=True),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("old_values", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("new_values", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("resulting_revision", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["processing_run_id"], ["processing_runs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["recording_id"], ["recordings.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["utterance_id"], ["transcript_utterances.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_transcript_utterance_events_recording_id"),
        "transcript_utterance_events",
        ["recording_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_transcript_utterance_events_utterance_id"),
        "transcript_utterance_events",
        ["utterance_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_transcript_utterance_events_processing_run_id"),
        "transcript_utterance_events",
        ["processing_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_transcript_utterance_events_actor_user_id"),
        "transcript_utterance_events",
        ["actor_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_transcript_utterance_events_event_type"),
        "transcript_utterance_events",
        ["event_type"],
        unique=False,
    )

    recording_speaker_aliases = op.create_table(
        "recording_speaker_aliases",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("recording_speaker_id", sa.BigInteger(), nullable=False),
        sa.Column("alias_type", recording_speaker_alias_type_enum, nullable=False),
        sa.Column("alias_value", sa.String(length=255), nullable=False),
        sa.Column("source_run_id", sa.BigInteger(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("valid_from_ms", sa.BigInteger(), nullable=True),
        sa.Column("valid_to_ms", sa.BigInteger(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["recording_speaker_id"], ["recording_speakers.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id"], ["processing_runs.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_recording_speaker_aliases_recording_speaker_id"),
        "recording_speaker_aliases",
        ["recording_speaker_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_recording_speaker_aliases_alias_value"),
        "recording_speaker_aliases",
        ["alias_value"],
        unique=False,
    )
    op.create_index(
        op.f("ix_recording_speaker_aliases_source_run_id"),
        "recording_speaker_aliases",
        ["source_run_id"],
        unique=False,
    )

    speaker_correction_events = op.create_table(
        "speaker_correction_events",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("recording_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("utterance_id", sa.BigInteger(), nullable=True),
        sa.Column("source_recording_speaker_id", sa.BigInteger(), nullable=True),
        sa.Column("target_recording_speaker_id", sa.BigInteger(), nullable=True),
        sa.Column("target_global_speaker_id", sa.BigInteger(), nullable=True),
        sa.Column("event_type", speaker_correction_event_type_enum, nullable=False),
        sa.Column("scope", speaker_correction_scope_enum, nullable=False),
        sa.Column("effective_from_ms", sa.BigInteger(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["recording_id"], ["recordings.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_recording_speaker_id"],
            ["recording_speakers.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["target_global_speaker_id"], ["global_speakers.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["target_recording_speaker_id"],
            ["recording_speakers.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["utterance_id"], ["transcript_utterances.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_speaker_correction_events_public_id"),
        "speaker_correction_events",
        ["public_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_speaker_correction_events_recording_id"),
        "speaker_correction_events",
        ["recording_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_speaker_correction_events_actor_user_id"),
        "speaker_correction_events",
        ["actor_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_speaker_correction_events_utterance_id"),
        "speaker_correction_events",
        ["utterance_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_speaker_correction_events_source_recording_speaker_id"),
        "speaker_correction_events",
        ["source_recording_speaker_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_speaker_correction_events_target_recording_speaker_id"),
        "speaker_correction_events",
        ["target_recording_speaker_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_speaker_correction_events_target_global_speaker_id"),
        "speaker_correction_events",
        ["target_global_speaker_id"],
        unique=False,
    )

    diarization_window_results = op.create_table(
        "diarization_window_results",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("recording_id", sa.BigInteger(), nullable=False),
        sa.Column("processing_run_id", sa.BigInteger(), nullable=True),
        sa.Column("window_index", sa.BigInteger(), nullable=False),
        sa.Column("window_start_ms", sa.BigInteger(), nullable=False),
        sa.Column("window_end_ms", sa.BigInteger(), nullable=False),
        sa.Column("chunk_start_sequence", sa.BigInteger(), nullable=True),
        sa.Column("chunk_end_sequence", sa.BigInteger(), nullable=True),
        sa.Column("model_name", sa.String(), nullable=True),
        sa.Column("model_version", sa.String(), nullable=True),
        sa.Column("device", sa.String(), nullable=True),
        sa.Column("config_hash", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column(
            "raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["processing_run_id"], ["processing_runs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["recording_id"], ["recordings.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_diarization_window_results_public_id"),
        "diarization_window_results",
        ["public_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_diarization_window_results_recording_id"),
        "diarization_window_results",
        ["recording_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_diarization_window_results_processing_run_id"),
        "diarization_window_results",
        ["processing_run_id"],
        unique=False,
    )

    op.create_table(
        "diarization_window_turns",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("window_result_id", sa.BigInteger(), nullable=False),
        sa.Column("local_speaker_key", sa.String(length=255), nullable=False),
        sa.Column("start_ms", sa.BigInteger(), nullable=False),
        sa.Column("end_ms", sa.BigInteger(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("matched_recording_speaker_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "metadata_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["matched_recording_speaker_id"],
            ["recording_speakers.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["window_result_id"], ["diarization_window_results.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_diarization_window_turns_window_result_id"),
        "diarization_window_turns",
        ["window_result_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_diarization_window_turns_local_speaker_key"),
        "diarization_window_turns",
        ["local_speaker_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_diarization_window_turns_matched_recording_speaker_id"),
        "diarization_window_turns",
        ["matched_recording_speaker_id"],
        unique=False,
    )

    recordings = sa.table(
        "recordings",
        sa.column("id", sa.BigInteger()),
        sa.column("user_id", sa.BigInteger()),
        sa.column("status", sa.String()),
    )
    transcripts = sa.table(
        "transcripts",
        sa.column("id", sa.BigInteger()),
        sa.column("recording_id", sa.BigInteger()),
        sa.column("segments", postgresql.JSONB(astext_type=sa.Text())),
        sa.column("text", sa.Text()),
    )
    global_speakers = sa.table(
        "global_speakers",
        sa.column("id", sa.BigInteger()),
        sa.column("name", sa.String()),
    )
    recording_speakers = sa.table(
        "recording_speakers",
        sa.column("id", sa.BigInteger()),
        sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
        sa.column("recording_id", sa.BigInteger()),
        sa.column("global_speaker_id", sa.BigInteger()),
        sa.column("diarization_label", sa.String()),
        sa.column("local_name", sa.String()),
        sa.column("name", sa.String()),
        sa.column("merged_into_id", sa.BigInteger()),
        sa.column("public_id", sa.String()),
        sa.column("speaker_status", sa.String()),
        sa.column("speaker_kind", sa.String()),
        sa.column("first_seen_ms", sa.BigInteger()),
        sa.column("last_seen_ms", sa.BigInteger()),
        sa.column("identity_confidence", sa.Float()),
        sa.column("identity_locked", sa.Boolean()),
    )

    speaker_rows = (
        bind.execute(
            sa.select(
                recording_speakers.c.id,
                recording_speakers.c.global_speaker_id,
                recording_speakers.c.diarization_label,
                recording_speakers.c.local_name,
                recording_speakers.c.name,
                recording_speakers.c.merged_into_id,
            )
        )
        .mappings()
        .all()
    )
    for row in speaker_rows:
        bind.execute(
            recording_speakers.update()
            .where(recording_speakers.c.id == row["id"])
            .values(
                public_id=str(uuid4()),
                speaker_status="merged" if row["merged_into_id"] else "active",
                speaker_kind=(
                    "live"
                    if str(row["diarization_label"] or "").startswith("LIVE_")
                    else "manual"
                    if str(row["diarization_label"] or "").startswith("MANUAL_")
                    or row["local_name"]
                    else "automated"
                ),
                identity_locked=bool(row["local_name"] or row["global_speaker_id"]),
                updated_at=_utcnow(),
            )
        )

    processed_recordings = (
        bind.execute(
            sa.select(
                recordings.c.id.label("recording_id"),
                recordings.c.user_id.label("user_id"),
                recordings.c.status.label("status"),
                transcripts.c.id.label("transcript_id"),
                transcripts.c.segments.label("segments"),
            )
            .select_from(
                recordings.join(
                    transcripts, transcripts.c.recording_id == recordings.c.id
                )
            )
            .where(recordings.c.status == "PROCESSED")
        )
        .mappings()
        .all()
    )

    for recording_row in processed_recordings:
        segments = [
            dict(segment)
            for segment in (recording_row["segments"] or [])
            if isinstance(segment, dict)
        ]
        if not segments:
            continue

        now = _utcnow()
        run_id = bind.execute(
            processing_runs.insert()
            .values(
                created_at=now,
                updated_at=now,
                public_id=str(uuid4()),
                recording_id=recording_row["recording_id"],
                parent_run_id=None,
                run_kind="backfill",
                trigger_source="migration",
                requested_by_user_id=None,
                status="completed",
                config_hash=None,
                transcription_backend=None,
                diarization_backend=None,
                model_metadata=None,
                span_start_ms=_segment_to_ms(
                    min(
                        (segment.get("start", 0.0) for segment in segments), default=0.0
                    )
                ),
                span_end_ms=_segment_to_ms(
                    max((segment.get("end", 0.0) for segment in segments), default=0.0)
                ),
                reused_live_asr=False,
                idempotency_key=None,
                metrics=None,
                error_summary=None,
                started_at=now,
                completed_at=now,
            )
            .returning(processing_runs.c.id)
        ).scalar_one()

        recording_speaker_rows = (
            bind.execute(
                sa.select(
                    recording_speakers.c.id,
                    recording_speakers.c.recording_id,
                    recording_speakers.c.global_speaker_id,
                    recording_speakers.c.diarization_label,
                    recording_speakers.c.local_name,
                    recording_speakers.c.name,
                    recording_speakers.c.public_id,
                    recording_speakers.c.first_seen_ms,
                    recording_speakers.c.last_seen_ms,
                    global_speakers.c.name.label("global_name"),
                )
                .select_from(
                    recording_speakers.outerjoin(
                        global_speakers,
                        recording_speakers.c.global_speaker_id == global_speakers.c.id,
                    )
                )
                .where(
                    recording_speakers.c.recording_id == recording_row["recording_id"]
                )
            )
            .mappings()
            .all()
        )
        speaker_rows_for_recording = [dict(row) for row in recording_speaker_rows]

        overlap_groups = _build_overlap_groups(segments)
        projection_segments: list[dict[str, Any]] = []
        speaker_bounds: dict[int, dict[str, int | None]] = {
            int(row["id"]): {
                "first_seen_ms": row.get("first_seen_ms"),
                "last_seen_ms": row.get("last_seen_ms"),
            }
            for row in speaker_rows_for_recording
        }

        for index, segment in enumerate(segments):
            speaker_value = (
                str(segment.get("speaker") or "UNKNOWN").strip() or "UNKNOWN"
            )
            matched_speaker: dict[str, Any] | None = None
            if speaker_value != "UNKNOWN":
                for row in speaker_rows_for_recording:
                    if _speaker_matches(row, speaker_value):
                        matched_speaker = row
                        break

                if matched_speaker is None:
                    now = _utcnow()
                    diarization_label = (
                        speaker_value
                        if _label_like(speaker_value)
                        else f"MANUAL_{uuid4().hex[:8]}"
                    )
                    local_name = None if _label_like(speaker_value) else speaker_value
                    new_speaker_id = bind.execute(
                        recording_speakers.insert()
                        .values(
                            created_at=now,
                            updated_at=now,
                            recording_id=recording_row["recording_id"],
                            global_speaker_id=None,
                            diarization_label=diarization_label,
                            local_name=local_name,
                            name=None,
                            public_id=str(uuid4()),
                            speaker_status="active",
                            speaker_kind=_speaker_kind_for_label(
                                diarization_label, segment
                            ),
                            first_seen_ms=None,
                            last_seen_ms=None,
                            identity_confidence=None,
                            identity_locked=bool(local_name),
                        )
                        .returning(recording_speakers.c.id)
                    ).scalar_one()
                    matched_speaker = {
                        "id": new_speaker_id,
                        "recording_id": recording_row["recording_id"],
                        "global_speaker_id": None,
                        "diarization_label": diarization_label,
                        "local_name": local_name,
                        "name": None,
                        "public_id": None,
                        "first_seen_ms": None,
                        "last_seen_ms": None,
                        "global_name": None,
                    }
                    speaker_rows_for_recording.append(matched_speaker)
                    speaker_bounds[int(new_speaker_id)] = {
                        "first_seen_ms": None,
                        "last_seen_ms": None,
                    }

            start_ms = _segment_to_ms(segment.get("start", 0.0))
            end_ms = _segment_to_ms(segment.get("end", 0.0))
            if matched_speaker is not None:
                bounds = speaker_bounds[int(matched_speaker["id"])]
                if bounds["first_seen_ms"] is None or start_ms < int(
                    bounds["first_seen_ms"]
                ):
                    bounds["first_seen_ms"] = start_ms
                if bounds["last_seen_ms"] is None or end_ms > int(
                    bounds["last_seen_ms"]
                ):
                    bounds["last_seen_ms"] = end_ms

            overlap_group = overlap_groups.get(index, {})
            utterance_public_id = str(segment.get("id") or uuid4())
            revision_value = int(segment.get("revision") or 1)
            utterance_id = bind.execute(
                transcript_utterances.insert()
                .values(
                    created_at=now,
                    updated_at=now,
                    public_id=utterance_public_id,
                    recording_id=recording_row["recording_id"],
                    sort_key=f"{index:012d}",
                    start_ms=start_ms,
                    end_ms=end_ms,
                    text=str(segment.get("text", "") or ""),
                    speaker_label=(
                        matched_speaker["diarization_label"]
                        if matched_speaker
                        else speaker_value
                    ),
                    recording_speaker_id=(
                        matched_speaker["id"] if matched_speaker else None
                    ),
                    state=_segment_state(recording_row["status"], segment),
                    source_kind=str(segment.get("segment_source") or "legacy"),
                    processing_run_id=run_id,
                    revision=revision_value,
                    overlap_group_id=overlap_group.get("group_id"),
                    overlap_rank=overlap_group.get("rank", 0),
                    manual_text_locked=bool(
                        segment.get("text_manually_edited") is True
                    ),
                    manual_speaker_locked=bool(
                        segment.get("speaker_manually_edited") is True
                    ),
                    text_confidence=float(segment["text_confidence"])
                    if segment.get("text_confidence") is not None
                    else None,
                    speaker_confidence=float(segment["speaker_confidence"])
                    if segment.get("speaker_confidence") is not None
                    else None,
                    confidence_payload=None,
                )
                .returning(transcript_utterances.c.id)
            ).scalar_one()

            bind.execute(
                transcript_utterance_events.insert().values(
                    created_at=now,
                    updated_at=now,
                    recording_id=recording_row["recording_id"],
                    utterance_id=utterance_id,
                    processing_run_id=run_id,
                    actor_user_id=None,
                    event_type="backfilled",
                    source="migration_backfill",
                    old_values=None,
                    new_values={
                        "start_ms": start_ms,
                        "end_ms": end_ms,
                        "text": str(segment.get("text", "") or ""),
                        "speaker": matched_speaker["diarization_label"]
                        if matched_speaker
                        else speaker_value,
                    },
                    resulting_revision=revision_value,
                )
            )

            projection_segment = dict(segment)
            projection_segment.update(
                {
                    "id": utterance_public_id,
                    "start": start_ms / 1000.0,
                    "end": end_ms / 1000.0,
                    "text": str(segment.get("text", "") or ""),
                    "speaker": matched_speaker["diarization_label"]
                    if matched_speaker
                    else speaker_value,
                    "overlapping_speakers": _projection_overlap_labels(
                        segments, index, overlap_groups
                    ),
                    "provisional": _segment_state(recording_row["status"], segment)
                    == "provisional",
                    "segment_source": segment.get("segment_source") or "legacy",
                    "speaker_manually_edited": bool(
                        segment.get("speaker_manually_edited") is True
                    ),
                    "text_manually_edited": bool(
                        segment.get("text_manually_edited") is True
                    ),
                    "revision": revision_value,
                    "recording_speaker_id": matched_speaker["id"]
                    if matched_speaker
                    else None,
                    "state": _segment_state(recording_row["status"], segment),
                    "speaker_confidence": float(segment["speaker_confidence"])
                    if segment.get("speaker_confidence") is not None
                    else None,
                    "text_confidence": float(segment["text_confidence"])
                    if segment.get("text_confidence") is not None
                    else None,
                    "updated_at": now.isoformat(),
                }
            )
            projection_segments.append(projection_segment)

        bind.execute(
            transcripts.update()
            .where(transcripts.c.id == recording_row["transcript_id"])
            .values(
                segments=projection_segments,
                text=" ".join(
                    str(segment.get("text", "") or "")
                    for segment in projection_segments
                ).strip(),
            )
        )

        for speaker_id, bounds in speaker_bounds.items():
            bind.execute(
                recording_speakers.update()
                .where(recording_speakers.c.id == speaker_id)
                .values(
                    first_seen_ms=bounds["first_seen_ms"],
                    last_seen_ms=bounds["last_seen_ms"],
                    updated_at=_utcnow(),
                )
            )

    alias_rows = (
        bind.execute(
            sa.select(
                recording_speakers.c.id,
                recording_speakers.c.diarization_label,
                recording_speakers.c.local_name,
                recording_speakers.c.name,
                global_speakers.c.name.label("global_name"),
            ).select_from(
                recording_speakers.outerjoin(
                    global_speakers,
                    recording_speakers.c.global_speaker_id == global_speakers.c.id,
                )
            )
        )
        .mappings()
        .all()
    )
    for row in alias_rows:
        _insert_alias_rows(
            bind,
            recording_speaker_aliases,
            recording_speaker_id=row["id"],
            diarization_label=row["diarization_label"],
            local_name=row["local_name"],
            name=row["name"],
            global_name=row["global_name"],
            source_run_id=None,
        )

    op.alter_column(
        "recording_speakers",
        "public_id",
        existing_type=sa.String(length=36),
        nullable=False,
    )
    op.create_index(
        op.f("ix_recording_speakers_public_id"),
        "recording_speakers",
        ["public_id"],
        unique=True,
    )
    op.alter_column("recording_speakers", "speaker_status", server_default=None)
    op.alter_column("recording_speakers", "speaker_kind", server_default=None)
    op.alter_column("recording_speakers", "identity_locked", server_default=None)


def downgrade() -> None:
    op.drop_index(
        op.f("ix_recording_speakers_public_id"), table_name="recording_speakers"
    )

    op.drop_index(
        op.f("ix_diarization_window_turns_matched_recording_speaker_id"),
        table_name="diarization_window_turns",
    )
    op.drop_index(
        op.f("ix_diarization_window_turns_local_speaker_key"),
        table_name="diarization_window_turns",
    )
    op.drop_index(
        op.f("ix_diarization_window_turns_window_result_id"),
        table_name="diarization_window_turns",
    )
    op.drop_table("diarization_window_turns")

    op.drop_index(
        op.f("ix_diarization_window_results_processing_run_id"),
        table_name="diarization_window_results",
    )
    op.drop_index(
        op.f("ix_diarization_window_results_recording_id"),
        table_name="diarization_window_results",
    )
    op.drop_index(
        op.f("ix_diarization_window_results_public_id"),
        table_name="diarization_window_results",
    )
    op.drop_table("diarization_window_results")

    op.drop_index(
        op.f("ix_speaker_correction_events_target_global_speaker_id"),
        table_name="speaker_correction_events",
    )
    op.drop_index(
        op.f("ix_speaker_correction_events_target_recording_speaker_id"),
        table_name="speaker_correction_events",
    )
    op.drop_index(
        op.f("ix_speaker_correction_events_source_recording_speaker_id"),
        table_name="speaker_correction_events",
    )
    op.drop_index(
        op.f("ix_speaker_correction_events_utterance_id"),
        table_name="speaker_correction_events",
    )
    op.drop_index(
        op.f("ix_speaker_correction_events_actor_user_id"),
        table_name="speaker_correction_events",
    )
    op.drop_index(
        op.f("ix_speaker_correction_events_recording_id"),
        table_name="speaker_correction_events",
    )
    op.drop_index(
        op.f("ix_speaker_correction_events_public_id"),
        table_name="speaker_correction_events",
    )
    op.drop_table("speaker_correction_events")

    op.drop_index(
        op.f("ix_recording_speaker_aliases_source_run_id"),
        table_name="recording_speaker_aliases",
    )
    op.drop_index(
        op.f("ix_recording_speaker_aliases_alias_value"),
        table_name="recording_speaker_aliases",
    )
    op.drop_index(
        op.f("ix_recording_speaker_aliases_recording_speaker_id"),
        table_name="recording_speaker_aliases",
    )
    op.drop_table("recording_speaker_aliases")

    op.drop_index(
        op.f("ix_transcript_utterance_events_event_type"),
        table_name="transcript_utterance_events",
    )
    op.drop_index(
        op.f("ix_transcript_utterance_events_actor_user_id"),
        table_name="transcript_utterance_events",
    )
    op.drop_index(
        op.f("ix_transcript_utterance_events_processing_run_id"),
        table_name="transcript_utterance_events",
    )
    op.drop_index(
        op.f("ix_transcript_utterance_events_utterance_id"),
        table_name="transcript_utterance_events",
    )
    op.drop_index(
        op.f("ix_transcript_utterance_events_recording_id"),
        table_name="transcript_utterance_events",
    )
    op.drop_table("transcript_utterance_events")

    op.drop_index(
        op.f("ix_transcript_utterances_overlap_group_id"),
        table_name="transcript_utterances",
    )
    op.drop_index(
        op.f("ix_transcript_utterances_processing_run_id"),
        table_name="transcript_utterances",
    )
    op.drop_index(
        op.f("ix_transcript_utterances_recording_speaker_id"),
        table_name="transcript_utterances",
    )
    op.drop_index(
        op.f("ix_transcript_utterances_speaker_label"),
        table_name="transcript_utterances",
    )
    op.drop_index(
        op.f("ix_transcript_utterances_sort_key"), table_name="transcript_utterances"
    )
    op.drop_index(
        op.f("ix_transcript_utterances_recording_id"),
        table_name="transcript_utterances",
    )
    op.drop_index(
        op.f("ix_transcript_utterances_public_id"), table_name="transcript_utterances"
    )
    op.drop_table("transcript_utterances")

    op.drop_index(
        op.f("ix_processing_runs_idempotency_key"), table_name="processing_runs"
    )
    op.drop_index(op.f("ix_processing_runs_recording_id"), table_name="processing_runs")
    op.drop_index(op.f("ix_processing_runs_public_id"), table_name="processing_runs")
    op.drop_table("processing_runs")

    op.drop_index(
        op.f("ix_recording_audio_chunks_idempotency_key"),
        table_name="recording_audio_chunks",
    )
    op.drop_index(
        op.f("ix_recording_audio_chunks_recording_id"),
        table_name="recording_audio_chunks",
    )
    op.drop_index(
        op.f("ix_recording_audio_chunks_public_id"), table_name="recording_audio_chunks"
    )
    op.drop_table("recording_audio_chunks")

    op.drop_column("recording_speakers", "identity_locked")
    op.drop_column("recording_speakers", "identity_confidence")
    op.drop_column("recording_speakers", "last_seen_ms")
    op.drop_column("recording_speakers", "first_seen_ms")
    op.drop_column("recording_speakers", "speaker_kind")
    op.drop_column("recording_speakers", "speaker_status")
    op.drop_column("recording_speakers", "public_id")

    bind = op.get_bind()
    for enum_type in (
        speaker_correction_event_type_enum,
        speaker_correction_scope_enum,
        recording_speaker_alias_type_enum,
        transcript_utterance_state_enum,
        processing_run_status_enum,
        processing_run_kind_enum,
    ):
        enum_type.drop(bind, checkfirst=True)
