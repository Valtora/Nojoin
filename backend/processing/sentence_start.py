"""Give a reply's first words to the speaker who says them (#813).

At a speaker change the diariser often hears the next speaker a moment late, so
the words that open their reply keep the previous speaker's label. Those words
sit after the last sentence end: "...that's done. So what" (L) | "do we do" (T).
Here a **candidate** is that tail: at a change from L to T, the words after the
last sentence terminal, when there are one to ``MAX_TAIL_WORDS`` of them and all
carry L. Each **rule** below may veto a candidate; the combiner moves every word
of a candidate no rule vetoes to T, all or nothing.

Each rule is one small function with the ``Rule`` signature, and its settings
are the constants below, so a rule can be dropped from ``RULES`` or retuned
without touching the others. Rules read only the input labels, never another
rule's verdict or a move already made, and candidates never overlap.

The final pass runs this inside each word-level merge, after flip smoothing
(``correct_word_assignments``); the live lane runs it on the words the native
diariser decided (``canonical_pipeline/live_sentence_start.py``).
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import regex

from backend.utils.word_times import WORD_TIME_TOLERANCE_S, exceeds, falls_short

# --- Settings, one place for every rule ---------------------------------------
# Longest tail moved: how far a late speaker change can be corrected.
MAX_TAIL_WORDS = 8
# keep_turn: the least of L's turn that must stay, in seconds. Consolidation
# folds a shorter line away (``consolidate_diarized_transcript`` min_duration_s).
MIN_REMAINDER_S = 0.1
# certain: a tail word needs more than this much of T's activity, in seconds.
NEXT_SPEAKER_MIN_ACTIVITY_S = 0.0
# Config key of the spans (ms) of lines a person edited, for the final pass.
EDITED_LINE_SPANS = "edited_line_spans_ms"

UNKNOWN = "UNKNOWN"
# A word a person assigned, whose chosen speaker the final pass applies later.
_MANUAL_OWNER = "\x00manual"
# Trailing whitespace, closing brackets and closing quotes, then a terminal.
_CLOSERS = regex.compile(r"[\s\p{Pe}\p{Pf}\p{Quotation_Mark}]+$")
_TERMINAL = regex.compile(r"\p{Sentence_Terminal}$")


def ends_sentence(token: str) -> bool:
    """Whether a word ends with a Unicode ``Sentence_Terminal``, such as . ? ! 。 ؟ ।"""
    stripped = _CLOSERS.sub("", token)
    return bool(stripped) and _TERMINAL.search(stripped) is not None


# --- Evidence -------------------------------------------------------------------
@dataclass(frozen=True)
class Activity:
    """Where the diariser says each label speaks, as merged ``[start, end)`` runs.

    ``coverage`` bounds the evidence that has arrived (None: everywhere). Outside
    it nothing is known, which is not silence.
    """

    runs: Mapping[str, tuple[list[float], list[float]]]
    coverage: tuple[float, float] | None = None

    def overlap(self, label: str, start: float, end: float) -> float:
        starts, ends = self.runs.get(label, ([], []))
        total = 0.0
        for k in range(bisect_right(ends, start), len(starts)):
            if starts[k] >= end:
                break
            total += max(0.0, min(ends[k], end) - max(starts[k], start))
        return total

    def at(self, label: str, instant: float) -> bool:
        """Whether label speaks at an instant; a run's end counts only when no
        run of any label holds it, as ``_speaker_at_instant`` reads turns."""
        starts, ends = self.runs.get(label, ([], []))
        k = bisect_right(starts, instant) - 1
        if k < 0:
            return False
        if instant < ends[k]:
            return True
        return ends[k] == instant and not any(
            self._holds(other, instant) for other in self.runs
        )

    def _holds(self, label: str, instant: float) -> bool:
        starts, ends = self.runs[label]
        k = bisect_right(starts, instant) - 1
        return k >= 0 and instant < ends[k]

    def available(self, start: float, end: float) -> bool:
        return self.coverage is None or (
            self.coverage[0] <= start and end <= self.coverage[1]
        )


def activity_from_turns(
    turns: Iterable[tuple[float, float, str]],
    coverage: tuple[float, float] | None = None,
) -> Activity:
    """Activity from ``(start, end, label)`` turns, overlapping ones merged."""
    spans: dict[str, list[tuple[float, float]]] = {}
    for start, end, label in turns:
        spans.setdefault(label, []).append((float(start), float(end)))
    runs = {}
    for label, items in spans.items():
        starts: list[float] = []
        ends: list[float] = []
        for start, end in sorted(items):
            if starts and start <= ends[-1]:
                ends[-1] = max(ends[-1], end)
            else:
                starts.append(start)
                ends.append(end)
        runs[label] = (starts, ends)
    return Activity(runs, coverage)


def activity_from_frames(
    posteriors: np.ndarray,
    labels: Sequence[str],
    first_frame: int,
    complete_frame: int,
) -> Activity:
    """Native activity: runs of 10 ms frames above 0.5 per speaker channel.

    The final pass reads native posteriors the same way
    (``diarization_union.native_annotation``). Row 0 is frame ``first_frame``;
    evidence covers the retained frames up to native completion.
    """
    turns = []
    for k in range(min(posteriors.shape[1], len(labels))):
        active = (posteriors[:, k] > 0.5).astype(np.int8)
        change = np.diff(np.r_[0, active, 0])
        for start, stop in zip(
            np.flatnonzero(change == 1), np.flatnonzero(change == -1)
        ):
            turns.append(
                ((start + first_frame) * 0.01, (stop + first_frame) * 0.01, labels[k])
            )
    covered = min(complete_frame, first_frame + len(posteriors))
    return activity_from_turns(turns, coverage=(first_frame * 0.01, covered * 0.01))


# --- Words and candidates --------------------------------------------------------
@dataclass(frozen=True)
class Words:
    """One merge's words (or one live pass's) and everything the rules read.

    ``labels`` are the diariser's own decisions, never ones corrected earlier.
    ``stored`` (live) is what each word shows now; a ``locked`` word can no longer
    change and counts as its stored speaker. ``manual`` words are in lines a
    person edited; ``unsafe`` words (untimed, or decided by another route) are
    never part of a candidate. ``regions`` are half-open index ranges a candidate
    never crosses (merge calls, capture resumes, immutable live rows).
    """

    starts: Sequence[float]
    ends: Sequence[float]
    marks: Sequence[bool]
    labels: Sequence[str]
    activity: Activity
    regions: Sequence[tuple[int, int]]
    pause_s: float
    stored: Sequence[str] | None = None
    locked: frozenset[int] = frozenset()
    manual: frozenset[int] = frozenset()
    unsafe: frozenset[int] = frozenset()

    def owner(self, i: int) -> str:
        """Who the transcript shows for a word: a settled or assigned one keeps
        its stored speaker; a final-pass manual word is nobody's yet."""
        if i in self.manual:
            return self.stored[i] if self.stored is not None else _MANUAL_OWNER
        if self.stored is not None and i in self.locked:
            return self.stored[i]
        return self.labels[i]

    def gap(self, i: int, j: int) -> float:
        return self.starts[j] - self.ends[i]

    def region_start(self, i: int) -> int:
        return next((lo for lo, hi in self.regions if lo <= i < hi), 0)


