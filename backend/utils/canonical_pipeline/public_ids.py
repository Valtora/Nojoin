"""Which segment-carried ids finalize may keep as an utterance ``public_id``.

A merged segment's ``id`` is a request, not an identity. Live-ASR reuse puts a
live utterance's public id there on purpose (``build_transcription_result_from_segments``
and ``_with_word_source_public_ids``), but the same key also carries whatever an
engine called its segments: openai-whisper numbers them 0, 1, 2. Finalize
persists a kept id as ``transcript_utterances.public_id``, which is unique
across every recording, so it keeps only an id in the form utterance ids are
minted in that no other recording already holds.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlmodel import col, select

from backend.models.pipeline import TranscriptUtterance
from backend.utils.db_batching import bind_batches

if TYPE_CHECKING:
    from sqlmodel import Session


def is_utterance_public_id(value: str) -> bool:
    """True for a canonical UUID string, the only form utterance ids take.

    ``generate_pipeline_public_id`` mints uuid4 and the live lane mints uuid5
    (``_build_live_utterance_public_id``); both are the lower-case hyphenated
    form ``str(UUID(...))`` returns.
    """
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def claimable_segment_public_ids(
    session: Session,
    *,
    recording_id: int,
    segments: Iterable[Mapping[str, Any]],
) -> set[str]:
    """The segment ids finalize may persist for ``recording_id``.

    An id qualifies when it is a well-formed utterance id and no utterance of
    another recording holds it. An id this recording already holds is left to
    finalize's per-recording reservation, which mints a fresh id and records
    the requested one as the new utterance's source.
    """
    requested = (str(segment.get("id") or "").strip() for segment in segments)
    candidates = sorted(
        {public_id for public_id in requested if is_utterance_public_id(public_id)}
    )
    held_elsewhere: set[str] = set()
    for batch in bind_batches(candidates):
        held_elsewhere.update(
            session.execute(
                select(TranscriptUtterance.public_id)
                .where(col(TranscriptUtterance.public_id).in_(batch))
                .where(TranscriptUtterance.recording_id != recording_id)
            )
            .scalars()
            .all()
        )
    return set(candidates) - held_elsewhere
