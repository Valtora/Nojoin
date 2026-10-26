"""Stage People split projections and publish after voiceprint inference."""

from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.models.recording import Recording
from backend.models.transcript import Transcript
from backend.utils.canonical_pipeline import serialize_canonical_utterances
from backend.utils.transcript_publication import lock_transcript_publications

from .helpers import (
    SegmentSelection,
    _canonical_transcript_writes_enabled,
    _copy_transcript_segments,
    _persist_segments_for_speaker_work,
)

StagedSegments = dict[int, tuple[Recording, Transcript, list[dict[str, Any]]]]


async def read_split_segments(
    db: AsyncSession, recording: Recording, transcript: Transcript | None
) -> list[dict[str, Any]]:
    """Read without a savepoint, whose unconditional flush could publish staging."""
    if transcript is None:
        return []
    if _canonical_transcript_writes_enabled():
        canonical = await db.run_sync(serialize_canonical_utterances, recording.id)
        if canonical:
            return canonical
    return _copy_transcript_segments(transcript.segments)


def stage_split_segments(
    segments: Sequence[dict[str, Any]],
    selections: Sequence[SegmentSelection],
    label: str,
) -> list[dict[str, Any]] | None:
    """Prepare the existing majority-overlap selection without publishing it."""
    replacement = [dict(segment) for segment in segments]
    changed = False
    for segment in replacement:
        duration = segment["end"] - segment["start"]
        if duration > 0 and any(
            max(
                0,
                min(segment["end"], selected.end)
                - max(segment["start"], selected.start),
            )
            / duration
            > 0.5
            for selected in selections
        ):
            segment["speaker"] = label
            changed = True
    return replacement if changed else None


async def publish_split_segments(
    db: AsyncSession,
    source_sequences: Mapping[int, int],
    staged: StagedSegments,
) -> None:
    """Fence and validate the staged read before any split changes are flushed."""
    await db.run_sync(lock_transcript_publications, source_sequences)
    current = dict(
        (
            await db.execute(
                select(Transcript.recording_id, Transcript.change_sequence).where(
                    Transcript.recording_id.in_(source_sequences)
                )
            )
        ).all()
    )
    if current != source_sequences:
        raise HTTPException(
            status_code=409,
            detail="Transcript changed during voiceprint extraction; please retry the split",
        )
    # Make staged speaker rows queryable before canonical alias resolution.
    await db.flush()
    for recording, transcript, segments in staged.values():
        await _persist_segments_for_speaker_work(
            db, recording=recording, transcript=transcript, segments=segments
        )
