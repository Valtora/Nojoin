from __future__ import annotations

import hashlib
import os
import wave
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session
from sqlmodel import select

from backend.models.pipeline import RecordingAudioChunk, RecordingAudioWindowManifest
from backend.utils.audio import get_audio_duration
from backend.utils.audio_windows import apply_audio_window_specs, build_audio_window_specs
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
        except Exception:
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


def sync_recording_audio_chunks_from_entries(
    session: Session,
    *,
    recording_id: int,
    source_kind: str,
    disk_entries: list[tuple[int, Path]],
) -> list[RecordingAudioChunk]:
    if not disk_entries:
        return []

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
