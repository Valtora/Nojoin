"""Conservative final remapping of accepted human work on settled recordings."""

from collections import Counter, defaultdict
from copy import deepcopy
from typing import Any, Sequence

from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceState
from backend.utils.final_word_boundaries import final_words


def line_is_protected(line: TranscriptUtterance) -> bool:
    """Edited wording and explicit split boundaries outlive machine segmentation.

    A speaker-only correction protects the speaker, not the words: the final
    wording replaces the line, and the finaliser carries the chosen speaker
    onto the final segments that overlap it.
    """
    return bool(
        line.manual_text_locked
        or (line.confidence_payload or {}).get("human_protection")
    )


def _bounds(segment: dict[str, Any]) -> tuple[int, int]:
    return round(float(segment.get("start", 0)) * 1000), round(
        float(segment.get("end", 0)) * 1000
    )


def _overlaps(first: tuple[int, int], second: tuple[int, int]) -> bool:
    return max(first[0], second[0]) < min(first[1], second[1]) or first == second


def _retained_segment(line: TranscriptUtterance) -> dict[str, Any]:
    payload = deepcopy(line.confidence_payload or {})
    if line.state == TranscriptUtteranceState.FINALIZED and not payload.get(
        "human_protection"
    ):
        # Protected evidence is read verbatim; persist the same identities
        # the final line exposed before it was locked.
        words = final_words(payload, line.public_id, text=line.text)
        offset = 0
        for source in payload.get("asr_segments") or []:
            count = len(source.get("words") or [])
            source["words"] = words[offset : offset + count]
            offset += count
    payload.setdefault("human_protection", {"version": 1, "retained": True})
    return {
        "id": line.public_id,
        "start": line.start_ms / 1000,
        "end": line.end_ms / 1000,
        "text": line.text,
        "speaker": line.speaker_label or "UNKNOWN",
        "recording_speaker_id": line.recording_speaker_id,
        "text_manually_edited": line.manual_text_locked,
        "speaker_manually_edited": line.manual_speaker_locked,
        "text_edit_source": line.text_last_edit_source,
        "speaker_edit_source": line.speaker_last_edit_source,
        "confidence_payload": payload,
        "segment_source": line.source_kind,
    }


def _with_final_review(
    segment: dict[str, Any], line: TranscriptUtterance, text: str
) -> dict[str, Any]:
    """Offer a differing final wording, since the finaliser keeps the line's own."""
    passed = dict(segment)
    if text and text != (line.text or "").strip():
        passed["confidence_payload"] = {
            **(segment.get("confidence_payload") or {}),
            "final_review": {"text": text, "reason": "ambiguous_alignment"},
        }
    return passed


def preserve_human_segments(
    lines: Sequence[TranscriptUtterance], segments: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Retain protected lines and expose the final wording over them only once.

    A final segment whose bounds match one protected line exactly, and the
    only one touching it, goes to the ordinary finaliser, which pins the
    accepted values; its wording is offered for review when it differs from
    the kept text. Otherwise each final segment that directly overlaps a
    protected line is taken as a review candidate on the first line it
    overlaps, so a merged segment across a split is offered once rather than
    duplicated beside the kept text. Every other final segment replaces the
    live lines as usual.
    """
    protected = [index for index, line in enumerate(lines) if line_is_protected(line)]
    if not protected:
        return [dict(segment) for segment in segments]
    line_bounds = {
        index: (lines[index].start_ms, lines[index].end_ms) for index in protected
    }
    segment_bounds = [_bounds(segment) for segment in segments]
    hits = [
        [index for index in protected if _overlaps(line_bounds[index], bounds)]
        for bounds in segment_bounds
    ]
    touches = Counter(index for found in hits for index in found)
    exact = {
        found[0]
        for bounds, found in zip(segment_bounds, hits)
        if len(found) == 1
        and touches[found[0]] == 1
        and line_bounds[found[0]] == bounds
    }
    reviews: dict[int, list[str]] = defaultdict(list)
    result: list[dict[str, Any]] = []
    for segment, found in zip(segments, hits):
        text = str(segment.get("text") or "").strip()
        if not found:
            result.append(dict(segment))
        elif found[0] in exact:
            result.append(_with_final_review(segment, lines[found[0]], text))
        elif text:
            reviews[found[0]].append(text)
    for index in protected:
        if index in exact:
            continue
        kept = _retained_segment(lines[index])
        kept["confidence_payload"].pop("final_review", None)
        if reviews[index]:
            kept["confidence_payload"]["final_review"] = {
                "text": " ".join(reviews[index]),
                "reason": "ambiguous_alignment",
            }
        result.append(kept)
    return sorted(
        result, key=lambda segment: (*_bounds(segment), str(segment.get("id", "")))
    )


def protected_final_payload(
    segment: dict[str, Any], existing: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Keep human word atoms and split boundaries rather than machine word spans."""
    incoming = segment.get("confidence_payload") or {}
    payload = deepcopy(existing or {})
    payload.pop("final_review", None)
    if isinstance(incoming, dict):
        payload.update(deepcopy(incoming))
    payload.pop("live_text_settling", None)
    payload.pop("live_speaker_label", None)
    return payload or None
