"""Constant-time registration of live capture chunks.

Every live segment used to re-sync the whole recording: list the upload
directory, load every chunk row, recompute every offset, rebuild every window
spec and upsert every window manifest, all under a lock on the recording row.
The cost of a segment grew with the meeting, and past about 100 minutes the
segment tasks could no longer keep up with the 2 s capture cadence (issue
#417).

Registration now touches only what a segment changes. A per-recording cursor
records how far the chunks are placed on the timeline. A chunk is placed once
every sequence before it is placed or known to have failed, which is what the
full rebuild computes too: offsets are cumulative over the chunks present, and
a missing sequence ends a contiguous group. Placing chunks can only grow the
last group, and growing a group can only change its last few windows, so only
those are rebuilt.

The full rebuild in ``recording_audio_sync`` stays as finalize's reconciliation
pass and as the repair path here, for a cursor that is missing or no longer
matches the chunks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy import delete
from sqlalchemy.orm import Session
from sqlmodel import select

from backend.models.pipeline import (
    RecordingAudioChunk,
    RecordingAudioChunkCursor,
    RecordingAudioWindowManifest,
)
from backend.utils.audio_windows import (
    AudioWindowSpec,
    apply_audio_window_specs,
    build_audio_window_specs,
    build_group_window_specs,
    group_window_count,
)
from backend.utils.config_manager import config_manager
from backend.utils.recording_audio_sync import (
    SEGMENT_CORRUPT_SUFFIX,
    TRANSCODE_FAILED_SUFFIX,
    _apply_chunk_fields,
    _build_window_manifest_payloads,
    _get_or_create_recording_audio_chunk,
    _row_still_describes_file,
    _supports_native_upsert,
    _window_row_signature,
    _window_spec_signature,
    build_recording_audio_chunk_fields,
    list_recording_audio_chunks,
    lock_recording_audio,
    write_window_manifests,
)
from backend.utils.time import utc_now

logger = logging.getLogger(__name__)

PLACEMENT_BATCH_SIZE = 256
TAIL_SCAN_BATCH_SIZE = 32
_FAILED_SEGMENT_SUFFIXES = (TRANSCODE_FAILED_SUFFIX, SEGMENT_CORRUPT_SUFFIX)


@dataclass(frozen=True)
class ChunkStream:
    """One recording's chunks of one source kind, and where its uploads live.

    ``temp_dir`` is where a failed segment leaves its marker. Without it every
    gap waits for its segment.
    """

    recording_id: int
    source_kind: str
    temp_dir: Path | None


@dataclass
class _Placement:
    through_sequence: int
    end_ms: int
    group_start_sequence: int
    group_start_ms: int
    group_first_window_index: int
    target_window_ms: int
    hop_ms: int

    @classmethod
    def initial(cls, target_window_ms: int, hop_ms: int) -> _Placement:
        return cls(
            through_sequence=-1,
            end_ms=0,
            group_start_sequence=0,
            group_start_ms=0,
            group_first_window_index=0,
            target_window_ms=target_window_ms,
            hop_ms=hop_ms,
        )

    @classmethod
    def from_cursor(cls, cursor: RecordingAudioChunkCursor) -> _Placement:
        return cls(
            through_sequence=int(cursor.through_sequence),
            end_ms=int(cursor.end_ms),
            group_start_sequence=int(cursor.group_start_sequence),
            group_start_ms=int(cursor.group_start_ms),
            group_first_window_index=int(cursor.group_first_window_index),
            target_window_ms=int(cursor.target_window_ms),
            hop_ms=int(cursor.hop_ms),
        )

    def group_window_count(self) -> int:
        return group_window_count(
            self.end_ms - self.group_start_ms,
            target_window_ms=self.target_window_ms,
            hop_ms=self.hop_ms,
        )

    def unchanged_window_count(self, previous_end_ms: int) -> int:
        """Windows of the current group that were full by ``previous_end_ms``."""
        span_ms = max(previous_end_ms - self.group_start_ms, 0)
        if span_ms < self.target_window_ms:
            return 0
        return (span_ms - self.target_window_ms) // self.hop_ms + 1

    def start_group(self, sequence: int) -> None:
        """Close the current group and open one at ``sequence``."""
        if self.through_sequence >= 0:
            self.group_first_window_index += self.group_window_count()
        self.group_start_sequence = sequence
        self.group_start_ms = self.end_ms

    def place(self, row: RecordingAudioChunk) -> None:
        if self.through_sequence < 0:
            self.group_start_sequence = int(row.sequence_no)
            self.group_start_ms = self.end_ms
        start_ms = self.end_ms
        end_ms = start_ms + int(row.duration_ms or 0)
        if int(row.absolute_start_ms) != start_ms or int(row.absolute_end_ms) != end_ms:
            row.absolute_start_ms = start_ms
            row.absolute_end_ms = end_ms
        self.end_ms = end_ms
        self.through_sequence = int(row.sequence_no)


def _window_geometry() -> tuple[int, int]:
    return (
        int(config_manager.get("rolling_diarization_window_ms", 20_000)),
        int(config_manager.get("rolling_diarization_hop_ms", 5_000)),
    )


def _sequence_failed(temp_dir: Path | None, sequence: int) -> bool:
    """Whether a sequence will never get a chunk row during this capture."""
    if temp_dir is None:
        return False
    return any(
        (temp_dir / f"{sequence}{suffix}").exists()
        for suffix in _FAILED_SEGMENT_SUFFIXES
    )


def _gap_is_passable(temp_dir: Path | None, first: int, last: int) -> bool:
    return all(_sequence_failed(temp_dir, sequence) for sequence in range(first, last))


def _load_cursor(
    session: Session, recording_id: int, source_kind: str
) -> RecordingAudioChunkCursor | None:
    return (
        session.execute(
            select(RecordingAudioChunkCursor)
            .where(RecordingAudioChunkCursor.recording_id == recording_id)
            .where(RecordingAudioChunkCursor.source_kind == source_kind)
        )
        .scalars()
        .first()
    )


def _save_cursor(
    session: Session,
    stream: ChunkStream,
    cursor: RecordingAudioChunkCursor | None,
    state: _Placement,
) -> RecordingAudioChunkCursor:
    if cursor is None:
        cursor = RecordingAudioChunkCursor(
            recording_id=stream.recording_id,
            source_kind=stream.source_kind,
            through_sequence=state.through_sequence,
            end_ms=state.end_ms,
            group_start_sequence=state.group_start_sequence,
            group_start_ms=state.group_start_ms,
            group_first_window_index=state.group_first_window_index,
            target_window_ms=state.target_window_ms,
            hop_ms=state.hop_ms,
            sealed=False,
        )
    else:
        cursor.through_sequence = state.through_sequence
        cursor.end_ms = state.end_ms
        cursor.group_start_sequence = state.group_start_sequence
        cursor.group_start_ms = state.group_start_ms
        cursor.group_first_window_index = state.group_first_window_index
        cursor.target_window_ms = state.target_window_ms
        cursor.hop_ms = state.hop_ms
        cursor.updated_at = utc_now()
    session.add(cursor)
    return cursor


def _write_window_specs(
    session: Session,
    recording_id: int,
    window_specs: Sequence[AudioWindowSpec],
) -> None:
    """Write only the windows that are new or whose geometry changed.

    A window whose geometry is unchanged is left alone, so its ASR and
    diarisation status are never written back from a read that a live task
    may have overtaken.
    """
    if not window_specs:
        return
    indices = [spec.window_index for spec in window_specs]
    existing_rows = list(
        session.execute(
            select(RecordingAudioWindowManifest)
            .where(RecordingAudioWindowManifest.recording_id == recording_id)
            .where(RecordingAudioWindowManifest.window_index >= min(indices))
            .where(RecordingAudioWindowManifest.window_index <= max(indices))
        )
        .scalars()
        .all()
    )
    existing_by_index = {int(row.window_index): row for row in existing_rows}
    changed_specs = [
        spec
        for spec in window_specs
        if spec.window_index not in existing_by_index
        or _window_row_signature(existing_by_index[spec.window_index])
        != _window_spec_signature(spec)
    ]
    if not changed_specs:
        return
    if _supports_native_upsert(session):
        write_window_manifests(
            session,
            _build_window_manifest_payloads(
                recording_id=recording_id,
                existing_rows=existing_rows,
                window_specs=changed_specs,
            ),
        )
        return
    for row in apply_audio_window_specs(
        recording_id=recording_id,
        existing_rows=existing_rows,
        window_specs=changed_specs,
    ):
        session.add(row)


def _tail_rows(
    session: Session,
    stream: ChunkStream,
    state: _Placement,
    earliest_start_ms: int,
) -> list[RecordingAudioChunk]:
    """The placed chunks of the current group that end after ``earliest_start_ms``.

    Walks back from the cursor in small batches, so the cost follows the window
    length rather than the recording length.
    """
    collected: list[RecordingAudioChunk] = []
    upper = state.through_sequence
    while upper >= state.group_start_sequence:
        batch = list(
            session.execute(
                select(RecordingAudioChunk)
                .where(RecordingAudioChunk.recording_id == stream.recording_id)
                .where(RecordingAudioChunk.source_kind == stream.source_kind)
                .where(RecordingAudioChunk.sequence_no <= upper)
                .where(RecordingAudioChunk.sequence_no >= state.group_start_sequence)
                .order_by(RecordingAudioChunk.sequence_no.desc())
                .limit(TAIL_SCAN_BATCH_SIZE)
            )
            .scalars()
            .all()
        )
        for row in batch:
            if int(row.absolute_end_ms) <= earliest_start_ms:
                return list(reversed(collected))
            collected.append(row)
        if len(batch) < TAIL_SCAN_BATCH_SIZE:
            break
        upper = int(batch[-1].sequence_no) - 1
    return list(reversed(collected))


def _refresh_group_windows(
    session: Session,
    stream: ChunkStream,
    state: _Placement,
    previous_end_ms: int,
) -> None:
    """Rebuild the windows of the current group that ``previous_end_ms`` left open.

    Full windows that ended by ``previous_end_ms`` cannot change: their chunks
    are placed and fixed. Everything from the first window past that point is
    rebuilt, which is the old partial window and the windows the group grew into.
    """
    if state.end_ms <= state.group_start_ms or previous_end_ms >= state.end_ms:
        return
    unchanged_count = state.unchanged_window_count(previous_end_ms)
    tail_rows = _tail_rows(
        session,
        stream,
        state,
        earliest_start_ms=state.group_start_ms + unchanged_count * state.hop_ms,
    )
    _write_window_specs(
        session,
        stream.recording_id,
        build_group_window_specs(
            tail_rows,
            group_start_ms=state.group_start_ms,
            group_end_ms=state.end_ms,
            first_window_index=state.group_first_window_index,
            from_relative_index=unchanged_count,
            target_window_ms=state.target_window_ms,
            hop_ms=state.hop_ms,
            source_kind=stream.source_kind,
        ),
    )


def _rows_from(
    session: Session, stream: ChunkStream, first_sequence: int
) -> list[RecordingAudioChunk]:
    return list(
        session.execute(
            select(RecordingAudioChunk)
            .where(RecordingAudioChunk.recording_id == stream.recording_id)
            .where(RecordingAudioChunk.source_kind == stream.source_kind)
            .where(RecordingAudioChunk.sequence_no >= first_sequence)
            .order_by(RecordingAudioChunk.sequence_no)
            .limit(PLACEMENT_BATCH_SIZE)
        )
        .scalars()
        .all()
    )


def _advance(session: Session, stream: ChunkStream, state: _Placement) -> None:
    """Place every chunk the cursor can now reach, then refresh their windows."""
    previous_end_ms = state.end_ms
    expected = state.through_sequence + 1
    blocked = False
    while not blocked:
        batch = _rows_from(session, stream, expected)
        for row in batch:
            sequence = int(row.sequence_no)
            if sequence > expected:
                # A sequence still uploading or transcoding holds every later
                # chunk back, exactly as the full rebuild would leave a gap.
                if not _gap_is_passable(stream.temp_dir, expected, sequence):
                    blocked = True
                    break
                _refresh_group_windows(session, stream, state, previous_end_ms)
                state.start_group(sequence)
                previous_end_ms = state.end_ms
            state.place(row)
            expected = sequence + 1
        if len(batch) < PLACEMENT_BATCH_SIZE:
            break
    _refresh_group_windows(session, stream, state, previous_end_ms)


def _rebuild(
    session: Session, stream: ChunkStream, target_window_ms: int, hop_ms: int
) -> _Placement:
    """Recompute placement and windows from every chunk row.

    Linear in the recording, so it runs only for a recording that has no cursor
    yet (one in flight across an upgrade) or whose placed chunks changed.
    """
    state = _Placement.initial(target_window_ms, hop_ms)
    placed: list[RecordingAudioChunk] = []
    expected = 0
    for row in list_recording_audio_chunks(
        session, stream.recording_id, source_kind=stream.source_kind
    ):
        sequence = int(row.sequence_no)
        if sequence < expected:
            continue
        if sequence > expected:
            if not _gap_is_passable(stream.temp_dir, expected, sequence):
                break
            state.start_group(sequence)
        state.place(row)
        placed.append(row)
        expected = sequence + 1
    window_specs = build_audio_window_specs(
        placed,
        target_window_ms=target_window_ms,
        hop_ms=hop_ms,
        seal_tail=False,
    )
    _write_window_specs(session, stream.recording_id, window_specs)
    # A rebuild can shorten the timeline (a chunk rewritten shorter, or windows
    # a pre-cursor sync built past a gap), so windows past the new end go.
    session.execute(
        delete(RecordingAudioWindowManifest)
        .where(RecordingAudioWindowManifest.recording_id == stream.recording_id)
        .where(RecordingAudioWindowManifest.window_index >= len(window_specs))
    )
    return state


def _upsert_chunk_row(
    session: Session,
    stream: ChunkStream,
    *,
    storage_path: Path,
    fields: dict[str, Any],
) -> tuple[RecordingAudioChunk, bool]:
    """Store the segment's row. Returns it, and whether it is new or rewritten."""
    existing = (
        session.execute(
            select(RecordingAudioChunk)
            .where(RecordingAudioChunk.recording_id == stream.recording_id)
            .where(RecordingAudioChunk.source_kind == stream.source_kind)
            .where(RecordingAudioChunk.sequence_no == int(fields["sequence_no"]))
        )
        .scalars()
        .first()
    )
    if existing is not None:
        if _row_still_describes_file(existing, storage_path):
            return existing, False
        _apply_chunk_fields(existing, fields)
        session.add(existing)
        return existing, True
    return (
        _get_or_create_recording_audio_chunk(
            session,
            recording_id=stream.recording_id,
            source_kind=stream.source_kind,
            fields=fields,
        ),
        True,
    )