@dataclass(frozen=True)
class Candidate:
    """Words ``first..last`` (L's) after the terminal ending ``p``; ``nxt`` is T's."""

    p: int
    first: int
    last: int
    nxt: int
    L: str
    T: str

    @property
    def tail(self) -> range:
        return range(self.first, self.last + 1)


def candidates(words: Words) -> list[Candidate]:
    found = []
    for lo, hi in words.regions:
        p = -1
        for b in range(lo, hi - 1):
            L, T = words.labels[b], words.labels[b + 1]
            if (
                L != UNKNOWN
                and T not in (L, UNKNOWN)
                and p >= 0
                and 1 <= b - p <= MAX_TAIL_WORDS
                and all(words.labels[k] == L for k in range(p + 1, b + 1))
                and not any(k in words.unsafe for k in range(p, b + 2))
            ):
                found.append(Candidate(p, p + 1, b, b + 1, L, T))
            if words.marks[b]:
                p = b
    return found


# --- Rules: each returns True to veto ------------------------------------------
Rule = Callable[[Candidate, Words], bool]


def keep_turn(c: Candidate, words: Words) -> bool:
    """Never erase L's turn: veto when the tail is L's whole run, holds a pause
    longer than the line-break pause, or leaves under MIN_REMAINDER_S of L."""
    if words.labels[c.p] != c.L or words.owner(c.p) != c.L:
        return True
    if any(exceeds(words.gap(k, k + 1), words.pause_s) for k in range(c.p, c.last)):
        return True
    k, floor = c.p, words.region_start(c.first)
    while (
        k - 1 >= floor
        and k - 1 not in words.unsafe
        and words.owner(k - 1) == c.L
        and not exceeds(words.gap(k - 1, k), words.pause_s)
    ):
        k -= 1
    return falls_short(words.ends[c.p] - words.starts[k], MIN_REMAINDER_S)


