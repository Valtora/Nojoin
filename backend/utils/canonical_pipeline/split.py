"""A human word-boundary split, published atomically with ordinary deltas."""

import copy
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceState
from backend.utils.word_text import reconstruct_text_from_words

from .constants import _append_utterance_event, _get_utterance, _load_transcript
from .guards import LineActionConflict, require_action_lines
from .live_settling_projection import row_words


def _split_index(words: list[dict[str, Any]], before_word_id: str) -> int:
    try:
        if str(UUID(before_word_id)) != before_word_id:
            raise ValueError
    except ValueError as error:
        raise LineActionConflict(
            "line_stale", "Choose a current word boundary to split."
        ) from error
    index = next(
        (i for i, word in enumerate(words) if word.get("id") == before_word_id), -1
    )
    if index <= 0:
        raise LineActionConflict(
            "line_stale", "Choose a word inside the line to split."
        )
    candidate = words[index]
    lexical = str(candidate.get("word") or "").strip()
    timed_left = any(
        word.get("start_ms") is not None
        and re.search(r"\w", str(word.get("word") or ""))
        for word in words[:index]
    )
    if (
        candidate.get("start_ms") is None
        or candidate.get("end_ms") is None
        or not timed_left
    ):
        raise LineActionConflict(
            "line_stale", "Untimed replacement text cannot be split."
        )
    if candidate.get("join_left") or not re.match(r"\w", lexical):
        raise LineActionConflict("line_stale", "Split between complete words.")
    return index


def _child(
    parent: TranscriptUtterance,
    words: list[dict[str, Any]],
    protection: dict[str, Any],
    side: int,
) -> TranscriptUtterance:
    text = reconstruct_text_from_words(words)
    start = min(
        int(word["start_ms"]) for word in words if word.get("start_ms") is not None
    )
    end = max(int(word["end_ms"]) for word in words if word.get("end_ms") is not None)
    payload = copy.deepcopy(parent.confidence_payload or {})
    payload["human_protection"] = protection
    payload["source_public_ids"] = [parent.public_id]
    payload["asr_segments"] = [
        {"start_ms": start, "end_ms": end, "text": text, "words": words}
    ]
    payload["utterance_start_ms"], payload["utterance_end_ms"] = start, end
    values = parent.model_dump(
        exclude={
            "id",
            "public_id",
            "created_at",
            "updated_at",
            "revision",
            "sort_key",
            "start_ms",
            "end_ms",
            "text",
            "confidence_payload",
        }
    )
    return TranscriptUtterance(
        **values,
        public_id=str(uuid4()),
        revision=1,
        sort_key=f"{parent.sort_key}:{side}",
        start_ms=start,
        end_ms=end,
        text=text,
        confidence_payload=payload,
    )


@dataclass(frozen=True)
class SplitCommand:
    """The source version and chosen boundary of one user split."""

    recording_id: int
    utterance_public_id: str
    before_word_id: str
    expected_revision: int
    actor_user_id: int | None = None


def split_utterance(
    session: Session, command: SplitCommand
) -> list[TranscriptUtterance]:
    """Reject a stale/settling source before superseding it or minting children."""
    from .core import refresh_transcript_projection_from_canonical

    transcript = _load_transcript(session, command.recording_id, lock=True)
    parent = _get_utterance(
        session, command.recording_id, command.utterance_public_id, lock=True
    )
    if transcript is None or parent is None:
        raise LookupError("Utterance not found")
    if (
        parent.state
        in {TranscriptUtteranceState.SUPERSEDED, TranscriptUtteranceState.DELETED}
        or parent.revision != command.expected_revision
    ):
        raise LineActionConflict(
            "line_stale", "This line changed. Reload it and try again."
        )
    require_action_lines(transcript, [parent])
    words = row_words(parent)
    index = _split_index(words, command.before_word_id)
    group = str(uuid4())
    protection = {
        "version": 1,
        "split_group_id": group,
        "source_public_id": parent.public_id,
        "split_before_word_id": command.before_word_id,
    }
    children = [
        _child(parent, part, {**protection, "side": side}, ordinal)
        for ordinal, (part, side) in enumerate(
            ((words[:index], "left"), (words[index:], "right"))
        )
    ]
    parent.state = TranscriptUtteranceState.SUPERSEDED
    parent.revision += 1
    session.add(parent)
    _append_utterance_event(
        session,
        utterance=parent,
        event_type="supersede",
        source="api",
        actor_user_id=command.actor_user_id,
        resulting_revision=parent.revision,
        old_values={"state": "active"},
        new_values={"state": "superseded"},
    )
    for child in children:
        session.add(child)
        session.flush()
        _append_utterance_event(
            session,
            utterance=child,
            event_type="split",
            source="api",
            actor_user_id=command.actor_user_id,
            resulting_revision=child.revision,
            old_values=None,
            new_values={"source_public_id": parent.public_id},
        )
    refresh_transcript_projection_from_canonical(session, command.recording_id)
    return children
