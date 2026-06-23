from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from backend.models.pipeline import RecordingAudioWindowManifest

DEFAULT_AUDIO_WINDOW_MS = 20_000
DEFAULT_AUDIO_WINDOW_HOP_MS = 5_000

WINDOW_STATUS_PENDING = "pending"
WINDOW_STATUS_LIVE_PROCESSING = "live_processing"
WINDOW_STATUS_LIVE_PROCESSED = "live_processed"
WINDOW_STATUS_CATCH_UP_PROCESSED = "catch_up_processed"
WINDOW_STATUS_FAILED = "failed"

WINDOW_ASR_STATUS_PENDING = WINDOW_STATUS_PENDING
WINDOW_ASR_STATUS_LIVE_PROCESSED = WINDOW_STATUS_LIVE_PROCESSED
WINDOW_ASR_STATUS_CATCH_UP_PROCESSED = WINDOW_STATUS_CATCH_UP_PROCESSED
WINDOW_ASR_STATUS_FAILED = WINDOW_STATUS_FAILED

WINDOW_DIARIZATION_STATUS_PENDING = "pending"
WINDOW_DIARIZATION_STATUS_PROCESSING = "processing"
WINDOW_DIARIZATION_STATUS_PROCESSED = "processed"
WINDOW_DIARIZATION_STATUS_FAILED = "failed"

PROCESSED_WINDOW_STATUSES = {
    WINDOW_STATUS_LIVE_PROCESSED,
    WINDOW_STATUS_CATCH_UP_PROCESSED,
}

PROCESSED_ASR_WINDOW_STATUSES = {
    WINDOW_ASR_STATUS_LIVE_PROCESSED,
    WINDOW_ASR_STATUS_CATCH_UP_PROCESSED,
}


@dataclass(frozen=True)
class AudioWindowSpec:
    window_index: int
    source_kind: str
    target_window_ms: int
    hop_ms: int
    window_start_ms: int
    window_end_ms: int
    chunk_start_sequence: int
    chunk_end_sequence: int
    is_partial: bool
    is_sealed: bool


@dataclass(frozen=True)
class CatchUpChunkSpan:
    start_sequence: int
    end_sequence: int
    start_ms: int
    end_ms: int


def build_audio_window_specs(
    chunk_rows: Sequence[Any],
    *,
    target_window_ms: int = DEFAULT_AUDIO_WINDOW_MS,
    hop_ms: int = DEFAULT_AUDIO_WINDOW_HOP_MS,
    seal_tail: bool = False,
) -> list[AudioWindowSpec]:
    if target_window_ms <= 0:
        raise ValueError("target_window_ms must be positive")
    if hop_ms <= 0:
        raise ValueError("hop_ms must be positive")

    contiguous_groups = _group_contiguous_chunks(chunk_rows)
    specs: list[AudioWindowSpec] = []
    next_window_index = 0

    for group in contiguous_groups:
        group_start_ms = int(group[0].absolute_start_ms)
        group_end_ms = int(group[-1].absolute_end_ms)
        if group_end_ms <= group_start_ms:
            continue

        full_window_starts: list[int] = []
        window_start_ms = group_start_ms
        while window_start_ms + target_window_ms <= group_end_ms:
            full_window_starts.append(window_start_ms)
            specs.append(
                _build_window_spec(
                    group,
                    window_index=next_window_index,
                    source_kind=str(group[0].source_kind or "browser"),
                    target_window_ms=target_window_ms,
                    hop_ms=hop_ms,
                    window_start_ms=window_start_ms,
                    window_end_ms=window_start_ms + target_window_ms,
                    is_partial=False,
                    is_sealed=seal_tail,
                )
            )
            next_window_index += 1
            window_start_ms += hop_ms

        last_full_end_ms = (
            full_window_starts[-1] + target_window_ms
            if full_window_starts
            else group_start_ms
        )
        if full_window_starts and last_full_end_ms >= group_end_ms:
            continue

        tail_start_ms = (
            group_start_ms
            if not full_window_starts
            else full_window_starts[-1] + hop_ms
        )
        if tail_start_ms >= group_end_ms:
            continue

        specs.append(
            _build_window_spec(
                group,
                window_index=next_window_index,
                source_kind=str(group[0].source_kind or "browser"),
                target_window_ms=target_window_ms,
                hop_ms=hop_ms,
                window_start_ms=tail_start_ms,
                window_end_ms=group_end_ms,
                is_partial=True,
                is_sealed=seal_tail,
            )
        )
        next_window_index += 1

    return specs


