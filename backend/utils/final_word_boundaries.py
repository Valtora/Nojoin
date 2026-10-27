"""Stable split identities derived from final evidence without rewriting it."""

from typing import Any
from uuid import NAMESPACE_URL, uuid5

from backend.utils.word_text import reconstruct_text_from_words


def final_words(
    payload: dict[str, Any], utterance_public_id: str, *, text: str | None = None
) -> list[dict[str, Any]]:
    """Derive repeatable UUIDs at real boundaries; the stored evidence stays intact."""
    result = []
    previous: dict[str, Any] = {}
    for source in payload.get("asr_segments") or []:
        for word in source.get("words") or []:
            order = len(result)
            identity = f"final-word:{utterance_public_id}:{order}:{word.get('start_ms')}:{word.get('end_ms')}:{word.get('word')}"
            boundary = {
                **word,
                "id": word.get("id") or str(uuid5(NAMESPACE_URL, identity)),
                "order": order,
                "locked": True,
            }
            # Forced alignment can return adjacent fragments of one lexical word.
            # A leading space is real separation even when source offsets touch.
            if (
                word.get("source_id")
                and word.get("source_id") == previous.get("source_id")
                and word.get("source_start") is not None
                and word.get("source_start") == previous.get("source_end")
                and not str(word.get("word") or "")[:1].isspace()
            ) or str(word.get("word") or "").strip() in {"n't", "n’t"}:
                boundary["join_left"] = True
            result.append(boundary)
            previous = word
    if text is not None and reconstruct_text_from_words(result).split() != text.split():
        # Older paths can retain a previous ASR result's evidence. It is not a
        # valid boundary map for the current line's different wording.
        return []
    return result