def _place_from_cursor(
    session: Session,
    stream: ChunkStream,
    cursor: RecordingAudioChunkCursor | None,
    force_rebuild: bool = False,
) -> None:
    target_window_ms, hop_ms = _window_geometry()
    if (
        cursor is None
        or force_rebuild
        or int(cursor.target_window_ms) != target_window_ms
        or int(cursor.hop_ms) != hop_ms
    ):
        state = _rebuild(session, stream, target_window_ms, hop_ms)
    else:
        state = _Placement.from_cursor(cursor)
        _advance(session, stream, state)
    _save_cursor(session, stream, cursor, state)


def register_recording_audio_chunk(
    session: Session,
    stream: ChunkStream,
    *,
    sequence: int,
    storage_path: Path,
    fields: dict[str, Any] | None = None,
) -> RecordingAudioChunk | None:
    """Record one live segment and place whatever it unblocks.

    ``fields`` should be built before the call (hashing and reading the WAV
    header), so the lock is held only for the database work. Returns ``None``
    when finalize has sealed the recording's chunks: its own full rebuild has
    already accounted for every segment on disk.
    """
    fields = fields or build_recording_audio_chunk_fields(
        sequence=sequence,
        source_kind=stream.source_kind,
        storage_path=storage_path,
    )
    lock_recording_audio(session, stream.recording_id)
    cursor = _load_cursor(session, stream.recording_id, stream.source_kind)
    if cursor is not None and cursor.sealed:
        logger.info(
            "Skipped registering segment %s for recording %s: its chunks were "
            "sealed by finalize",
            sequence,
            stream.recording_id,
        )
        return None
    row, rewritten = _upsert_chunk_row(
        session, stream, storage_path=storage_path, fields=fields
    )
    _place_from_cursor(
        session,
        stream,
        cursor,
        # A row that changed behind the cursor moves every later offset.
        force_rebuild=(
            rewritten
            and cursor is not None
            and sequence <= int(cursor.through_sequence)
        ),
    )
    return row


