"""Sentence-start correction on the live lane (#813; ``processing/sentence_start.py``).

Only words the native diariser decided are corrected: their evidence is fixed
once a word is decided, so a move does not come and go between passes. Words
labelled by clustering (a classic capture, or after a hand-back) are left alone.
The rule runs afresh on every pass from the diariser's own decisions; a word
whose speaker has locked keeps what it shows, and a tail holding one is not moved
unless that word already shows the next speaker.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from backend.models.pipeline import TranscriptUtterance
from backend.processing.sentence_start import (
    Activity,
    Words,
    activity_from_frames,
    corrected,
    ends_sentence,
    split_regions,
)
from backend.utils.live_speaker_labels import live_speaker_label

from .live_labels import LiveLabelDecision
from .live_settling_projection import (
    DecideSpeaker,
    mutable_row,
    row_words,
    speaker_locked_word_ids,
    stored_speakers,
)


@dataclass(frozen=True)
class NativeActivity:
    """Native speaker activity, and the last instant (ms) of natively decided
    words; None when the native route decides every word."""

    activity: Activity
    native_until_ms: int | None


def native_activity(
    posteriors: np.ndarray,
    first_frame: int,
    complete_frame: int,
    *,
    native: bool,
    switched: bool,
) -> NativeActivity | None:
    """Activity from retained native posteriors; None on a classic capture."""
    if not native and not switched:
        return None
    labels = [live_speaker_label(k + 1) for k in range(posteriors.shape[1])]
    return NativeActivity(
        activity_from_frames(posteriors, labels, first_frame, complete_frame),
        None if native else complete_frame * 10,
    )


@dataclass(frozen=True)
class LivePass:
    """What one settling pass gives the rule beyond its words and rows."""

    native: NativeActivity
    resume_boundaries_ms: Sequence[int]
    pause_s: float


@dataclass
class _Lane:
    """The words the rule reads: each region of the pass's words, after the
    immutable row just before it (read-only context, never moved)."""

    words: list[dict[str, Any]] = field(default_factory=list)
    raw: list[str] = field(default_factory=list)
    stored: list[str] = field(default_factory=list)
    locked: set[int] = field(default_factory=set)
    manual: set[int] = field(default_factory=set)
    regions: list[tuple[int, int]] = field(default_factory=list)
    source: list[int | None] = field(default_factory=list)

    def add(self, word: dict[str, Any], raw: str, stored: str, source: int | None):
        self.words.append(word)
        self.raw.append(raw)
        self.stored.append(stored)
        self.source.append(source)


def correct_sentence_starts(
    words: Sequence[dict[str, Any]],
    rows: Sequence[TranscriptUtterance],
    decisions: Sequence[LiveLabelDecision],
    decide: DecideSpeaker,
    live: LivePass,
) -> list[LiveLabelDecision]:
    """One pass's ``word_decisions`` result with each moved tail given its
    next speaker. ``words`` are the pass's mutable words; ``rows`` every row
    it read, immutable ones (manual or line-locked) included.

    A candidate never crosses an immutable row or a capture resume, but the
    immutable row just before a run of words, in the same capture interval,
    may end the sentence it follows. A word whose speaker has locked
    (``speaker_locked``), and every word of that row, is decided again for the rule's
    input, so a speaker the rule gave it is never read back as evidence.
    """
    mutable = [row for row in rows if mutable_row(row)]
    fixed = [row for row in rows if not mutable_row(row)]
    locked_ids = speaker_locked_word_ids(stored_speakers(mutable), mutable)
    cuts = sorted({*(row.start_ms for row in fixed), *live.resume_boundaries_ms})
    lane = _Lane()
    for lo, hi in split_regions(_region_keys(words, cuts)):
        start = len(lane.words)
        context = _context_row(fixed, words[lo:hi], live.resume_boundaries_ms)
        if context is not None:
            stored = context.speaker_label or "UNKNOWN"
            for word in row_words(context):
                if context.manual_text_locked or context.manual_speaker_locked:
                    lane.manual.add(len(lane.words))
                lane.locked.add(len(lane.words))
                lane.add(word, _decided(word, decide, stored), stored, None)
        for i in range(lo, hi):
            decision = decisions[i]
            raw = "UNKNOWN" if decision.diarisation_pending else decision.speaker
            if words[i].get("id") in locked_ids:
                lane.locked.add(len(lane.words))
                raw = _decided(words[i], decide, raw)
            lane.add(words[i], raw, decision.speaker, i)
        lane.regions.append((start, len(lane.words)))
    labels = corrected(_rule_words(lane, live))
    result = list(decisions)
    for label, given, source in zip(labels, lane.raw, lane.source, strict=True):
        if source is not None and label != given:
            result[source] = replace(decisions[source], speaker=label)
    return result


def _decided(word: dict[str, Any], decide: DecideSpeaker, fallback: str) -> str:
    """The diariser's own speaker for a word; ``fallback`` for an untimed one."""
    start, end = word.get("start_ms"), word.get("end_ms")
    if start is None or end is None:
        return fallback
    decision = decide(int(start), max(int(start) + 1, int(end)))
    return "UNKNOWN" if decision.diarisation_pending else decision.speaker


def _region_keys(words: Sequence[dict[str, Any]], cuts: Sequence[int]) -> list[int]:
    """Per word, how many cuts lie at or before it; untimed words follow the last."""
    keys: list[int] = []
    for word in words:
        start = word.get("start_ms")
        if start is not None and word.get("end_ms") is not None:
            keys.append(sum(cut <= int(start) for cut in cuts))
        else:
            keys.append(keys[-1] if keys else -1)
    return keys


def _context_row(
    fixed: Sequence[TranscriptUtterance],
    region: Sequence[dict[str, Any]],
    resume_boundaries_ms: Sequence[int],
) -> TranscriptUtterance | None:
    """The immutable row just before a region, unless a resume cut lies between."""
    first = next(
        (int(word["start_ms"]) for word in region if word.get("start_ms") is not None),
        None,
    )
    if first is None:
        return None
    before = [row for row in fixed if row.start_ms <= first]
    if not before:
        return None
    row = max(before, key=lambda item: (item.start_ms, item.sort_key, item.id or 0))
    if any(row.start_ms < cut <= first for cut in resume_boundaries_ms):
        return None
    return row


def _rule_words(lane: _Lane, live: LivePass) -> Words:
    until = live.native.native_until_ms
    timed = [
        word.get("start_ms") is not None and word.get("end_ms") is not None
        for word in lane.words
    ]
    return Words(
        starts=[
            int(w["start_ms"]) / 1000 if t else 0.0 for w, t in zip(lane.words, timed)
        ],
        ends=[int(w["end_ms"]) / 1000 if t else 0.0 for w, t in zip(lane.words, timed)],
        marks=[ends_sentence(str(word.get("word") or "")) for word in lane.words],
        labels=lane.raw,
        activity=live.native.activity,
        regions=lane.regions,
        pause_s=live.pause_s,
        stored=lane.stored,
        locked=frozenset(lane.locked),
        manual=frozenset(lane.manual),
        unsafe=frozenset(
            i
            for i, word in enumerate(lane.words)
            if not timed[i] or (until is not None and _decided_until(word) > until)
        ),
    )


def _decided_until(word: dict[str, Any]) -> int:
    """The end a word's speaker was decided over, at least 1 ms after its start,
    as ``word_decisions`` asks for it."""
    return max(int(word["start_ms"]) + 1, int(word["end_ms"]))
