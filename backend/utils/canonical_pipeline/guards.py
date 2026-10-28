"""Recording-level settling provenance and human text protection."""

from typing import Any

from backend.models.pipeline import TranscriptUtterance
from backend.models.transcript import Transcript


def settling_required(transcript: Transcript | None) -> bool:
    """Retain the settled lane after finalisation, resets and device changes."""
    state = getattr(transcript, "settling_state", None) or {}
    return state.get("settled") is True or bool(
        state.get("eligible") and state.get("policy", {}).get("enabled") is True
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
