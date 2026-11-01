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


def ends_sentence(token: str) -> bool:
    """Whether a word ends with a Unicode ``Sentence_Terminal``, such as . ? ! 。 ؟ ।

    Trailing whitespace, closing brackets and quotes are skipped first, so ``done.")`` ends one.
    """
    stripped = token.rstrip(_CLOSERS)
    return bool(stripped) and stripped[-1] in SENTENCE_TERMINALS


# --- Unicode tables -------------------------------------------------------------
# Generated once from Unicode 18.0.0, with the regex package (2026.9.29), so they
# don't follow the Python build's own Unicode version: every code point matching
# ``\p{Sentence_Terminal}``, then the closers skipped before it, every code point
# matching ``[\s\p{Pe}\p{Pf}\p{Quotation_Mark}]`` (whitespace, closing brackets,
# final and other quotation marks). To regenerate, test each of chr(0)..chr(0x10FFFF)
# against those two patterns and list the matches in order.
SENTENCE_TERMINALS = frozenset(
    (
        "\u0021\u002e\u003f\u0589\u061d\u061e\u061f\u06d4\u0700\u0701\u0702\u07f9\u0837\u0839\u083d\u083e"
        "\u0964\u0965\u104a\u104b\u1362\u1367\u1368\u166e\u1735\u1736\u17d4\u17d5\u1803\u1809\u1944\u1945"
        "\u1aa8\u1aa9\u1aaa\u1aab\u1b4e\u1b4f\u1b5a\u1b5b\u1b5e\u1b5f\u1b7d\u1b7e\u1b7f\u1c3b\u1c3c\u1c7e"
        "\u1c7f\u2024\u203c\u203d\u2047\u2048\u2049\u2cf9\u2cfa\u2cfb\u2e2e\u2e3c\u2e53\u2e54\u2e60\u2e61"
        "\u3002\ua4ff\ua60e\ua60f\ua6f3\ua6f7\ua876\ua877\ua8ce\ua8cf\ua92f\ua9c8\ua9c9\uaa5d\uaa5e\uaa5f"
        "\uaaf0\uaaf1\uabeb\ufe12\ufe15\ufe16\ufe52\ufe56\ufe57\uff01\uff0e\uff1f\uff61\U00010a56\U00010a57"
        "\U00010f55\U00010f56\U00010f57\U00010f58\U00010f59\U00010f86\U00010f87\U00010f88\U00010f89\U00011047"
        "\U00011048\U000110be\U000110bf\U000110c0\U000110c1\U00011141\U00011142\U00011143\U000111c5\U000111c6"
        "\U000111cd\U000111de\U000111df\U00011238\U00011239\U0001123b\U0001123c\U000112a9\U000113d4\U000113d5"
        "\U0001144b\U0001144c\U000115c2\U000115c3\U000115c9\U000115ca\U000115cb\U000115cc\U000115cd\U000115ce"
        "\U000115cf\U000115d0\U000115d1\U000115d2\U000115d3\U000115d4\U000115d5\U000115d6\U000115d7\U00011641"
        "\U00011642\U0001173c\U0001173d\U0001173e\U00011944\U00011946\U00011a42\U00011a43\U00011a9b\U00011a9c"
        "\U00011c41\U00011c42\U00011ef7\U00011ef8\U00011f43\U00011f44\U00016a6e\U00016a6f\U00016af5\U00016b37"
        "\U00016b38\U00016b44\U00016d6e\U00016d6f\U00016e98\U0001bc9f\U0001da88"
    )
)
_CLOSERS = (
    "\u0009\u000a\u000b\u000c\u000d\u0020\u0022\u0027\u0029\u005d\u007d\u0085\u00a0\u00ab\u00bb\u0f3b"
    "\u0f3d\u1680\u169c\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2018\u2019"
    "\u201a\u201b\u201c\u201d\u201e\u201f\u2028\u2029\u202f\u2039\u203a\u2046\u205f\u207e\u208e\u2309"
    "\u230b\u232a\u2769\u276b\u276d\u276f\u2771\u2773\u2775\u27c6\u27e7\u27e9\u27eb\u27ed\u27ef\u2984"
    "\u2986\u2988\u298a\u298c\u298e\u2990\u2992\u2994\u2996\u2998\u29d9\u29db\u29fd\u2e03\u2e05\u2e0a"
    "\u2e0d\u2e1d\u2e21\u2e23\u2e25\u2e27\u2e29\u2e42\u2e56\u2e58\u2e5a\u2e5c\u2e63\u3000\u3009\u300b"
    "\u300c\u300d\u300e\u300f\u3011\u3015\u3017\u3019\u301b\u301d\u301e\u301f\ufd3e\ufe18\ufe36\ufe38"
    "\ufe3a\ufe3c\ufe3e\ufe40\ufe41\ufe42\ufe43\ufe44\ufe48\ufe5a\ufe5c\ufe5e\uff02\uff07\uff09\uff3d"
    "\uff5d\uff60\uff62\uff63"
)


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
    evidence covers the retained frames up to native completion. Frames become
    seconds by dividing by 100, so frame 35 is the same 0.35 as a word at 350 ms
    (``35 * 0.01`` is a hair later and would miss a word starting there).
    """
    turns = []
    for k in range(min(posteriors.shape[1], len(labels))):
        active = (posteriors[:, k] > 0.5).astype(np.int8)
        change = np.diff(np.r_[0, active, 0])
        for start, stop in zip(
            np.flatnonzero(change == 1), np.flatnonzero(change == -1)
        ):
            turns.append(
                (
                    int(start + first_frame) / 100,
                    int(stop + first_frame) / 100,
                    labels[k],
                )
            )
    covered = min(complete_frame, first_frame + len(posteriors))
    return activity_from_turns(turns, coverage=(first_frame / 100, covered / 100))


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
