from __future__ import annotations

import hashlib
import os
import wave
from pathlib import Path
from typing import Any

from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session
from sqlmodel import select

from backend.models.pipeline import (
    RecordingAudioChunk,
    RecordingAudioWindowManifest,
    generate_pipeline_public_id,
)
from backend.utils.audio import get_audio_duration
from backend.utils.audio_windows import (
    WINDOW_ASR_STATUS_PENDING,
    WINDOW_DIARIZATION_STATUS_PENDING,
    WINDOW_STATUS_PENDING,
    AudioWindowSpec,
    build_audio_window_specs,
)
from backend.utils.config_manager import config_manager
from backend.utils.recording_storage import recording_upload_temp_dir
from backend.utils.time import utc_now


BROWSER_AUDIO_SEGMENT_SUFFIXES = frozenset({".webm", ".ogg", ".m4a"})
TRANSCODE_FAILED_SUFFIX = ".transcode_failed"
PENDING_TRANSCODE_SUFFIXES = frozenset(
    {*BROWSER_AUDIO_SEGMENT_SUFFIXES, TRANSCODE_FAILED_SUFFIX}
)


def _chunk_idempotency_key(*, source_kind: str, sequence: int, sha256: str) -> str:
    return f"{source_kind}:{sequence}:{sha256}"


def _dialect_name(session: Session) -> str:
    bind = session.get_bind()
    if bind is None:
        return ""
    return str(bind.dialect.name or "").lower()


def _supports_native_upsert(session: Session) -> bool:
    return _dialect_name(session) in {"postgresql", "sqlite"}


def _build_insert_statement(session: Session, table):
    dialect = _dialect_name(session)
    if dialect == "postgresql":
        return postgresql_insert(table)
    if dialect == "sqlite":
        return sqlite_insert(table)
    raise ValueError(f"Unsupported upsert dialect: {dialect}")


def _sha256_for_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _read_wav_chunk_metadata(path: Path) -> tuple[int, int, int]:
    try:
        with wave.open(str(path), "rb") as wav_file:
            sample_rate_hz = int(wav_file.getframerate() or 0)
            channel_count = int(wav_file.getnchannels() or 0)
            frame_count = int(wav_file.getnframes() or 0)
    except (EOFError, OSError, wave.Error):
        return 0, 0, 0

    if sample_rate_hz <= 0:
        return 0, channel_count, 0
    duration_ms = int((frame_count / sample_rate_hz) * 1000)
    return sample_rate_hz, channel_count, duration_ms


def _build_recording_audio_chunk_fields(
    *,
    sequence: int,
    source_kind: str,
    storage_path: Path,
) -> dict[str, Any]:
    sample_rate_hz = 0
    channel_count = 0
    duration_ms = 0
    if storage_path.suffix.lower() == ".wav":
        sample_rate_hz, channel_count, duration_ms = _read_wav_chunk_metadata(storage_path)
    if duration_ms <= 0:
        try:
            duration_ms = int(round(float(get_audio_duration(str(storage_path)) or 0.0) * 1000.0))
        except Exception:  # noqa: BLE001
            duration_ms = 0

    sha256 = _sha256_for_path(storage_path)
    byte_size = storage_path.stat().st_size
    return {
        "sequence_no": sequence,
        "source_kind": source_kind,
        "absolute_start_ms": 0,
        "absolute_end_ms": duration_ms,
        "duration_ms": duration_ms,
        "sample_rate_hz": sample_rate_hz,
        "channel_count": channel_count,
        "byte_size": byte_size,
        "sha256": sha256,
        "storage_path": str(storage_path),
        "upload_status": "received",
        "idempotency_key": _chunk_idempotency_key(
            source_kind=source_kind,
            sequence=sequence,
            sha256=sha256,
        ),
        "received_at": utc_now(),
        "cleanup_eligible_at": None,
    }


