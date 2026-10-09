"""Which segment-carried ids may become an utterance ``public_id``.

A segment's ``id`` is a request, not an identity. Live-ASR reuse puts a live
utterance's public id there on purpose (``build_transcription_result_from_segments``
and ``_with_word_source_public_ids``), and a transcript projection carries the
ids of the utterances it was built from, but the same key also carries whatever
an engine called its segments: openai-whisper numbers them 0, 1, 2. A kept id
is persisted as ``transcript_utterances.public_id``, which is unique across
every recording, so the writers that build utterances from segments (finalize
and the backfill/replace path) hand ids out through ``UtterancePublicIds``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

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


def _requested_public_id(segment: Mapping[str, Any]) -> str:
    return str(segment.get("id") or "").strip()


def _ids_held_elsewhere(
    session: Session, *, recording_id: int, candidates: list[str]
) -> set[str]:
    held: set[str] = set()
    for batch in bind_batches(candidates):
        held.update(
            session.execute(
                select(TranscriptUtterance.public_id)
                .where(col(TranscriptUtterance.public_id).in_(batch))
                .where(TranscriptUtterance.recording_id != recording_id)
            )
            .scalars()
            .all()
        )
    return held


def _record_source_public_id(segment: dict[str, Any], public_id: str) -> None:
    """Note on the segment that its utterance replaces ``public_id``."""
    confidence_payload = dict(segment.get("confidence_payload") or {})
    source_public_ids = [
        str(source_id or "").strip()
        for source_id in confidence_payload.get("source_public_ids")
        or segment.get("source_public_ids")
        or []
        if str(source_id or "").strip()
    ]
    if public_id not in source_public_ids:
        source_public_ids.append(public_id)
    confidence_payload["source_public_ids"] = source_public_ids
    segment["confidence_payload"] = confidence_payload


class UtterancePublicIds:
    """Hands out ``public_id`` values for one write of a recording's segments.

    A requested id is kept when it is a well-formed utterance id that no
    utterance holds yet, in this recording or any other. An id this recording
    already holds (a superseded or still-active utterance) is recorded as the
    new utterance's source and a fresh id is minted. Anything else, an engine's
    segment number or an id another recording owns, gets a fresh id.
    """

    def __init__(
        self,
        session: Session,
        *,
        recording_id: int,
        segments: Iterable[Mapping[str, Any]],
    ) -> None:
        self._held_here: set[str] = {
            str(public_id)
            for public_id in session.execute(
                select(TranscriptUtterance.public_id).where(
                    TranscriptUtterance.recording_id == recording_id
                )
            )
            .scalars()
            .all()
            if str(public_id or "").strip()
        }
        candidates = sorted(
            {
                public_id
                for public_id in map(_requested_public_id, segments)
                if is_utterance_public_id(public_id)
            }
        )
        self._claimable = set(candidates) - _ids_held_elsewhere(
            session, recording_id=recording_id, candidates=candidates
        )

    def assign(self, segment: dict[str, Any]) -> str:
        """The public id for the utterance built from ``segment``.

        May add ``confidence_payload.source_public_ids`` to ``segment``.
        """
        requested = _requested_public_id(segment)
        if requested in self._held_here:
            _record_source_public_id(segment, requested)
        elif requested in self._claimable:
            self._held_here.add(requested)
            return requested
        public_id = str(uuid4())
        while public_id in self._held_here:
            public_id = str(uuid4())
        self._held_here.add(public_id)
        return public_id