def apply_audio_window_specs(
    *,
    recording_id: int,
    existing_rows: Sequence[RecordingAudioWindowManifest],
    window_specs: Sequence[AudioWindowSpec],
) -> list[RecordingAudioWindowManifest]:
    existing_by_index = {int(row.window_index): row for row in existing_rows}
    applied_rows: list[RecordingAudioWindowManifest] = []

    for spec in window_specs:
        row = existing_by_index.get(spec.window_index)
        if row is None:
            row = RecordingAudioWindowManifest(
                recording_id=recording_id,
                window_index=spec.window_index,
                source_kind=spec.source_kind,
                target_window_ms=spec.target_window_ms,
                hop_ms=spec.hop_ms,
                window_start_ms=spec.window_start_ms,
                window_end_ms=spec.window_end_ms,
                chunk_start_sequence=spec.chunk_start_sequence,
                chunk_end_sequence=spec.chunk_end_sequence,
                status=WINDOW_STATUS_PENDING,
                asr_status=WINDOW_ASR_STATUS_PENDING,
                diarization_status=WINDOW_DIARIZATION_STATUS_PENDING,
                is_partial=spec.is_partial,
                is_sealed=spec.is_sealed,
            )
            applied_rows.append(row)
            continue

        signature_changed = _window_row_signature(row) != _window_spec_signature(spec)
        row.source_kind = spec.source_kind
        row.target_window_ms = spec.target_window_ms
        row.hop_ms = spec.hop_ms
        row.window_start_ms = spec.window_start_ms
        row.window_end_ms = spec.window_end_ms
        row.chunk_start_sequence = spec.chunk_start_sequence
        row.chunk_end_sequence = spec.chunk_end_sequence
        row.is_partial = spec.is_partial
        row.is_sealed = spec.is_sealed
        if signature_changed:
            row.status = WINDOW_STATUS_PENDING
            row.processing_run_id = None
            row.last_error = None
            row.asr_status = WINDOW_ASR_STATUS_PENDING
            row.asr_processing_run_id = None
            row.asr_last_error = None
            row.diarization_status = WINDOW_DIARIZATION_STATUS_PENDING
            row.diarization_processing_run_id = None
            row.diarization_config_hash = None
            row.diarization_window_result_id = None
            row.diarization_last_error = None
        applied_rows.append(row)

    return applied_rows


def mark_audio_windows_processed(
    manifest_rows: Iterable[RecordingAudioWindowManifest],
    *,
    up_to_sequence: int | None = None,
    window_ids: set[int] | None = None,
    status: str,
    processing_run_id: int | None = None,
) -> list[RecordingAudioWindowManifest]:
    if up_to_sequence is None and not window_ids:
        return []

    updated_rows: list[RecordingAudioWindowManifest] = []
    for row in manifest_rows:
        row_id = int(row.id or 0)
        matches = False
        if up_to_sequence is not None and int(row.chunk_end_sequence) <= up_to_sequence:
            matches = True
        if window_ids and row_id and row_id in window_ids:
            matches = True
        if not matches:
            continue
        if status in PROCESSED_ASR_WINDOW_STATUSES:
            row.asr_status = status
            row.asr_processing_run_id = processing_run_id
            row.asr_last_error = None
        _project_legacy_status_after_asr_update(
            row,
            asr_status=status,
            processing_run_id=processing_run_id,
        )
        updated_rows.append(row)

    return updated_rows


def infer_resume_state_from_manifests(
    manifest_rows: Sequence[RecordingAudioWindowManifest],
) -> dict[str, float | int] | None:
    processed_rows = [
        row
        for row in manifest_rows
        if _get_asr_status(row) in PROCESSED_ASR_WINDOW_STATUSES
    ]
    if not processed_rows:
        return None

    last_sequence = max(int(row.chunk_end_sequence) for row in processed_rows)
    last_window_end_ms = max(int(row.window_end_ms) for row in processed_rows)
    return {
        "next_expected": last_sequence + 1,
        "buffer_abs_start": last_window_end_ms / 1000.0,
    }


def collect_pending_chunk_spans(
    manifest_rows: Sequence[RecordingAudioWindowManifest],
    chunk_rows: Sequence[Any],
) -> list[CatchUpChunkSpan]:
    if not manifest_rows or not chunk_rows:
        return []

    pending_ranges = [
        (int(row.chunk_start_sequence), int(row.chunk_end_sequence))
        for row in manifest_rows
        if _get_asr_status(row) not in PROCESSED_ASR_WINDOW_STATUSES
    ]
    if not pending_ranges:
        return []

    chunk_by_sequence = {int(row.sequence_no): row for row in chunk_rows}
    merged_ranges = _merge_sequence_ranges(pending_ranges)
    spans: list[CatchUpChunkSpan] = []
    for start_sequence, end_sequence in merged_ranges:
        start_chunk = chunk_by_sequence.get(start_sequence)
        end_chunk = chunk_by_sequence.get(end_sequence)
        if start_chunk is None or end_chunk is None:
            continue
        spans.append(
            CatchUpChunkSpan(
                start_sequence=start_sequence,
                end_sequence=end_sequence,
                start_ms=int(start_chunk.absolute_start_ms),
                end_ms=int(end_chunk.absolute_end_ms),
            )
        )
    return spans


