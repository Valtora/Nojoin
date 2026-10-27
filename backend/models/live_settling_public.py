"""Public word and lock fields, excluding private comparison evidence."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class LiveWordPublicRead(BaseModel):
    """A word identity and its current exact wording and timing."""

    id: str
    order: int
    word: str
    start_ms: int | None
    end_ms: int | None
    source_start: int | None = None
    source_end: int | None = None
    join_left: bool | None = None
    locked: bool
    lock_reason: Literal["agreement", "expiry", "closure"] | None = None


class LiveSettlingPublicRead(BaseModel):
    """Optional settling facts, absent from older recordings' public contract."""

    words: list[LiveWordPublicRead] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    text_locked: bool | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    locked: bool | None = Field(default=None, exclude_if=lambda value: value is None)


def public_settling_fields(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Expose only typed word facts for lines carrying the settling marker."""
    payload = payload or {}
    metadata = payload.get("live_text_settling")
    if not isinstance(metadata, dict):
        return {}
    words = [
        LiveWordPublicRead.model_validate(word).model_dump()
        for segment in payload.get("asr_segments") or []
        for word in segment.get("words") or []
    ]
    return {
        "words": words,
        "text_locked": bool(words) and all(word["locked"] for word in words),
        "locked": bool(metadata.get("line_locked")),
    }


def public_transcript_segment(segment: dict[str, Any]) -> dict[str, Any]:
    """Strip private evidence while preserving the typed settling projection."""
    return {
        **{key: value for key, value in segment.items() if key != "confidence_payload"},
        **public_settling_fields(segment.get("confidence_payload")),
    }
