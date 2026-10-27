"""Scoped word partitions and canonical events for the marked live lane."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session
from sqlmodel import col, select

from backend.models.live_settling_public import public_settling_fields
from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceState
from backend.processing.browser_live_audio import source_channel_from_confidence_payload
from backend.utils.word_text import reconstruct_text_from_words

from .constants import LIVE_SPEAKER_LABEL_PAYLOAD_KEY, _append_utterance_event
from .live_labels import LiveLabelDecision
from .live_settling_lines import LineBoundaries, Partition, partition_words, word_extent
from .live_settling_sources import partition_source_metadata

SETTLING_KEY = "live_text_settling"
DecideSpeaker = Callable[[int, int], LiveLabelDecision]


def line_is_locked(row: TranscriptUtterance) -> bool:
    """Whether machine work must retain this complete canonical line."""
    return bool((row.confidence_payload or {}).get(SETTLING_KEY, {}).get("line_locked"))


def row_words(row: TranscriptUtterance) -> list[dict[str, Any]]:
    """Read the stored ordered words without changing their dictionaries."""
    return [
        dict(word)
        for segment in (row.confidence_payload or {}).get("asr_segments", [])
        for word in segment.get("words", [])
    ]


def live_rows(
    session: Session,
    recording_id: int,
    *,
    window_start_ms: int | None = None,
    lock: bool = False,
) -> list[TranscriptUtterance]:
    """Read the mutable tail and immutable anchors still inside the audio window."""
    mutable = (
        col(TranscriptUtterance.confidence_payload)[SETTLING_KEY]["line_locked"]
        .as_boolean()
        .is_not(True)
    )
    if window_start_ms is not None:
        mutable = (
            col(TranscriptUtterance.confidence_payload)[SETTLING_KEY]["text_locked"]
            .as_boolean()
            .is_not(True)
        ) | (
            mutable
            & col(TranscriptUtterance.confidence_payload)[SETTLING_KEY]["line_closed"]
            .as_boolean()
            .is_not(True)
        )
        latest_anchor = (
            select(TranscriptUtterance.id)
            .where(TranscriptUtterance.recording_id == recording_id)
            .where(TranscriptUtterance.state == TranscriptUtteranceState.PROVISIONAL)
            .where(TranscriptUtterance.source_kind == "live")
            .where(
                col(TranscriptUtterance.confidence_payload)[SETTLING_KEY]["text_locked"]
                .as_boolean()
                .is_(True)
            )
            .order_by(
                col(TranscriptUtterance.end_ms).desc(),
                col(TranscriptUtterance.id).desc(),
            )
            .limit(1)
        )
        mutable = (
            mutable
            | (TranscriptUtterance.end_ms >= window_start_ms)
            | col(TranscriptUtterance.id).in_(latest_anchor)
        )
    statement = (
        select(TranscriptUtterance)
        .where(TranscriptUtterance.recording_id == recording_id)
        .where(TranscriptUtterance.state == TranscriptUtteranceState.PROVISIONAL)
        .where(TranscriptUtterance.source_kind == "live")
        .where(
            col(TranscriptUtterance.confidence_payload)[SETTLING_KEY][
                "version"
            ].as_integer()
            == 1
        )
        .where(mutable)
        .order_by(col(TranscriptUtterance.id))
    )
    if lock:
        statement = statement.with_for_update().execution_options(
            populate_existing=True
        )
    return sorted(
        session.execute(statement).scalars().all(),
        key=lambda row: (row.start_ms, row.sort_key, row.id),
    )


def mutable_row(row: TranscriptUtterance) -> bool:
    """Manual results and fully locked lines are immutable machine anchors."""
    return not (
        row.manual_text_locked or row.manual_speaker_locked or line_is_locked(row)
    )


def word_decisions(
    words: Sequence[dict[str, Any]],
    rows: Sequence[TranscriptUtterance],
    decide: DecideSpeaker | None,
    *,
    speaker_enabled: bool,
    closing: bool = False,
) -> list[LiveLabelDecision]:
    """Assign timed words independently; untimed atoms follow their neighbour."""
    previous = {
        word.get("id"): _stored_speaker(row, word)
        for row in rows
        for word in row_words(row)
    }
    immutable_speakers = {
        word.get("id")
        for row in rows
        for word in row_words(row)
        if word.get("locked") and previous[word.get("id")].locked
    }
    decisions: list[LiveLabelDecision | None] = []
    for word in words:
        old = previous.get(word.get("id"))
        start, end = word.get("start_ms"), word.get("end_ms")
        if old is not None and word.get("id") in immutable_speakers:
            decision = old
        elif decide is not None and start is not None and end is not None:
            decision = decide(int(start), max(int(start) + 1, int(end)))
        elif start is not None and end is not None:
            decision = old or LiveLabelDecision("UNKNOWN", not speaker_enabled, None)
        else:
            decision = None
        decisions.append(decision)
    for index, decision in enumerate(decisions):
        if decision is None:
            neighbour = next(
                (
                    decisions[j]
                    for j in range(index - 1, -1, -1)
                    if decisions[j] is not None
                ),
                None,
            ) or next(
                (item for item in decisions[index + 1 :] if item is not None), None
            )
            decisions[index] = neighbour or LiveLabelDecision(
                "UNKNOWN", not speaker_enabled, None
            )
    return [
        LiveLabelDecision(item.speaker, closing or item.locked, item.share)
        for item in decisions
        if item is not None
    ]


def _stored_speaker(
    row: TranscriptUtterance, word: dict[str, Any]
) -> LiveLabelDecision:
    """Retain settled word speakers when an open run gains pending words."""
    stored = (row.confidence_payload or {}).get(LIVE_SPEAKER_LABEL_PAYLOAD_KEY, {})
    return LiveLabelDecision(
        row.speaker_label or "UNKNOWN",
        bool(
            stored.get("locked") or word.get("id") in stored.get("locked_word_ids", [])
        ),
        None,
    )


def _bounds(
    words: list[dict[str, Any]], rows: Sequence[TranscriptUtterance]
) -> tuple[int, int]:
    extents = [extent for word in words if (extent := word_extent(word)) is not None]
    return (
        min(
            (item[0] for item in extents),
            default=min((row.start_ms for row in rows), default=0),
        ),
        max(
            (item[1] for item in extents),
            default=max((row.end_ms for row in rows), default=0),
        ),
    )


def _payload(
    partition: Partition,
    rows: Sequence[TranscriptUtterance],
    generation: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    start, end = _bounds(partition.words, rows)
    if not any(word_extent(word) is not None for word in partition.words):
        start = metadata.get("utterance_start_ms", start)
        end = metadata.get("utterance_end_ms", end)
    return {
        **(dict(rows[0].confidence_payload or {}) if rows else {}),
        **metadata,
        **partition_source_metadata(partition.words),
        "utterance_start_ms": start,
        "utterance_end_ms": end,
        "asr_segments": [
            {
                "start_ms": start,
                "end_ms": end,
                "text": reconstruct_text_from_words(partition.words),
                "words": partition.words,
            }
        ],
        "asr_word_timestamps_available": any(
            word.get("start_ms") is not None for word in partition.words
        ),
        SETTLING_KEY: {
            "version": 1,
            "generation": generation,
            "text_locked": all(word.get("locked") for word in partition.words),
            "line_closed": partition.closed,
            "boundary_locked": partition.boundary_locked,
            "closure_reason": partition.closure_reason,
            "closed_at_ms": partition.closed_at_ms,
            "line_locked": partition.line_locked,
        },
        LIVE_SPEAKER_LABEL_PAYLOAD_KEY: {
            "locked": partition.speaker_locked,
            "locked_word_ids": partition.locked_speaker_words,
            "share": None,
        },
    }


def _event(session: Session, row: TranscriptUtterance, event_type: str) -> None:
    _append_utterance_event(
        session,
        utterance=row,
        event_type=event_type,
        source="live",
        old_values=None,
        new_values={"text": row.text, "speaker": row.speaker_label},
        resulting_revision=row.revision,
    )


@dataclass(frozen=True)
class PartitionWrite:
    """The held canonical rows and provenance of one scoped projection."""

    recording_id: int
    rows: list[TranscriptUtterance]
    generation: str
    metadata: dict[str, Any]
    decode_id: str | None = None


def write_partitions(
    session: Session,
    context: PartitionWrite,
    partitions: list[Partition],
) -> tuple[list[str], list[str], int]:
    """Update equal memberships in place, otherwise supersede only this tail."""
    recording_id, rows = context.recording_id, context.rows
    by_membership = {
        tuple(word.get("id") for word in row_words(row)): row for row in rows
    }
    owner_by_word = {word.get("id"): row for row in rows for word in row_words(row)}
    new_memberships = {
        tuple(word.get("id") for word in part.words) for part in partitions
    }
    tombstones: list[str] = []
    active_ids: list[str] = []
    changed = 0
    for membership, row in by_membership.items():
        if membership not in new_memberships:
            row.state = TranscriptUtteranceState.SUPERSEDED
            session.add(row)
            _event(session, row, "supersede")
            tombstones.append(row.public_id)
    for partition in partitions:
        matching = by_membership.get(tuple(word.get("id") for word in partition.words))
        same_memberships = matching is not None
        row = (
            matching
            if matching is not None
            else TranscriptUtterance(
                recording_id=recording_id,
                public_id=str(uuid4()),
                sort_key=f"{_bounds(partition.words, rows)[0]:020d}:{uuid4().hex[:8]}",
                start_ms=0,
                end_ms=0,
                text="",
                state=TranscriptUtteranceState.PROVISIONAL,
                source_kind="live",
                speaker_assignment_source="live",
                speaker_assignment_authority="provisional",
            )
        )
        source_rows = (
            [matching]
            if matching is not None
            else list(
                {
                    owner_by_word[word["id"]].id: owner_by_word[word["id"]]
                    for word in partition.words
                    if word["id"] in owner_by_word
                }.values()
            )
        )
        redecoded = context.decode_id is not None and any(
            word.get("last_decode_id") == context.decode_id for word in partition.words
        )
        metadata = context.metadata if redecoded or not source_rows else {}
        payload = _payload(partition, source_rows, context.generation, metadata)
        if (
            redecoded
            and not metadata.get("alignment_error")
            and any(word.get("start_ms") is not None for word in partition.words)
        ):
            payload.pop("alignment_error", None)
        start, end = _bounds(partition.words, rows)
        if not any(word.get("start_ms") is not None for word in partition.words):
            start, end = payload["utterance_start_ms"], payload["utterance_end_ms"]
        text = reconstruct_text_from_words(partition.words)
        public_changed = not same_memberships or (
            row.text,
            row.speaker_label,
            row.start_ms,
            row.end_ms,
            public_settling_fields(row.confidence_payload),
            source_channel_from_confidence_payload(row.confidence_payload),
            (row.confidence_payload or {}).get("alignment_error") or None,
            (row.confidence_payload or {}).get(LIVE_SPEAKER_LABEL_PAYLOAD_KEY),
        ) != (
            text,
            partition.speaker,
            start,
            end,
            public_settling_fields(payload),
            source_channel_from_confidence_payload(payload),
            payload.get("alignment_error") or None,
            payload.get(LIVE_SPEAKER_LABEL_PAYLOAD_KEY),
        )
        dirty = not same_memberships or (
            row.text,
            row.speaker_label,
            row.start_ms,
            row.end_ms,
            row.confidence_payload,
        ) != (text, partition.speaker, start, end, payload)
        active_ids.append(row.public_id)
        if not dirty:
            continue
        row.text, row.speaker_label, row.start_ms, row.end_ms = (
            text,
            partition.speaker,
            start,
            end,
        )
        row.confidence_payload = payload
        if same_memberships and public_changed:
            row.revision += 1
        session.add(row)
        session.flush()
        if public_changed:
            _event(session, row, "live_settling" if same_memberships else "created")
            changed += 1
    return tombstones, active_ids, changed + len(tombstones)
