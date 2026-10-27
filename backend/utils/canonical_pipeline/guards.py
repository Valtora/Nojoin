"""Shared human-action eligibility under the canonical publication fence."""

from collections.abc import Iterable
from typing import Any

from sqlalchemy.orm import Session

from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceState
from backend.models.transcript import Transcript

from .constants import ACTIVE_UTTERANCE_STATES


class LineActionConflict(RuntimeError):
    """A source line changed or is still settling; API adapters expose a 409."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code

    @property
    def detail(self) -> dict[str, str]:
        return {"code": self.code, "message": str(self)}


def settling_required(transcript: Transcript | None) -> bool:
    """Retain action policy after finalisation, resets and device changes."""
    state = getattr(transcript, "settling_state", None) or {}
    return state.get("settled") is True or bool(
        state.get("eligible") and state.get("policy", {}).get("enabled") is True
    )


def line_effectively_locked(row: TranscriptUtterance) -> bool:
    """Final output is fixed; live words require both text and speaker locks."""
    metadata = (row.confidence_payload or {}).get("live_text_settling")
    if isinstance(metadata, dict):
        return metadata.get("line_locked") is True
    return row.state != TranscriptUtteranceState.PROVISIONAL


def require_action_lines(
    transcript: Transcript | None,
    rows: Iterable[TranscriptUtterance],
    *,
    revisions: dict[str, int] | None = None,
) -> None:
    """Preflight a complete scope before any row, alias or name is changed."""
    if not settling_required(transcript):
        return
    for row in rows:
        if row.state not in ACTIVE_UTTERANCE_STATES:
            raise LineActionConflict(
                "line_stale", "This line changed. Reload it and try again."
            )
        if revisions is not None and row.public_id in revisions:
            if row.revision != revisions[row.public_id]:
                raise LineActionConflict(
                    "line_stale", "This line changed. Reload it and try again."
                )
        if not line_effectively_locked(row):
            raise LineActionConflict(
                "line_settling", "This line is still settling. Try again once it locks."
            )


def require_source_line(
    session: Session,
    transcript: Transcript | None,
    public_id: str,
    revision: int | None = None,
) -> TranscriptUtterance | None:
    """Resolve a supplied source identity after the caller takes Transcript."""
    from .constants import _get_utterance

    if transcript is None:
        return None
    row = _get_utterance(session, transcript.recording_id, public_id, lock=True)
    if row is None and settling_required(transcript):
        raise LineActionConflict(
            "line_stale", "This line changed. Reload it and try again."
        )
    if row is not None:
        revisions = {public_id: revision} if revision is not None else None
        require_action_lines(transcript, [row], revisions=revisions)
    return row


def conflict_detail(error: RuntimeError) -> str | dict[str, str]:
    """Keep legacy revision errors unchanged while exposing actionable reasons."""
    return error.detail if isinstance(error, LineActionConflict) else str(error)


def require_label_lines(
    session: Session, recording_id: int, label: str
) -> Transcript | None:
    """Fence and preflight a whole-speaker command before identity side effects."""
    from .constants import _load_transcript
    from .scope import _lock_active_lines

    transcript = _load_transcript(session, recording_id, lock=True)
    rows = _lock_active_lines(session, recording_id)
    require_action_lines(
        transcript, [row for row in rows if row.speaker_label == label]
    )
    return transcript


def require_recording_lines(session: Session, recording_id: int) -> Transcript | None:
    """Preflight a whole-recording compatibility action before caller side effects."""
    from .constants import _load_transcript
    from .scope import _lock_active_lines

    transcript = _load_transcript(session, recording_id, lock=True)
    require_action_lines(transcript, _lock_active_lines(session, recording_id))
    return transcript


def require_compatibility_lines(
    transcript: Transcript,
    rows: list[TranscriptUtterance],
    segments: list[dict[str, Any]],
) -> None:
    """Validate the complete displaced or changed scope before compatibility writes."""
    if not settling_required(transcript):
        return
    revisions = {
        str(segment["id"]): int(segment["revision"])
        for segment in segments
        if segment.get("revision") is not None
    }
    require_action_lines(transcript, rows, revisions=revisions)
    incoming = [str(segment.get("id") or "") for segment in segments]
    if len(set(incoming)) != len(segments) or set(incoming) != {
        row.public_id for row in rows
    }:
        raise LineActionConflict(
            "line_stale",
            "This transcript changed. Reload it and use the line split action to change boundaries.",
        )


def protect_text_replacement(row: TranscriptUtterance, text: str) -> None:
    """Human text has no invented word timings and cannot be split internally."""
    from uuid import uuid4

    from .live_settling_projection import row_words

    payload = dict(row.confidence_payload or {})
    old_protection = dict(payload.get("human_protection") or {})
    previous = old_protection.get("replacement") or {}
    source_ids = previous.get("source_word_ids") or [
        word["id"] for word in row_words(row) if word.get("id")
    ]
    payload["human_protection"] = {
        **old_protection,
        "version": 1,
        "replacement": {
            "text": text,
            "source_word_ids": source_ids,
            "start_ms": row.start_ms,
            "end_ms": row.end_ms,
        },
    }
    atom: dict[str, Any] = {
        "id": str(uuid4()),
        "order": 0,
        "word": text,
        "start_ms": None,
        "end_ms": None,
        "locked": True,
    }
    payload["asr_segments"] = [
        {"start_ms": row.start_ms, "end_ms": row.end_ms, "text": text, "words": [atom]}
    ]
    payload["asr_word_timestamps_available"] = False
    row.confidence_payload = payload