def complete_sentence(c: Candidate, words: Words) -> bool:
    """A tail that ends a sentence right at the change is a whole utterance."""
    return words.marks[c.last]


def settled(c: Candidate, words: Words) -> bool:
    """Never change a settled or person-assigned word, nor move into an assigned
    one. A locked word already showing T (moved on an earlier pass) lets the
    rest of its tail follow."""
    stored = words.stored if words.stored is not None else words.labels
    return c.nxt in words.manual or any(
        (i in words.locked and stored[i] != c.T) or i in words.manual for i in c.tail
    )


def certain(c: Candidate, words: Words) -> bool:
    """Only move words in flux: veto when a tail word holds none of T's
    activity, or its evidence has not arrived. A zero-length word reads T at
    its instant."""
    for i in c.tail:
        start, end = words.starts[i], max(words.ends[i], words.starts[i])
        if not words.activity.available(start, end):
            return True
        if end > start:
            heard = words.activity.overlap(c.T, start, end) > (
                NEXT_SPEAKER_MIN_ACTIVITY_S + WORD_TIME_TOLERANCE_S
            )
        else:
            heard = words.activity.at(c.T, start)
        if not heard:
            return True
    return False


RULES: tuple[Rule, ...] = (keep_turn, complete_sentence, settled, certain)


def corrected(words: Words, rules: Sequence[Rule] = RULES) -> list[str]:
    """``words.labels`` with every candidate no rule vetoes moved to its T."""
    labels = list(words.labels)
    for c in candidates(words):
        if not any(rule(c, words) for rule in rules):
            for i in c.tail:
                if i not in words.locked:
                    labels[i] = c.T
    return labels


# --- Final pass ------------------------------------------------------------------
def correct_word_assignments(
    assignments: list[dict[str, Any]],
    groups: Sequence[tuple[dict[str, Any], list[dict[str, Any]]]],
    speaker_turns: Any,
    config: Mapping[str, Any] | None,
    pause_s: float,
) -> None:
    """Correct one word-level merge's speakers in place (after flip smoothing).

    ``groups`` are the merge's timed anchors with the untimed words attached to
    each; ``speaker_turns`` is its diarisation annotation; ``config`` supplies
    the capture resume cuts and the edited-line spans, and ``pause_s`` is the
    owner's resolved line-break pause.
    """
    from backend.utils.capture_resume import RESUME_BOUNDARIES

    anchors = [item["word"] for item in assignments]
    starts = [float(word["start"]) for word in anchors]
    ends = [float(word["end"]) for word in anchors]
    cuts = sorted(point / 1000 for point in (config or {}).get(RESUME_BOUNDARIES, []))
    spans = (config or {}).get(EDITED_LINE_SPANS, [])
    words = Words(
        starts=starts,
        ends=ends,
        marks=[ends_sentence(str(word.get("word") or "")) for word in anchors],
        labels=[item["speaker"] for item in assignments],
        activity=activity_from_turns(
            (turn.start, turn.end, label)
            for turn, _, label in speaker_turns.itertracks(yield_label=True)
        ),
        regions=split_regions(
            [sum(not falls_short(s, cut) for cut in cuts) for s in starts]
        ),
        pause_s=pause_s,
        manual=frozenset(
            i
            for i, (start, end) in enumerate(zip(starts, ends))
            if any(a <= (start + end) * 500 < b for a, b in spans)
        ),
        unsafe=frozenset(i for i, (_, group) in enumerate(groups) if len(group) > 1),
    )
    for item, label in zip(assignments, corrected(words)):
        item["speaker"] = label


def split_regions(keys: Sequence[object]) -> list[tuple[int, int]]:
    """Half-open ranges of consecutive words that share a key."""
    regions, start = [], 0
    for i in range(1, len(keys)):
        if keys[i] != keys[i - 1]:
            regions.append((start, i))
            start = i
    if keys:
        regions.append((start, len(keys)))
    return regions
