"""Conservative final remapping of accepted human work on settled recordings."""

from collections import Counter, defaultdict
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from itertools import groupby
from operator import itemgetter
from typing import Any, Sequence, TypeVar

from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceState
from backend.utils.final_word_boundaries import final_words
from backend.utils.live_speaker_labels import live_speaker_display_name
from backend.utils.word_text import reconstruct_text_from_words
from backend.utils.word_times import has_word_timing

T = TypeVar("T")


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


def _speaker_chosen(line: TranscriptUtterance) -> bool:
    """A speaker a person chose for the line, which keeps it as it is."""
    return bool(line.manual_speaker_locked) and line.recording_speaker_id is not None


@dataclass
class _Reviews:
    """The final words over each protected line, and whose speech they were."""

    texts: dict[int, list[str]] = field(default_factory=lambda: defaultdict(list))
    # Milliseconds of those words each final speaker holds, UNKNOWN left out.
    speakers: dict[int, Counter[str]] = field(
        default_factory=lambda: defaultdict(Counter)
    )

    def add(self, index: int, text: str, speaker: object, held_ms: int) -> None:
        if not text:
            return
        self.texts[index].append(text)
        label = str(speaker or "UNKNOWN")
        if label != "UNKNOWN" and live_speaker_display_name(label) is None:
            self.speakers[index][label] += max(1, held_ms)

    def speaker(self, index: int) -> str:
        """The final speaker holding most of the words, or UNKNOWN for none."""
        held = self.speakers.get(index)
        return max(held, key=held.__getitem__) if held else "UNKNOWN"


def _held_ms(words: list[dict[str, Any]]) -> int:
    return round(
        sum(
            float(word["end"]) - float(word["start"])
            for word in words
            if has_word_timing(word)
        )
        * 1000
    )


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


def _middle_ms(word: dict[str, Any]) -> float:
    return (float(word["start"]) + float(word["end"])) * 500


def timed_word_owners(
    words: list[dict[str, Any]], owner: Callable[[dict[str, Any]], T]
) -> list[T]:
    """Each word's owner: a timed word's own, an untimed word's from its neighbour.

    An untimed word goes with the timed word before it, or the first timed
    word when none precedes it. ``words`` must hold at least one timed word.
    """
    current = next(owner(word) for word in words if has_word_timing(word))
    owners: list[T] = []
    for word in words:
        if has_word_timing(word):
            current = owner(word)
        owners.append(current)
    return owners


def _part_bounds(
    words: list[dict[str, Any]],
    found: list[int],
    line_bounds: dict[int, tuple[int, int]],
) -> tuple[float, float]:
    """A part's span stops at the edges of the protected lines beside it.

    A word straddling a line's edge keeps its own timing, but the part no
    longer overlaps the line. Otherwise a part before the line, finalised
    before the line is matched, would inherit the line's manual speaker.
    """
    start, end = float(words[0]["start"]), float(words[-1]["end"])
    first, last = _middle_ms(words[0]), _middle_ms(words[-1])
    for index in found:
        line_start, line_end = line_bounds[index]
        if line_start > last:
            end = min(end, line_start / 1000)
        elif line_end <= first:
            start = max(start, line_end / 1000)
    return start, end


def _consume_overlap(
    segment: dict[str, Any],
    found: list[int],
    line_bounds: dict[int, tuple[int, int]],
    reviews: _Reviews,
) -> list[dict[str, Any]]:
    """Review only the words over protected lines; return the rest as segments.

    A timed word belongs to the protected line holding its midpoint, and an
    untimed word goes with the timed word before it, or the first timed word
    when none precedes it. Runs of words outside every protected line stay
    ordinary final segments, split wherever a protected line lies between
    them, even one holding no word's midpoint, and bounded by their timed
    words and the protected lines beside them. Only a segment with no timed
    word is reviewed whole on the first line.
    """
    words = segment.get("words") or []
    if not any(has_word_timing(word) for word in words):
        start, end = _bounds(segment)
        line_start, line_end = line_bounds[found[0]]
        reviews.add(
            found[0],
            str(segment.get("text") or "").strip(),
            segment.get("speaker"),
            min(end, line_end) - max(start, line_start),
        )
        return []

    def owner(word: dict[str, Any]) -> tuple[int | None, int]:
        """The protected line holding the midpoint, and how many lines end before it."""
        middle = _middle_ms(word)
        line = next(
            (i for i in found if line_bounds[i][0] <= middle < line_bounds[i][1]),
            None,
        )
        return line, sum(line_bounds[i][1] <= middle for i in found)

    owners = timed_word_owners(words, owner)
    parts: list[dict[str, Any]] = []
    for (index, _gap), run in groupby(zip(words, owners), key=itemgetter(1)):
        kept = [word for word, _ in run]
        text = reconstruct_text_from_words(kept)
        if index is not None:
            reviews.add(index, text, segment.get("speaker"), _held_ms(kept))
            continue
        timed = [word for word in kept if has_word_timing(word)]
        start, end = _part_bounds(timed, found, line_bounds)
        parts.append(
            {**segment, "words": kept, "text": text, "start": start, "end": end}
        )
    return parts


def preserve_human_segments(
    lines: Sequence[TranscriptUtterance], segments: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Retain protected lines and expose the final wording over them only once.

    A final segment whose bounds match one protected line exactly, and the
    only one touching it, goes to the ordinary finaliser, which pins the
    accepted values; its wording is offered for review when it differs from
    the kept text. Otherwise a final segment that directly overlaps a
    protected line offers the words over it for review there, once, rather
    than duplicating them beside the kept text; its other words, and every
    other final segment, replace the live lines as usual. A line kept so
    takes the final speaker holding most of those words, or UNKNOWN when no
    speaker holds any, unless a person chose its speaker: a live label, or an
    earlier run's label, names nobody in this run.
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
    reviews = _Reviews()
    result: list[dict[str, Any]] = []
    for segment, found in zip(segments, hits):
        text = str(segment.get("text") or "").strip()
        if not found:
            result.append(dict(segment))
        elif found[0] in exact:
            result.append(_with_final_review(segment, lines[found[0]], text))
        else:
            result.extend(_consume_overlap(segment, found, line_bounds, reviews))
    for index in protected:
        if index in exact:
            continue
        kept = _retained_segment(lines[index])
        if not _speaker_chosen(lines[index]):
            kept["speaker"] = reviews.speaker(index)
            kept["recording_speaker_id"] = None
        kept["confidence_payload"].pop("final_review", None)
        if reviews.texts[index]:
            kept["confidence_payload"]["final_review"] = {
                "text": " ".join(reviews.texts[index]),
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
