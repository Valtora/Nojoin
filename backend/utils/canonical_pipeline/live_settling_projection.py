"""Scoped word partitions and canonical events for the marked live lane."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from itertools import groupby
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
from .live_settling_sources import partition_source_metadata, words_source_channel

SETTLING_KEY = "live_text_settling"
DecideSpeaker = Callable[[int, int], LiveLabelDecision]


def line_is_locked(row: TranscriptUtterance) -> bool:
    """Whether machine work must retain this complete canonical line."""
    return bool((row.confidence_payload or {}).get(SETTLING_KEY, {}).get("line_locked"))


def row_words(row: TranscriptUtterance) -> list[dict[str, Any]]:
    """Read the stored ordered words without changing their dictionaries."""
    if row.state == TranscriptUtteranceState.FINALIZED and not (
        row.confidence_payload or {}
    ).get("human_protection"):
        from backend.utils.final_word_boundaries import final_words

        return final_words(row.confidence_payload or {}, row.public_id, text=row.text)
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


LeadSpeaker = Callable[[str | None], str | None]


@dataclass(frozen=True)
class Closing:
    """A close seals the tail; pending words continue ``lead_speaker`` if no word does."""

    lead_speaker: LeadSpeaker


def lead_speaker(
    session: Session, recording_id: int, rows: Sequence[TranscriptUtterance]
) -> LeadSpeaker:
    """Look up the speaker of the newest immutable live line before the tail, by source.

    A line's source is the badge it shows (``None`` when unattributed), as the
    transcript compares sources before continuing a speaker.
    """
    tail = [row for row in rows if mutable_row(row)]

    def speaker_from(channel: str | None) -> str | None:
        if not tail:
            return None
        lines = session.execute(
            select(
                TranscriptUtterance.speaker_label,
                TranscriptUtterance.confidence_payload,
            )
            .where(TranscriptUtterance.recording_id == recording_id)
            .where(TranscriptUtterance.state == TranscriptUtteranceState.PROVISIONAL)
            .where(TranscriptUtterance.source_kind == "live")
            .where(col(TranscriptUtterance.id).not_in([row.id for row in tail]))
            .where(TranscriptUtterance.start_ms <= tail[0].start_ms)
            .order_by(
                col(TranscriptUtterance.start_ms).desc(),
                col(TranscriptUtterance.id).desc(),
            )
        )
        return next(
            (
                speaker
                for speaker, payload in lines
                if source_channel_from_confidence_payload(payload) == channel
            ),
            None,
        )

    return speaker_from


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
    closing: Closing | None = None,
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
    covered_untimed: dict[int, LiveLabelDecision] = {}
    for word in words:
        old = previous.get(word.get("id"))
        start, end = word.get("start_ms"), word.get("end_ms")
        if old is not None and word.get("id") in immutable_speakers:
            decision = old
        elif decide is not None and start is not None and end is not None:
            decision = decide(int(start), max(int(start) + 1, int(end)))
        elif start is not None and end is not None:
            decision = old or LiveLabelDecision(
                "UNKNOWN", not speaker_enabled, None, speaker_enabled
            )
        else:
            extent = word_extent(word)
            if decide is not None and extent is not None:
                coverage = decide(extent[0], max(extent[0] + 1, extent[1]))
                decision = coverage if coverage.diarisation_pending else None
                if not coverage.diarisation_pending:
                    covered_untimed[len(decisions)] = LiveLabelDecision(
                        "UNKNOWN", coverage.locked, None
                    )
            else:
                decision = old
        decisions.append(decision)
    for index, decision in enumerate(decisions):
        if decision is None:
            neighbour = next(
                (
                    candidate
                    for j in range(index - 1, -1, -1)
                    if (candidate := decisions[j]) is not None
                    and (
                        index not in covered_untimed
                        or not candidate.diarisation_pending
                    )
                ),
                None,
            ) or next(
                (
                    item
                    for item in decisions[index + 1 :]
                    if item is not None
                    and (index not in covered_untimed or not item.diarisation_pending)
                ),
                None,
            )
            decisions[index] = (
                neighbour
                or covered_untimed.get(index)
                or LiveLabelDecision(
                    "UNKNOWN", not speaker_enabled, None, speaker_enabled
                )
            )
    decided = [item for item in decisions if item is not None]
    if closing is not None:
        return _sealed(words, decided, closing)
    return [
        LiveLabelDecision(
            "UNKNOWN" if item.diarisation_pending else item.speaker,
            item.locked,
            item.share,
            item.diarisation_pending,
        )
        for item in decided
    ]


def _sealed(
    words: Sequence[dict[str, Any]],
    decisions: list[LiveLabelDecision],
    closing: Closing,
) -> list[LiveLabelDecision]:
    """Seal each pending run with the nearest earlier decision from its audio source.

    The transcript continues a speaker only within one source, so a microphone
    tail never takes a shared-audio speaker. The final pass corrects a wrong
    guess; Unknown remains only with nothing before it from that source.
    """
    latest: dict[str | None, str] = {}
    sealed: list[LiveLabelDecision] = []
    for (pending, _), group in groupby(zip(words, decisions, strict=True), key=_run):
        run = list(group)
        channel = words_source_channel([word for word, _ in run])
        if pending:
            speaker = latest.get(channel) or closing.lead_speaker(channel) or "UNKNOWN"
        else:
            speaker = run[0][1].speaker
            latest[channel] = speaker
        sealed.extend(LiveLabelDecision(speaker, True, item.share) for _, item in run)
    return sealed


def _run(pair: tuple[dict[str, Any], LiveLabelDecision]) -> tuple[bool, str | None]:
    """Pending words form one run; decided words run while their speaker holds."""
    item = pair[1]
    return item.diarisation_pending, None if item.diarisation_pending else item.speaker


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
        bool(stored.get("diarisation_pending")),
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
            "diarisation_pending": partition.diarisation_pending,
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


def _partition_rows(
    rows: list[TranscriptUtterance], partitions: list[Partition]
) -> list[TranscriptUtterance | None]:
    """Retain an open line on growth, but never carry its identity over a split or merge."""
    memberships = [tuple(word.get("id") for word in row_words(row)) for row in rows]
    incoming = [tuple(word.get("id") for word in part.words) for part in partitions]
    exact = dict(zip(memberships, rows, strict=True))
    matches = [exact.get(membership) for membership in incoming]
    retained = {row.public_id for row in matches if row is not None}
    owners = {
        word_id: index for index, words in enumerate(memberships) for word_id in words
    }
    contributors = [
        {owners[word_id] for word_id in words if word_id in owners}
        for words in incoming
    ]
    destinations = [
        {index for index, sources in enumerate(contributors) if row_index in sources}
        for row_index in range(len(rows))
    ]
    for index, sources in enumerate(contributors):
        if matches[index] is not None or len(sources) != 1:
            continue
        source = next(iter(sources))
        row = rows[source]
        metadata = (row.confidence_payload or {}).get(SETTLING_KEY, {})
        if (
            row.public_id not in retained
            and destinations[source] == {index}
            and memberships[source]
            and incoming[index]
            and memberships[source][0] == incoming[index][0]
            and mutable_row(row)
            and not metadata.get("line_closed")
        ):
            matches[index] = row
            retained.add(row.public_id)
    return matches


def write_partitions(
    session: Session,
    context: PartitionWrite,
    partitions: list[Partition],
) -> tuple[list[str], list[str], int]:
    """Update continuing open lines in place; supersede split or merged tails."""
    recording_id, rows = context.recording_id, context.rows
    matches = _partition_rows(rows, partitions)
    retained_ids = {row.public_id for row in matches if row is not None}
    owner_by_word = {word.get("id"): row for row in rows for word in row_words(row)}
    tombstones: list[str] = []
    active_ids: list[str] = []
    changed = 0
    for row in rows:
        if row.public_id not in retained_ids:
            row.state = TranscriptUtteranceState.SUPERSEDED
            session.add(row)
            _event(session, row, "supersede")
            tombstones.append(row.public_id)
    for partition, matching in zip(partitions, matches, strict=True):
        retained = matching is not None
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
        public_changed = not retained or (
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
        dirty = not retained or (
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
        if retained and public_changed:
            row.revision += 1
        session.add(row)
        session.flush()
        if public_changed:
            _event(session, row, "live_settling" if retained else "created")
            changed += 1
    return tombstones, active_ids, changed + len(tombstones)
