"""Public word and lock fields, excluding private comparison evidence."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.utils.final_word_boundaries import final_words


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


class FinalReviewPublicRead(BaseModel):
    """Machine wording offered once beside an ambiguously remapped human line."""

    text: str
    reason: Literal["ambiguous_alignment"]


class LiveSettlingPublicRead(BaseModel):
    """Live settling facts, or timed final word identities on settled recordings."""

    words: list[LiveWordPublicRead] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    text_locked: bool | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    locked: bool | None = Field(default=None, exclude_if=lambda value: value is None)
    final_review: FinalReviewPublicRead | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


def public_settling_fields(
    payload: dict[str, Any] | None,
    *,
    final_utterance_id: str | None = None,
    final_text: str | None = None,
) -> dict[str, Any]:
    """Expose typed live facts or final boundaries; never private comparisons."""
    payload = payload or {}
    review = payload.get("final_review")
    public_review = (
        {"final_review": FinalReviewPublicRead.model_validate(review).model_dump()}
        if isinstance(review, dict)
        else {}
    )
    metadata = payload.get("live_text_settling")
    protected = bool(payload.get("human_protection"))
    if not isinstance(metadata, dict) and not protected:
        if final_utterance_id:
            words = final_words(payload, final_utterance_id, text=final_text)
            if words:
                return {
                    **public_review,
                    "words": [
                        LiveWordPublicRead.model_validate(word).model_dump()
                        for word in words
                    ],
                }
        return public_review
    # A locked line's words read as locked, including a line a person froze
    # while its words were still settling (``freeze_live_line``).
    line_locked = bool((metadata or {}).get("line_locked"))
    words = [
        LiveWordPublicRead.model_validate(
            {**word, "locked": True} if line_locked else word
        ).model_dump()
        for segment in payload.get("asr_segments") or []
        for word in segment.get("words") or []
    ]
    return {
        **public_review,
        "words": words,
        "text_locked": bool(words) and all(word["locked"] for word in words),
        "locked": protected or line_locked,
    }


def public_transcript_segment(
    segment: dict[str, Any], *, include_final_words: bool = False
) -> dict[str, Any]:
    """Strip private evidence while preserving the typed settling projection."""
    return {
        **{key: value for key, value in segment.items() if key != "confidence_payload"},
        **public_settling_fields(
            segment.get("confidence_payload"),
            final_utterance_id=segment.get("id")
            if include_final_words and not segment.get("provisional")
            else None,
            final_text=segment.get("text"),
        ),
    }
