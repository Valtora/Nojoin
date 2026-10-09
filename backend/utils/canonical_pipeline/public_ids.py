"""Which segment-carried ids may become an utterance ``public_id``.

A segment's ``id`` is a request, not an identity. Live-ASR reuse puts a live
utterance's public id there on purpose (``build_transcription_result_from_segments``
and ``_with_word_source_public_ids``), and a transcript projection carries the
ids of the utterances it was built from, but the same key also carries whatever
an engine called its segments: openai-whisper numbers them 0, 1, 2. A kept id
is persisted as ``transcript_utterances.public_id``, which is unique across
every recording.

Finalize and backfill hand ids out through ``UtterancePublicIds``. A client's
full-replace edit keeps its own ids through ``RequestedPublicIds``, which
refuses the write up front when an existing utterance already holds one.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlmodel import col, select

from backend.models.pipeline import TranscriptUtterance
from backend.utils.db_batching import bind_batches

if TYPE_CHECKING:
    from sqlmodel import Session


class SegmentIdConflictError(RuntimeError):
    """A full replace asked to reuse ids that existing utterances hold."""


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


def _held_ids(
    session: Session,
    candidates: list[str],
    *,
    excluding_recording_id: int | None = None,
) -> set[str]:
    held: set[str] = set()
    for batch in bind_batches(candidates):
        statement = select(TranscriptUtterance.public_id).where(
            col(TranscriptUtterance.public_id).in_(batch)
        )
        if excluding_recording_id is not None:
            statement = statement.where(
                TranscriptUtterance.recording_id != excluding_recording_id
            )
        held.update(session.execute(statement).scalars().all())
    return held


def _record_source_public_id(segment: dict[str, Any], public_id: str) -> list[str]:
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
    return source_public_ids


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
        self._claimable = set(candidates) - _held_ids(
            session, candidates, excluding_recording_id=recording_id
        )
        self._sources: dict[str, list[str]] = {}

    def assign(self, segment: dict[str, Any]) -> str:
        """The public id for the utterance built from ``segment``.

        May add ``confidence_payload.source_public_ids`` to ``segment``.
        """
        requested = _requested_public_id(segment)
        sources: list[str] | None = None
        if requested in self._held_here:
            sources = _record_source_public_id(segment, requested)
        elif requested in self._claimable:
            self._held_here.add(requested)
            return requested
        public_id = str(uuid4())
        while public_id in self._held_here:
            public_id = str(uuid4())
        self._held_here.add(public_id)
        if sources is not None:
            self._sources[public_id] = sources
        return public_id

    def lineage_for(self, public_id: str) -> dict[str, Any] | None:
        """The row payload naming the ids ``public_id`` was minted over, if any."""
        sources = self._sources.get(public_id)
        return {"source_public_ids": sources} if sources else None


class RequestedPublicIds:
    """Keeps a client's own segment ids, as a full-replace edit always has.

    Refuses the whole write before anything changes when an existing
    utterance, in any recording or state, already holds a requested id, or
    when the request repeats one: inserting it would violate the unique index.
    """

    def __init__(self, session: Session, segments: Iterable[Mapping[str, Any]]):
        counts = Counter(
            str(segment["id"]) for segment in segments if segment.get("id")
        )
        repeated = {public_id for public_id, count in counts.items() if count > 1}
        conflicts = sorted(repeated | _held_ids(session, sorted(counts)))
        if conflicts:
            raise SegmentIdConflictError(
                f"{len(conflicts)} segment id(s) already belong to existing "
                f"utterances or repeat in the request (first: {conflicts[0]}). "
                "Send exactly the current utterance ids to edit in place, or "
                "omit ids for new segments."
            )

    def assign(self, segment: Mapping[str, Any]) -> str:
        return str(segment.get("id") or uuid4())

    def lineage_for(self, public_id: str) -> None:
        return None


def public_ids_for_write(
    session: Session,
    *,
    recording_id: int,
    segments: Iterable[Mapping[str, Any]],
    keep_requested: bool,
) -> UtterancePublicIds | RequestedPublicIds:
    """``RequestedPublicIds`` for a client edit, ``UtterancePublicIds`` otherwise."""
    if keep_requested:
        return RequestedPublicIds(session, segments)
    return UtterancePublicIds(session, recording_id=recording_id, segments=segments)
