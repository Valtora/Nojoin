"""Durable capture cuts on the recorded audio timeline, excluding paused time."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy.orm import Session
from sqlmodel import select

from backend.models.pipeline import RecordingAudioChunk, RecordingAudioChunkCursor
from backend.models.transcript import Transcript

RESUME_SEQUENCES = "resume_chunk_sequences"
RESUME_BOUNDARIES = "resume_boundaries_ms"


def capture_interval(start_ms: int, boundaries_ms: Sequence[int]) -> int | None:
    """A live line's recorded-audio interval; zero stays absent on old captures."""
    return sum(point <= start_ms for point in boundaries_ms) or None


def mark_capture_intervals(
    segments: list[dict[str, Any]], boundaries_ms: Sequence[int]
) -> None:
    """Keep pending legacy lines in their own capture interval in public reads."""
    for segment in segments:
        interval = capture_interval(
            round(float(segment["start"]) * 1000), boundaries_ms
        )
        if interval is not None:
            segment["confidence_payload"] = {
                **(segment.get("confidence_payload") or {}),
                "capture_interval": interval,
            }


def mark_capture_resume(transcript: Transcript | None, next_sequence: int) -> None:
    """Record a sequence fence before uploads or asynchronous transcodes resume."""
    if transcript is None or next_sequence <= 0:
        return
    state = transcript.settling_state or {}
    transcript.settling_state = {
        **state,
        RESUME_SEQUENCES: sorted({*state.get(RESUME_SEQUENCES, []), next_sequence}),
    }


def durable_resume_state(state: dict[str, Any] | None) -> dict[str, Any]:
    """Keep capture geometry independently of word-settling eligibility."""
    return {
        key: list(state[key])
        for key in (RESUME_SEQUENCES, RESUME_BOUNDARIES)
        if state and state.get(key)
    }


def resolve_resume_boundaries(
    state: dict[str, Any] | None,
    chunks: Sequence[RecordingAudioChunk],
    *,
    through_sequence: int | None = None,
) -> list[int]:
    """Resolve cuts only from placed chunks, never transcode completion order."""
    state = state or {}
    boundaries = set(state.get(RESUME_BOUNDARIES, []))
    placed = sorted(
        (
            row
            for row in chunks
            if through_sequence is None or row.sequence_no <= through_sequence
        ),
        key=lambda row: row.sequence_no,
    )
    for sequence in state.get(RESUME_SEQUENCES, []):
        first = next((row for row in placed if row.sequence_no >= sequence), None)
        if first is not None and first.absolute_start_ms > 0:
            boundaries.add(int(first.absolute_start_ms))
    return sorted(boundaries)


def load_resume_boundaries(
    session: Session, transcript: Transcript | None
) -> list[int]:
    """Read live cuts without accepting offsets that a missing chunk may move."""
    state = getattr(transcript, "settling_state", None) or {}
    if not state.get(RESUME_SEQUENCES) or not hasattr(session, "execute"):
        return list(state.get(RESUME_BOUNDARIES, []))
    cursor = session.execute(
        select(RecordingAudioChunkCursor)
        .where(RecordingAudioChunkCursor.recording_id == transcript.recording_id)
        .where(RecordingAudioChunkCursor.source_kind == "browser")
    ).scalar_one_or_none()
    if cursor is None:
        return list(state.get(RESUME_BOUNDARIES, []))
    chunks = []
    for sequence in state[RESUME_SEQUENCES]:
        first = session.execute(
            select(RecordingAudioChunk)
            .where(RecordingAudioChunk.recording_id == transcript.recording_id)
            .where(RecordingAudioChunk.source_kind == "browser")
            .where(RecordingAudioChunk.sequence_no >= sequence)
            .where(RecordingAudioChunk.sequence_no <= cursor.through_sequence)
            .order_by(RecordingAudioChunk.sequence_no)
            .limit(1)
        ).scalar_one_or_none()
        if first is not None:
            chunks.append(first)
    return resolve_resume_boundaries(
        state, chunks, through_sequence=cursor.through_sequence
    )


def persist_resume_boundaries(
    transcript: Transcript | None, chunks: Sequence[RecordingAudioChunk]
) -> None:
    """Reconciled offsets outlive chunk cleanup and reprocessing."""
    state = getattr(transcript, "settling_state", None) or {}
    if transcript is None or not state.get(RESUME_SEQUENCES):
        return
    transcript.settling_state = {
        **state,
        RESUME_BOUNDARIES: resolve_resume_boundaries(state, chunks),
    }


def split_resume_regions(
    regions: list[dict[str, Any]], offset_s: float, boundaries_ms: Sequence[int]
) -> list[dict[str, Any]]:
    """Cut legacy live audio before decoding, including engines without word times."""
    result = []
    for region in regions:
        start, end = float(region["start"]), float(region["end"])
        cuts = [point / 1000 - offset_s for point in boundaries_ms]
        for cut in [point for point in cuts if start < point < end] + [end]:
            result.append({**region, "start": start, "end": cut})
            start = cut
    return result
