"""Apply inferred speaker names as pending until a person confirms them.

A self-introduction or the LLM's pick shows at once, as #65 made it, but as a
pending name ("Alex?"): the speaker's local name, with the source that gave it.
It links nobody in People and offers no voiceprint until the user confirms it
(``routes_name_review``), because only a person's act may link People or teach
a library voiceprint. A name the user dismissed for this speaker is not
applied again.

With the owner's ``confirm_suggested_names`` setting on, a name is taken as
given instead (``name_source='auto'``): an exact People name links that person
and offers the voiceprint, as #65 did.

Each applied name is also written as a name event, anchored on the turn it was
given for, with the suggestion as its evidence.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Sequence

from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.speaker_name_event import SpeakerNameEvent
from backend.utils.canonical_pipeline.identity_fields import (
    apply_recording_speaker_identity_fields,
)
from backend.utils.speaker_name_suggestions import SPEAKER_SUGGESTION_STATUS_ACCEPTED
from backend.utils.speaker_names.anchors import Line, longest_turn, turn_nearest
from backend.utils.speaker_names.events import add_event, speaker_lines
from backend.utils.speaker_names.preferences import (
    confirm_suggested_names_enabled,
    library_learns_automatically,
    owner_settings,
)
from backend.utils.speaker_names.review import dismissed_names
from backend.utils.speaker_names.sources import (
    AUTHORITY,
    KIND_NAME,
    SOURCE_AUTO,
    SOURCE_HEURISTIC,
    suggestion_source,
)

logger = logging.getLogger(__name__)


def _suggested_person(session: Any, suggestion: dict[str, Any]) -> GlobalSpeaker | None:
    raw = suggestion.get("suggested_global_speaker_id")
    # Person ids are integer keys; bool is an int subclass, so it is refused.
    if not isinstance(raw, int) or isinstance(raw, bool):
        return None
    return session.get(GlobalSpeaker, raw)


def _confidence(suggestion: dict[str, Any]) -> float | None:
    raw = suggestion.get("confidence")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    return None


def _anchor(
    lines: Sequence[Line], speaker: RecordingSpeaker, suggestion: dict[str, Any]
) -> tuple[int, int] | None:
    """The turn the name was given for.

    A self-introduction is the line itself. The LLM's evidence is often
    someone else addressing the speaker, so its name goes on the speaker's
    turn nearest that line. Without evidence, the speaker's longest turn.
    """
    for span in suggestion.get("evidence_spans") or []:
        start, end = span.get("start_seconds"), span.get("end_seconds")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            continue
        start_ms, end_ms = int(round(start * 1000)), int(round(end * 1000))
        if end_ms <= start_ms:
            continue
        if suggestion_source(suggestion) == SOURCE_HEURISTIC:
            return start_ms, end_ms
        return turn_nearest(lines, speaker.id, start_ms, end_ms)
    return longest_turn(lines, speaker.id)


def _mark_auto_applied(suggestion: dict[str, Any]) -> None:
    """Record a name taken as given; a pending one stays "pending" until answered."""
    timestamp = datetime.now(UTC).isoformat()
    suggestion["status"] = SPEAKER_SUGGESTION_STATUS_ACCEPTED
    suggestion["updated_at"] = timestamp
    suggestion["resolved_at"] = timestamp
    suggestion["resolution_reason"] = "auto_applied"
    suggestion["resolution_actor_user_id"] = None


def _record_event(
    session: Any,
    speaker: RecordingSpeaker,
    suggestion: dict[str, Any],
    source: str,
    lines: Sequence[Line],
) -> None:
    anchor = _anchor(lines, speaker, suggestion)
    if anchor is None:
        return
    add_event(
        session,
        speaker,
        SpeakerNameEvent(
            recording_id=speaker.recording_id,
            kind=KIND_NAME,
            name=str(suggestion.get("suggested_name") or "").strip(),
            global_speaker_id=suggestion.get("suggested_global_speaker_id")
            if isinstance(suggestion.get("suggested_global_speaker_id"), int)
            else None,
            source=source,
            authority=AUTHORITY[source],
            surface="worker",
            anchor_start_ms=anchor[0],
            anchor_end_ms=anchor[1],
            confidence=_confidence(suggestion),
            evidence=dict(suggestion),
        ),
    )


def apply_inferred_names(
    session: Any,
    recording: Any,
    speakers: Sequence[RecordingSpeaker],
    persisted: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply each suggestion to its speaker; return those applied."""
    settings = owner_settings(session, recording.user_id)
    confirm = confirm_suggested_names_enabled(settings)
    # A name taken as given is no confirmation: it teaches the person's
    # voiceprint only when the owner lets unconfirmed matches teach it.
    fold_alpha = 0.3 if library_learns_automatically(settings) else None
    lines = speaker_lines(session, recording.id)
    speakers_by_id = {speaker.id: speaker for speaker in speakers}
    applied: list[dict[str, Any]] = []
    for suggestion in persisted:
        name = str(suggestion.get("suggested_name") or "").strip()
        speaker = speakers_by_id.get(suggestion.get("recording_speaker_id"))
        if speaker is None or not name:
            continue
        if name.casefold() in dismissed_names(session, speaker.id):
            logger.info(
                "Not applying %s to %s: the user dismissed it",
                name,
                speaker.diarization_label,
            )
            continue
        # Pending: the name shows, no one is linked and no voiceprint is
        # offered. Taken as given: an exact People name links that person.
        person = _suggested_person(session, suggestion) if confirm else None
        source = SOURCE_AUTO if confirm else suggestion_source(suggestion)
        placeholder = speaker.name
        apply_recording_speaker_identity_fields(
            session,
            speaker,
            new_speaker_name=name,
            target_global_speaker=person,
            merge_global_embedding_alpha=fold_alpha if person is not None else None,
            identity_confidence=_confidence(suggestion),
            name_source=source,
        )
        if confirm:
            _mark_auto_applied(suggestion)
        else:
            # The row keeps the "Speaker N" this run gave it, which Not
            # {name} gives back (``placeholder_speaker_name``).
            speaker.name = placeholder
        _record_event(session, speaker, suggestion, source, lines)
        applied.append(suggestion)
    return applied