def _upsert_recording_audio_chunks(
    session: Session,
    *,
    recording_id: int,
    source_kind: str,
    chunk_payloads: list[dict[str, Any]],
) -> list[RecordingAudioChunk]:
    if not chunk_payloads:
        return []

    table = RecordingAudioChunk.__table__
    insert_stmt = _build_insert_statement(session, table).values(chunk_payloads)
    excluded = insert_stmt.excluded
    update_columns = {
        "updated_at": excluded.updated_at,
        "source_kind": excluded.source_kind,
        "absolute_start_ms": excluded.absolute_start_ms,
        "absolute_end_ms": excluded.absolute_end_ms,
        "duration_ms": excluded.duration_ms,
        "sample_rate_hz": excluded.sample_rate_hz,
        "channel_count": excluded.channel_count,
        "byte_size": excluded.byte_size,
        "sha256": excluded.sha256,
        "storage_path": excluded.storage_path,
        "upload_status": excluded.upload_status,
        "idempotency_key": excluded.idempotency_key,
        "received_at": excluded.received_at,
        "cleanup_eligible_at": excluded.cleanup_eligible_at,
    }
    dialect = _dialect_name(session)
    if dialect == "postgresql":
        statement = insert_stmt.on_conflict_do_update(
            constraint="uq_recording_audio_chunks_recording_sequence",
            set_=update_columns,
        )
    else:
        statement = insert_stmt.on_conflict_do_update(
            index_elements=["recording_id", "sequence_no"],
            set_=update_columns,
        )

    session.execute(statement)
    return list_recording_audio_chunks(
        session,
        recording_id,
        source_kind=source_kind,
    )


def _window_row_signature(row: RecordingAudioWindowManifest) -> tuple[Any, ...]:
    return (
        int(row.window_start_ms),
        int(row.window_end_ms),
        int(row.chunk_start_sequence),
        int(row.chunk_end_sequence),
        int(row.target_window_ms),
        int(row.hop_ms),
        bool(row.is_partial),
        bool(row.is_sealed),
        str(row.source_kind or "browser"),
    )


def _window_spec_signature(spec: AudioWindowSpec) -> tuple[Any, ...]:
    return (
        int(spec.window_start_ms),
        int(spec.window_end_ms),
        int(spec.chunk_start_sequence),
        int(spec.chunk_end_sequence),
        int(spec.target_window_ms),
        int(spec.hop_ms),
        bool(spec.is_partial),
        bool(spec.is_sealed),
        str(spec.source_kind or "browser"),
    )