def advance_recording_audio_cursor(session: Session, stream: ChunkStream) -> None:
    """Move the cursor past a segment that failed, if chunks are waiting on it."""
    lock_recording_audio(session, stream.recording_id)
    cursor = _load_cursor(session, stream.recording_id, stream.source_kind)
    if cursor is not None and cursor.sealed:
        return
    _place_from_cursor(session, stream, cursor)


def seal_recording_audio_cursor(
    session: Session, *, recording_id: int, source_kind: str
) -> None:
    """Stop live registration once finalize has rebuilt and sealed the windows."""
    cursor = _load_cursor(session, recording_id, source_kind)
    if cursor is None:
        target_window_ms, hop_ms = _window_geometry()
        cursor = _save_cursor(
            session,
            ChunkStream(recording_id, source_kind, None),
            None,
            _Placement.initial(target_window_ms, hop_ms),
        )
    cursor.sealed = True
    session.add(cursor)


def reopen_recording_audio_cursor(
    session: Session, *, recording_id: int, source_kind: str
) -> None:
    """Let a recording resumed after a failed stop register segments again.

    The sealed cursor is dropped rather than unsealed: finalize's rebuild moved
    every offset and window since the cursor last advanced, so the next segment
    rebuilds the cursor from the chunk rows.
    """
    cursor = _load_cursor(session, recording_id, source_kind)
    if cursor is not None and cursor.sealed:
        session.delete(cursor)
