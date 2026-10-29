"""Fixed WAV snapshots over the PCM payloads already held for live capture.

Every browser chunk is the canonical WAV that ``decode_browser_flac_segment``
writes: a 44-byte header, then 16 kHz two-channel 16-bit PCM, so its payload is
``byte_size - 44`` bytes. Stored metadata plans a response; audio files are
opened for the intersecting spans alone, and each one's header is checked
before its bytes are served. Neither a meeting-sized buffer nor a transcode
sits on this read path.
"""

from __future__ import annotations

import os
import re
import struct
from collections.abc import AsyncIterator
from dataclasses import dataclass

import aiofiles
from fastapi import HTTPException
from sqlalchemy import ColumnElement, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from backend.models.pipeline import RecordingAudioChunk, RecordingAudioChunkCursor
from backend.models.recording import ClientStatus, Recording
from backend.processing.browser_live_audio import (
    BROWSER_LIVE_CHANNEL_COUNT,
    BROWSER_LIVE_SAMPLE_RATE_HZ,
)
from backend.utils.stored_paths import confined_recording_path

READ_SIZE = 64 * 1024
RANGE_LIMIT = 2500 * 1024
WAV_HEADER_SIZE = 44
FRAME_BYTES = BROWSER_LIVE_CHANNEL_COUNT * 2
PCM_BYTES_PER_SECOND = BROWSER_LIVE_SAMPLE_RATE_HZ * FRAME_BYTES
# 18 digits keep int() far from its 4,300-digit limit and exceed any file size.
_RANGE = re.compile(r"bytes=(\d{0,18})-(\d{0,18})")
UNAVAILABLE = "This capture's audio snapshot is not available."


@dataclass(frozen=True)
class PcmPart:
    path: str
    offset: int
    size: int


def wav_header(pcm_size: int) -> bytes:
    """The canonical header for ``pcm_size`` bytes of browser-format PCM."""
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        pcm_size + 36,
        b"WAVE",
        b"fmt ",
        16,
        1,
        BROWSER_LIVE_CHANNEL_COUNT,
        BROWSER_LIVE_SAMPLE_RATE_HZ,
        PCM_BYTES_PER_SECOND,
        FRAME_BYTES,
        16,
        b"data",
        pcm_size,
    )


@dataclass(frozen=True)
class CaptureSnapshot:
    parts: tuple[PcmPart, ...]
    pcm_size: int

    @property
    def size(self) -> int:
        return WAV_HEADER_SIZE + self.pcm_size

    def header(self) -> bytes:
        return wav_header(self.pcm_size)


def _unplayable() -> ColumnElement[bool]:
    """A chunk row whose stored geometry is not a canonical browser WAV."""
    return or_(
        col(RecordingAudioChunk.sample_rate_hz) != BROWSER_LIVE_SAMPLE_RATE_HZ,
        col(RecordingAudioChunk.channel_count) != BROWSER_LIVE_CHANNEL_COUNT,
        col(RecordingAudioChunk.byte_size) <= WAV_HEADER_SIZE,
        (col(RecordingAudioChunk.byte_size) - WAV_HEADER_SIZE) % FRAME_BYTES != 0,
        col(RecordingAudioChunk.upload_status).in_(["cleaned", "failed"]),
    )


async def _through_sequence(db: AsyncSession, recording_id: int) -> int | None:
    result = await db.execute(
        select(col(RecordingAudioChunkCursor.through_sequence)).where(
            col(RecordingAudioChunkCursor.recording_id) == recording_id,
            col(RecordingAudioChunkCursor.source_kind) == "browser",
        )
    )
    return result.scalar_one_or_none()


async def capture_playback_sequence(
    db: AsyncSession, recording: Recording
) -> int | None:
    """Advertise only a placed prefix made of canonical browser WAV chunks."""
    assert recording.id is not None
    through = await _through_sequence(db, recording.id)
    if through is None:
        return (
            -1
            if recording.client_status in {ClientStatus.RECORDING, ClientStatus.PAUSED}
            else None
        )
    if through < 0:
        return -1
    result = await db.execute(
        select(func.count(), func.sum(case((_unplayable(), 1), else_=0))).where(
            col(RecordingAudioChunk.recording_id) == recording.id,
            col(RecordingAudioChunk.source_kind) == "browser",
            col(RecordingAudioChunk.sequence_no) <= through,
        )
    )
    count, invalid_count = result.one()
    return through if count and not invalid_count else None