def count_manifest_statuses(
    manifest_rows: Sequence[RecordingAudioWindowManifest],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in manifest_rows:
        status = str(row.status or WINDOW_STATUS_PENDING)
        counts[status] = counts.get(status, 0) + 1
    return counts


def _get_asr_status(row: Any) -> str:
    asr_status = getattr(row, "asr_status", None)
    if asr_status:
        return str(asr_status)

    legacy_status = str(getattr(row, "status", "") or "")
    if legacy_status in PROCESSED_ASR_WINDOW_STATUSES:
        return legacy_status
    return WINDOW_ASR_STATUS_PENDING


def _get_diarization_status(row: Any) -> str:
    diarization_status = getattr(row, "diarization_status", None)
    if diarization_status:
        return str(diarization_status)

    legacy_status = str(getattr(row, "status", "") or "")
    if legacy_status == WINDOW_STATUS_LIVE_PROCESSING:
        return WINDOW_DIARIZATION_STATUS_PROCESSING
    if legacy_status == WINDOW_STATUS_FAILED:
        return WINDOW_DIARIZATION_STATUS_FAILED
    return WINDOW_DIARIZATION_STATUS_PENDING


def _project_legacy_status_after_asr_update(
    row: Any,
    *,
    asr_status: str,
    processing_run_id: int | None,
) -> None:
    diarization_status = _get_diarization_status(row)
    if diarization_status == WINDOW_DIARIZATION_STATUS_PROCESSING:
        row.status = WINDOW_STATUS_LIVE_PROCESSING
        row.processing_run_id = getattr(row, "diarization_processing_run_id", None)
        row.last_error = None
        return
    if diarization_status == WINDOW_DIARIZATION_STATUS_FAILED:
        row.status = WINDOW_STATUS_FAILED
        row.processing_run_id = getattr(row, "diarization_processing_run_id", None)
        row.last_error = getattr(row, "diarization_last_error", None)
        return

    row.status = asr_status
    row.processing_run_id = processing_run_id
    row.last_error = None


def window_asr_is_processed(row: Any) -> bool:
    return _get_asr_status(row) in PROCESSED_ASR_WINDOW_STATUSES


def window_diarization_status(row: Any) -> str:
    return _get_diarization_status(row)


def window_diarization_is_processed(
    row: Any, *, config_hash: str | None = None
) -> bool:
    if _get_diarization_status(row) != WINDOW_DIARIZATION_STATUS_PROCESSED:
        return False
    if config_hash is None:
        return True
    return str(getattr(row, "diarization_config_hash", "") or "") == str(config_hash)


def _group_contiguous_chunks(chunk_rows: Sequence[Any]) -> list[list[Any]]:
    ordered_rows = sorted(chunk_rows, key=lambda row: int(row.sequence_no))
    groups: list[list[Any]] = []
    current_group: list[Any] = []
    previous_sequence: int | None = None

    for row in ordered_rows:
        sequence_no = int(row.sequence_no)
        if previous_sequence is None or sequence_no == previous_sequence + 1:
            current_group.append(row)
        else:
            if current_group:
                groups.append(current_group)
            current_group = [row]
        previous_sequence = sequence_no

    if current_group:
        groups.append(current_group)
    return groups


def _build_window_spec(
    group: Sequence[Any],
    *,
    window_index: int,
    source_kind: str,
    target_window_ms: int,
    hop_ms: int,
    window_start_ms: int,
    window_end_ms: int,
    is_partial: bool,
    is_sealed: bool,
) -> AudioWindowSpec:
    overlapping_chunks = [
        row
        for row in group
        if int(row.absolute_end_ms) > window_start_ms
        and int(row.absolute_start_ms) < window_end_ms
    ]
    if not overlapping_chunks:
        raise ValueError("window must overlap at least one audio chunk")

    return AudioWindowSpec(
        window_index=window_index,
        source_kind=source_kind,
        target_window_ms=target_window_ms,
        hop_ms=hop_ms,
        window_start_ms=window_start_ms,
        window_end_ms=window_end_ms,
        chunk_start_sequence=int(overlapping_chunks[0].sequence_no),
        chunk_end_sequence=int(overlapping_chunks[-1].sequence_no),
        is_partial=is_partial,
        is_sealed=is_sealed,
    )


def _merge_sequence_ranges(ranges: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    ordered_ranges = sorted(ranges)
    if not ordered_ranges:
        return []

    merged: list[tuple[int, int]] = [ordered_ranges[0]]
    for start_sequence, end_sequence in ordered_ranges[1:]:
        previous_start, previous_end = merged[-1]
        if start_sequence <= previous_end + 1:
            merged[-1] = (previous_start, max(previous_end, end_sequence))
            continue
        merged.append((start_sequence, end_sequence))
    return merged


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
