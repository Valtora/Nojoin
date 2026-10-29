"""Recording-level settling provenance and human text protection."""

from typing import Any

from backend.models.live_settling_public import public_settling_fields
from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceState
from backend.models.transcript import Transcript


def settling_required(transcript: Transcript | None) -> bool:
    """Retain the settled lane after finalisation, resets and device changes."""
    state = getattr(transcript, "settling_state", None) or {}
    return state.get("settled") is True or bool(
        state.get("eligible") and state.get("policy", {}).get("enabled") is True
    )


def line_still_settling(
    transcript: Transcript | None, row: TranscriptUtterance
) -> bool:
    """Whether a line still waits for its lock, exactly as public reads show it.

    The client holds a line's actions back on the same ``locked`` field, so a
    line the server refuses is one the client also shows as settling.
    """
    provisional = row.state == TranscriptUtteranceState.PROVISIONAL
    fields = public_settling_fields(
        row.confidence_payload,
        provisional=settling_required(transcript) and provisional,
    )
    return settling_required(transcript) and fields.get("locked") is False


def freeze_live_line(row: TranscriptUtterance) -> None:
    """Mark an open live line locked once a person's lock holds it.

    The client waits for a line to lock before acting on it, but an action's
    scope can reach further: assigning a speaker for the whole transcript
    also locks that speaker's lines still settling. The manual lock already
    keeps later decodes off them; this makes them read as locked too
    (``public_settling_fields``), so their words stop showing as settling.
    The stored words keep their own locks: word settling takes locked words
    as a time prefix, and an earlier line may still be settling. For the same
    reason the line is not marked ``text_locked``, which sets the settled
    frontier.
    """
    from .live_settling_projection import SETTLING_KEY

    payload = dict(row.confidence_payload or {})
    metadata = payload.get(SETTLING_KEY)
    if (
        not (row.manual_text_locked or row.manual_speaker_locked)
        or row.state != TranscriptUtteranceState.PROVISIONAL
        or not isinstance(metadata, dict)
        or metadata.get("line_locked")
    ):
        return
    row.confidence_payload = {
        **payload,
        SETTLING_KEY: {**metadata, "line_locked": True},
    }


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