async def load_capture_snapshot(
    db: AsyncSession, recording_id: int, sequence: int
) -> CaptureSnapshot:
    """Load a fixed prefix; the request never moves beyond the placed cursor."""
    through = await _through_sequence(db, recording_id)
    if through is None or sequence > through:
        raise HTTPException(404, UNAVAILABLE)
    result = await db.execute(
        select(
            col(RecordingAudioChunk.sequence_no),
            col(RecordingAudioChunk.storage_path),
            col(RecordingAudioChunk.byte_size),
            _unplayable().label("unplayable"),
        )
        .where(
            col(RecordingAudioChunk.recording_id) == recording_id,
            col(RecordingAudioChunk.source_kind) == "browser",
            col(RecordingAudioChunk.sequence_no) <= sequence,
        )
        .order_by(col(RecordingAudioChunk.sequence_no))
    )
    rows = result.all()
    if not rows or rows[-1].sequence_no != sequence:
        raise HTTPException(404, UNAVAILABLE)
    if any(row.unplayable for row in rows):
        raise HTTPException(404, UNAVAILABLE)
    parts = tuple(
        PcmPart(row.storage_path, WAV_HEADER_SIZE, row.byte_size - WAV_HEADER_SIZE)
        for row in rows
    )
    size = sum(part.size for part in parts)
    if size > 0xFFFFFFFF - 36:
        raise HTTPException(404, "This capture exceeds the WAV playback size limit.")
    return CaptureSnapshot(parts, size)


def snapshot_range(range_header: str | None, size: int) -> tuple[int, int]:
    """Resolve one byte range, including suffix ranges, with bounded responses."""
    if range_header is None:
        return 0, size - 1
    match = _RANGE.fullmatch(range_header)
    if match is None or not any(match.groups()):
        raise _unsatisfiable(size)
    first, last = match.groups()
    if first:
        start = int(first)
        end = min(int(last), size - 1) if last else size - 1
    else:
        suffix = int(last)
        if suffix <= 0:
            raise _unsatisfiable(size)
        start, end = max(0, size - suffix), size - 1
    if start >= size or start > end:
        raise _unsatisfiable(size)
    return start, min(end, start + RANGE_LIMIT - 1)


def _unsatisfiable(size: int) -> HTTPException:
    return HTTPException(
        416,
        "Requested range not satisfiable",
        headers={"Content-Range": f"bytes */{size}"},
    )


def _verified_path(part: PcmPart) -> str:
    """The chunk's file, provided it still starts with its canonical header."""
    path = confined_recording_path(part.path)
    if path is None:
        raise HTTPException(404, UNAVAILABLE)
    try:
        with open(path, "rb") as handle:
            header = handle.read(WAV_HEADER_SIZE)
            file_size = os.fstat(handle.fileno()).st_size
    except OSError as exc:
        raise HTTPException(404, UNAVAILABLE) from exc
    if header != wav_header(part.size) or file_size < part.offset + part.size:
        raise HTTPException(404, UNAVAILABLE)
    return path


def snapshot_spans(snapshot: CaptureSnapshot, start: int, end: int) -> list[PcmPart]:
    """Map virtual WAV offsets to the selected files, before headers are sent.

    Blocking file reads: callers run this in a worker thread.
    """
    spans: list[PcmPart] = []
    position = WAV_HEADER_SIZE
    for part in snapshot.parts:
        first, last = max(start, position), min(end + 1, position + part.size)
        if first < last:
            path = _verified_path(part)
            spans.append(PcmPart(path, part.offset + first - position, last - first))
        position += part.size
        if position > end:
            break
    return spans


async def stream_snapshot(
    snapshot: CaptureSnapshot, start: int, end: int, spans: list[PcmPart]
) -> AsyncIterator[bytes]:
    """Read only requested PCM spans in bounded blocks, with no capture lock."""
    if start < WAV_HEADER_SIZE:
        yield snapshot.header()[start : min(end + 1, WAV_HEADER_SIZE)]
    for span in spans:
        async with aiofiles.open(span.path, "rb") as handle:
            await handle.seek(span.offset)
            remaining = span.size
            while remaining:
                data = await handle.read(min(READ_SIZE, remaining))
                if not data:
                    raise OSError("Capture audio changed during playback.")
                remaining -= len(data)
                yield data