def _build_window_manifest_payloads(
    *,
    recording_id: int,
    existing_rows: list[RecordingAudioWindowManifest],
    window_specs: list[AudioWindowSpec],
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    existing_by_index = {int(row.window_index): row for row in existing_rows}

    for spec in window_specs:
        row = existing_by_index.get(spec.window_index)
        now = utc_now()

        base_payload = {
            "recording_id": recording_id,
            "window_index": spec.window_index,
            "source_kind": spec.source_kind,
            "target_window_ms": spec.target_window_ms,
            "hop_ms": spec.hop_ms,
            "window_start_ms": spec.window_start_ms,
            "window_end_ms": spec.window_end_ms,
            "chunk_start_sequence": spec.chunk_start_sequence,
            "chunk_end_sequence": spec.chunk_end_sequence,
            "is_partial": spec.is_partial,
            "is_sealed": spec.is_sealed,
            "updated_at": now,
        }

        if row is None:
            payloads.append(
                {
                    **base_payload,
                    "created_at": now,
                    "public_id": generate_pipeline_public_id(),
                    "status": WINDOW_STATUS_PENDING,
                    "asr_status": WINDOW_ASR_STATUS_PENDING,
                    "asr_processing_run_id": None,
                    "asr_last_error": None,
                    "diarization_status": WINDOW_DIARIZATION_STATUS_PENDING,
                    "diarization_processing_run_id": None,
                    "diarization_config_hash": None,
                    "diarization_window_result_id": None,
                    "diarization_last_error": None,
                    "processing_run_id": None,
                    "last_error": None,
                }
            )
            continue

        signature_changed = _window_row_signature(row) != _window_spec_signature(spec)
        payload = {
            **base_payload,
            "created_at": row.created_at,
            "public_id": row.public_id,
        }
        if signature_changed:
            payload.update(
                {
                    "status": WINDOW_STATUS_PENDING,
                    "asr_status": WINDOW_ASR_STATUS_PENDING,
                    "asr_processing_run_id": None,
                    "asr_last_error": None,
                    "diarization_status": WINDOW_DIARIZATION_STATUS_PENDING,
                    "diarization_processing_run_id": None,
                    "diarization_config_hash": None,
                    "diarization_window_result_id": None,
                    "diarization_last_error": None,
                    "processing_run_id": None,
                    "last_error": None,
                }
            )
        else:
            payload.update(
                {
                    "status": row.status,
                    "asr_status": row.asr_status,
                    "asr_processing_run_id": row.asr_processing_run_id,
                    "asr_last_error": row.asr_last_error,
                    "diarization_status": row.diarization_status,
                    "diarization_processing_run_id": row.diarization_processing_run_id,
                    "diarization_config_hash": row.diarization_config_hash,
                    "diarization_window_result_id": row.diarization_window_result_id,
                    "diarization_last_error": row.diarization_last_error,
                    "processing_run_id": row.processing_run_id,
                    "last_error": row.last_error,
                }
            )
        payloads.append(payload)

    return payloads


def _upsert_window_manifests(
    session: Session,
    *,
    recording_id: int,
    manifest_payloads: list[dict[str, Any]],
) -> list[RecordingAudioWindowManifest]:
    if not manifest_payloads:
        return []

    table = RecordingAudioWindowManifest.__table__
    insert_stmt = _build_insert_statement(session, table).values(manifest_payloads)
    excluded = insert_stmt.excluded
    update_columns = {
        "updated_at": excluded.updated_at,
        "source_kind": excluded.source_kind,
        "target_window_ms": excluded.target_window_ms,
        "hop_ms": excluded.hop_ms,
        "window_start_ms": excluded.window_start_ms,
        "window_end_ms": excluded.window_end_ms,
        "chunk_start_sequence": excluded.chunk_start_sequence,
        "chunk_end_sequence": excluded.chunk_end_sequence,
        "status": excluded.status,
        "asr_status": excluded.asr_status,
        "asr_processing_run_id": excluded.asr_processing_run_id,
        "asr_last_error": excluded.asr_last_error,
        "diarization_status": excluded.diarization_status,
        "diarization_processing_run_id": excluded.diarization_processing_run_id,
        "diarization_config_hash": excluded.diarization_config_hash,
        "diarization_window_result_id": excluded.diarization_window_result_id,
        "diarization_last_error": excluded.diarization_last_error,
        "is_partial": excluded.is_partial,
        "is_sealed": excluded.is_sealed,
        "processing_run_id": excluded.processing_run_id,
        "last_error": excluded.last_error,
    }
    dialect = _dialect_name(session)
    if dialect == "postgresql":
        statement = insert_stmt.on_conflict_do_update(
            constraint="uq_recording_audio_window_manifests_recording_window",
            set_=update_columns,
        )
    else:
        statement = insert_stmt.on_conflict_do_update(
            index_elements=["recording_id", "window_index"],
            set_=update_columns,
        )

    session.execute(statement)
    return list(
        session.execute(
            select(RecordingAudioWindowManifest)
            .where(RecordingAudioWindowManifest.recording_id == recording_id)
            .order_by(RecordingAudioWindowManifest.window_index)
        )
        .scalars()
        .all()
    )


def sync_recording_audio_chunks_from_entries(
    session: Session,
    *,
    recording_id: int,
    source_kind: str,
    disk_entries: list[tuple[int, Path]],
) -> list[RecordingAudioChunk]:
    if not disk_entries:
        return []

    if _supports_native_upsert(session):
        chunk_payloads: list[dict[str, Any]] = []
        absolute_start_ms = 0
        for sequence, storage_path in sorted(disk_entries, key=lambda item: item[0]):
            fields = _build_recording_audio_chunk_fields(
                sequence=sequence,
                source_kind=source_kind,
                storage_path=storage_path,
            )
            fields["absolute_start_ms"] = absolute_start_ms
            fields["absolute_end_ms"] = absolute_start_ms + int(fields["duration_ms"])
            absolute_start_ms = int(fields["absolute_end_ms"])
            now = utc_now()
            chunk_payloads.append(
                {
                    "created_at": now,
                    "updated_at": now,
                    "public_id": generate_pipeline_public_id(),
                    "recording_id": recording_id,
                    **fields,
                }
            )

        return _upsert_recording_audio_chunks(
            session,
            recording_id=recording_id,
            source_kind=source_kind,
            chunk_payloads=chunk_payloads,
        )

    existing_rows = (
        session.execute(
            select(RecordingAudioChunk).where(RecordingAudioChunk.recording_id == recording_id)
        )
        .scalars()
        .all()
    )
    existing_by_sequence = {
        row.sequence_no: row
        for row in existing_rows
        if row.source_kind == source_kind
    }
    existing_by_idempotency = {
        row.idempotency_key: row
        for row in existing_rows
        if row.source_kind == source_kind and row.idempotency_key
    }

    synced_rows: list[RecordingAudioChunk] = []
    absolute_start_ms = 0
    for sequence, storage_path in sorted(disk_entries, key=lambda item: item[0]):
        fields = _build_recording_audio_chunk_fields(
            sequence=sequence,
            source_kind=source_kind,
            storage_path=storage_path,
        )
        row = existing_by_sequence.get(sequence) or existing_by_idempotency.get(fields["idempotency_key"])

        if row is None:
            row = RecordingAudioChunk(recording_id=recording_id, **fields)
        else:
            for field_name, field_value in fields.items():
                setattr(row, field_name, field_value)

        row.absolute_start_ms = absolute_start_ms
        row.absolute_end_ms = absolute_start_ms + row.duration_ms
        absolute_start_ms = row.absolute_end_ms
        session.add(row)
        synced_rows.append(row)
        existing_by_sequence[row.sequence_no] = row
        if row.idempotency_key:
            existing_by_idempotency[row.idempotency_key] = row

    return synced_rows


def sync_recording_audio_chunks_from_directory(
    session: Session,
    *,
    recording_id: int,
    source_kind: str,
    suffix: str,
    temp_dir: Path | None = None,
) -> list[RecordingAudioChunk]:
    temp_dir = temp_dir or recording_upload_temp_dir(recording_id, create=False)
    if not temp_dir.exists():
        return []

    disk_entries: list[tuple[int, Path]] = []
    for filename in os.listdir(temp_dir):
        if not filename.endswith(suffix):
            continue
        try:
            sequence = int(os.path.splitext(filename)[0])
        except ValueError:
            continue
        disk_entries.append((sequence, temp_dir / filename))
    disk_entries.sort(key=lambda item: item[0])

    return sync_recording_audio_chunks_from_entries(
        session,
        recording_id=recording_id,
        source_kind=source_kind,
        disk_entries=disk_entries,
    )


def list_recording_audio_chunks(
    session: Session,
    recording_id: int,
    source_kind: str | None = None,
) -> list[RecordingAudioChunk]:
    statement = select(RecordingAudioChunk).where(
        RecordingAudioChunk.recording_id == recording_id
    )
    if source_kind is not None:
        statement = statement.where(RecordingAudioChunk.source_kind == source_kind)
    statement = statement.order_by(RecordingAudioChunk.sequence_no)
    return list(session.execute(statement).scalars().all())


def sync_recording_audio_window_manifests(
    session: Session,
    *,
    recording_id: int,
    source_kind: str,
    seal_tail: bool,
) -> list[RecordingAudioWindowManifest]:
    chunk_rows = list_recording_audio_chunks(
        session,
        recording_id,
        source_kind=source_kind,
    )
    if not chunk_rows:
        return []

    if _supports_native_upsert(session):
        existing_rows = list(
            session.execute(
                select(RecordingAudioWindowManifest)
                .where(RecordingAudioWindowManifest.recording_id == recording_id)
                .order_by(RecordingAudioWindowManifest.window_index)
            )
            .scalars()
            .all()
        )
        target_window_ms = int(config_manager.get("rolling_diarization_window_ms", 20_000))
        hop_ms = int(config_manager.get("rolling_diarization_hop_ms", 5_000))
        window_specs = build_audio_window_specs(
            chunk_rows,
            target_window_ms=target_window_ms,
            hop_ms=hop_ms,
            seal_tail=seal_tail,
        )
        manifest_payloads = _build_window_manifest_payloads(
            recording_id=recording_id,
            existing_rows=existing_rows,
            window_specs=window_specs,
        )
        return _upsert_window_manifests(
            session,
            recording_id=recording_id,
            manifest_payloads=manifest_payloads,
        )

    manifest_rows = list(
        session.execute(
            select(RecordingAudioWindowManifest)
            .where(RecordingAudioWindowManifest.recording_id == recording_id)
            .order_by(RecordingAudioWindowManifest.window_index)
        )
        .scalars()
        .all()
    )
    target_window_ms = int(config_manager.get("rolling_diarization_window_ms", 20_000))
    hop_ms = int(config_manager.get("rolling_diarization_hop_ms", 5_000))
    window_specs = build_audio_window_specs(
        chunk_rows,
        target_window_ms=target_window_ms,
        hop_ms=hop_ms,
        seal_tail=seal_tail,
    )
    applied_rows = apply_audio_window_specs(
        recording_id=recording_id,
        existing_rows=manifest_rows,
        window_specs=window_specs,
    )
    for row in applied_rows:
        session.add(row)
    return applied_rows


def find_missing_chunk_sequences(chunk_rows: list[RecordingAudioChunk]) -> list[int]:
    if not chunk_rows:
        return []

    missing_sequences: list[int] = []
    expected_sequence = int(chunk_rows[0].sequence_no)
    for row in chunk_rows:
        sequence_no = int(row.sequence_no)
        while expected_sequence < sequence_no:
            missing_sequences.append(expected_sequence)
            expected_sequence += 1
        expected_sequence = sequence_no + 1
    return missing_sequences


def list_recording_upload_sequences(
    recording_id: int,
    *,
    suffixes: set[str] | frozenset[str] | None = None,
    temp_dir: Path | None = None,
) -> set[int]:
    temp_dir = temp_dir or recording_upload_temp_dir(recording_id, create=False)
    if not temp_dir.exists():
        return set()

    sequences: set[int] = set()
    for entry in temp_dir.iterdir():
        if not entry.is_file():
            continue
        entry_suffix = entry.suffix.lower()
        if suffixes is not None and entry_suffix not in suffixes:
            continue
        try:
            sequences.add(int(entry.stem))
        except ValueError:
            continue
    return sequences


def find_pending_recording_upload_sequences(
    recording_id: int,
    *,
    chunk_rows: list[RecordingAudioChunk],
    temp_dir: Path | None = None,
) -> list[int]:
    pending_sequences = list_recording_upload_sequences(
        recording_id,
        suffixes=PENDING_TRANSCODE_SUFFIXES,
        temp_dir=temp_dir,
    )
    if not pending_sequences:
        return []

    available_sequences = {int(row.sequence_no) for row in chunk_rows}
    return sorted(
        sequence
        for sequence in pending_sequences
        if sequence not in available_sequences
    )
