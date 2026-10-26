"""The study's measures and pass rules, as PREREG.md fixes them.

Every measure works on the 100 ms grid from ``align`` with the platform's
fitted lag already applied. Speech time is reviewed speech; a bin with two
reviewed speakers counts towards the precondition but not towards any
measure that needs one speaker. A tile is a slot within a layout, numbered
by the detector, so the same place in a different layout is another tile.

Hints are made from the tiles and the pipeline's speakers alone. The
reviewed speakers only judge them.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from .align import BIN_MS, Bins, Utterance, bin_span, majority

MIN_SPEECH_BINS = 3_000 // BIN_MS  # a speaker, or a tile, needs 3 s
SHORT_REPLY_MS = 1_500
MERGE_SHARE = 0.6
SPLIT_SHARE = 0.2
REPLY_TILE_SHARE = 0.5

PASS, FAIL, INCONCLUSIVE, NOT_SCORED = "pass", "fail", "inconclusive", "not_scored"


@dataclass(frozen=True)
class Meeting:
    """One held-out meeting, ready to score."""

    recording: str
    platform: str
    bins: Bins
    utterances: Sequence[Utterance]


def _ratio(num: float, den: float) -> float | None:
    return num / den if den else None


def visible_speech(bins: Bins) -> tuple[int, int]:
    """Speech bins with tiles on screen, and all speech bins."""
    return int((bins.speech & bins.visible).sum()), int(bins.speech.sum())


def speaker_counts(bins: Bins) -> tuple[int, int, int]:
    """Reviewed speakers, pipeline speakers, and highlighted tiles, each
    counted once it holds 3 s. Tiles are counted per layout, and the layout
    with the most is the meeting's count."""
    single = bins.reviewed >= 0
    reviewed = np.bincount(bins.reviewed[single]) if single.any() else np.zeros(0)
    labelled = bins.pipeline >= 0
    pipeline = np.bincount(bins.pipeline[labelled]) if labelled.any() else np.zeros(0)
    shown = bins.visible & (bins.tile >= 0)
    per_layout: dict[int, int] = defaultdict(int)
    for tile, count in zip(
        *np.unique(bins.tile[shown], return_counts=True), strict=True
    ):
        if count >= MIN_SPEECH_BINS:
            per_layout[bins.layout_of.get(int(tile), -1)] += 1
    return (
        int((reviewed >= MIN_SPEECH_BINS).sum()),
        int((pipeline >= MIN_SPEECH_BINS).sum()),
        max(per_layout.values(), default=0),
    )


def matching(bins: Bins) -> tuple[int, int, int]:
    """Matched speech bins, those matched to the right person, and all
    one-speaker speech bins.

    Each tile stands for the reviewed speaker it is lit for most; a tile lit
    for less than 3 s of speech stands for no one.
    """
    single = bins.reviewed >= 0
    usable = single & bins.visible & (bins.tile >= 0)
    tiles = bins.tile[usable]
    person, _ = majority(tiles, bins.reviewed[usable])
    mapped = [t for t, n in _tile_counts(tiles).items() if n >= MIN_SPEECH_BINS]
    matched = np.isin(tiles, mapped)
    named = np.array([person.get(int(t), -1) for t in tiles], np.int64)
    right = matched & (named == bins.reviewed[usable])
    return int(matched.sum()), int(right.sum()), int(single.sum())


def _tile_counts(tiles: np.ndarray) -> dict[int, int]:
    values, counts = np.unique(tiles, return_counts=True)
    return {int(v): int(c) for v, c in zip(values, counts, strict=True)}


def _cluster_people(bins: Bins) -> tuple[dict[int, int], dict[int, np.ndarray]]:
    """Each pipeline speaker's reviewed person by majority, and how much of
    each person it holds."""
    both = (bins.pipeline >= 0) & (bins.reviewed >= 0)
    people: dict[int, np.ndarray] = {}
    size = max(len(bins.reviewed_index), 1)
    for cluster in np.unique(bins.pipeline[both]):
        held = bins.reviewed[both & (bins.pipeline == cluster)]
        people[int(cluster)] = np.bincount(held, minlength=size)
    best = {c: int(np.argmax(v)) for c, v in people.items()}
    return best, people


@dataclass(frozen=True)
class HintCounts:
    needed_merges: int
    merge_hints: int
    right_merges: int
    fixed_merges: int
    two_person: int
    split_hints: int
    right_splits: int


def _tiles_by_layout(bins: Bins, cluster: int) -> dict[int, dict[int, int]]:
    """A pipeline speaker's lit tiles, counted within each layout."""
    mine = (bins.pipeline == cluster) & bins.visible & (bins.tile >= 0)
    out: dict[int, dict[int, int]] = defaultdict(dict)
    for tile, count in _tile_counts(bins.tile[mine]).items():
        out[bins.layout_of.get(tile, -1)][tile] = count
    return out


def _dominant(counts: Mapping[int, int]) -> int | None:
    total = sum(counts.values())
    tile, count = max(counts.items(), key=lambda kv: kv[1])
    return tile if count >= MIN_SPEECH_BINS and count >= MERGE_SHARE * total else None


def _splits(counts: Mapping[int, int]) -> bool:
    total = sum(counts.values())
    strong = [
        c for c in counts.values() if c >= MIN_SPEECH_BINS and c >= SPLIT_SHARE * total
    ]
    return len(strong) >= 2


def hints(bins: Bins) -> HintCounts:
    """Merge and split hints from tiles, judged against the reviewed people."""
    best, people = _cluster_people(bins)
    counted = [c for c, v in people.items() if v.sum() >= MIN_SPEECH_BINS]
    layouts = {c: _tiles_by_layout(bins, c) for c in counted}
    dominant: dict[tuple[int, int], list[int]] = defaultdict(list)
    split_hinted = set()
    for cluster in counted:
        for layout, counts in layouts[cluster].items():
            tile = _dominant(counts)
            if tile is not None:
                dominant[(layout, tile)].append(cluster)
            if _splits(counts):
                split_hinted.add(cluster)
    pairs = {
        (a, b) for group in dominant.values() for a in group for b in group if a < b
    }
    right = [(a, b) for a, b in pairs if best[a] == best[b]]
    by_person: dict[int, list[int]] = defaultdict(list)
    for cluster in counted:
        by_person[best[cluster]].append(cluster)
    needed = sum(len(c) - 1 for c in by_person.values())
    fixed = needed - sum(_components(c, right) - 1 for c in by_person.values())
    two = {c for c in counted if _holds_two(people[c])}
    return HintCounts(
        needed_merges=needed,
        merge_hints=len(pairs),
        right_merges=len(right),
        fixed_merges=fixed,
        two_person=len(two),
        split_hints=len(split_hinted),
        right_splits=len(split_hinted & two),
    )


def _holds_two(held: np.ndarray) -> bool:
    ordered = np.sort(held)[::-1]
    return (
        len(ordered) > 1
        and ordered[1] >= MIN_SPEECH_BINS
        and ordered[1] >= SPLIT_SHARE * held.sum()
    )


def _components(nodes: Sequence[int], edges: Iterable[tuple[int, int]]) -> int:
    parent = {n: n for n in nodes}

    def find(n: int) -> int:
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    for a, b in edges:
        if a in parent and b in parent:
            parent[find(a)] = find(b)
    return len({find(n) for n in nodes})


@dataclass(frozen=True)
class ReplyCounts:
    errors: int
    fixed: int
    correct: int
    broken: int


def short_replies(meeting: Meeting) -> ReplyCounts:
    """What tile hints do to the pipeline's labels on replies of 1.5 s or less.

    A tile stands for the pipeline speaker it is lit for most during longer
    lines. A short reply whose bins show one tile for at least half of them
    is moved to that tile's speaker when it differs from its own.
    """
    bins = meeting.bins
    best, _ = _cluster_people(bins)
    long_bins = np.zeros(bins.tile.shape, bool)
    for u in meeting.utterances:
        if u.end_ms - u.start_ms > SHORT_REPLY_MS:
            long_bins[bin_span(u)] = True
    usable = long_bins & (bins.pipeline >= 0) & bins.visible & (bins.tile >= 0)
    speaker, _ = majority(bins.tile[usable], bins.pipeline[usable])
    counts = _tile_counts(bins.tile[usable])
    tile_speaker = {
        t: c for t, c in speaker.items() if counts.get(t, 0) >= MIN_SPEECH_BINS
    }
    errors = fixed = correct = broken = 0
    for u in meeting.utterances:
        if not _is_reply(u):
            continue
        own = bins.pipeline_index[u.pipeline]  # type: ignore[index]
        person = bins.reviewed_index[u.reviewed]  # type: ignore[index]
        moved = _moved_to(bins, u, tile_speaker, own)
        if best.get(own, -1) != person:
            errors += 1
            fixed += moved is not None and best.get(moved, -2) == person
        else:
            correct += 1
            broken += moved is not None and best.get(moved, -2) != person
    return ReplyCounts(errors, fixed, correct, broken)


def _is_reply(u: Utterance) -> bool:
    return (
        0 < u.end_ms - u.start_ms <= SHORT_REPLY_MS
        and u.reviewed is not None
        and u.pipeline is not None
        and u.overlap_rank == 0
    )


def _moved_to(
    bins: Bins, u: Utterance, tile_speaker: Mapping[int, int], own: int
) -> int | None:
    span = bin_span(u)
    tiles = bins.tile[span][bins.visible[span]]
    if not tiles.size:
        return None
    values, counts = np.unique(tiles[tiles >= 0], return_counts=True)
    if not values.size:
        return None
    top = int(np.argmax(counts))
    if counts[top] < REPLY_TILE_SHARE * (span.stop - span.start):
        return None
    target = tile_speaker.get(int(values[top]))
    return target if target is not None and target != own else None


def _rule(
    value: float | None, cases: int, minimum: int, passed: bool
) -> dict[str, object]:
    if cases < minimum or value is None:
        return {"value": value, "cases": cases, "result": INCONCLUSIVE}
    return {"value": value, "cases": cases, "result": PASS if passed else FAIL}


def score(meetings: Sequence[Meeting]) -> dict[str, object]:
    """Every pass rule over the held-out meetings."""
    seen = [visible_speech(m.bins) for m in meetings]
    shown, spoken = sum(s[0] for s in seen), sum(s[1] for s in seen)
    share = _ratio(shown, spoken)
    precondition = {
        "value": share,
        "cases": len(meetings),
        "result": PASS if share is not None and share >= 0.5 else FAIL,
    }
    if precondition["result"] != PASS:
        return {
            "precondition": precondition,
            "speaker_count": NOT_SCORED,
            "matching": NOT_SCORED,
            "merge_hints": NOT_SCORED,
            "split_hints": NOT_SCORED,
            "short_replies": NOT_SCORED,
        }
    return {
        "precondition": precondition,
        "speaker_count": _speaker_count(meetings),
        "matching": _matching(meetings),
        **_hint_rules(meetings),
        "short_replies": _reply_rule(meetings),
    }


def _speaker_count(meetings: Sequence[Meeting]) -> dict[str, object]:
    counts = [speaker_counts(m.bins) for m in meetings]
    bounded = sum(1 for true, _, tiles in counts if tiles >= true)
    lower = _ratio(bounded, len(counts))
    over = [(pipeline, tiles) for true, pipeline, tiles in counts if pipeline > true]
    fewer = sum(1 for pipeline, tiles in over if tiles < pipeline)
    return {
        "tiles_at_least_speakers": _rule(
            lower, len(counts), 1, lower is not None and lower >= 0.95
        ),
        "tiles_below_overcount": _rule(
            _ratio(fewer, len(over)),
            len(over),
            3,
            len(over) > 0 and fewer >= 0.5 * len(over),
        ),
    }


def _matching(meetings: Sequence[Meeting]) -> dict[str, object]:
    by_platform: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for m in meetings:
        matched, right, speech = matching(m.bins)
        totals = by_platform[m.platform]
        totals[0], totals[1], totals[2] = (
            totals[0] + matched,
            totals[1] + right,
            totals[2] + speech,
        )
    platforms = {}
    passing = 0
    for platform, (matched, right, speech) in sorted(by_platform.items()):
        accuracy, matched_share = _ratio(right, matched), _ratio(matched, speech)
        ok = (
            accuracy is not None
            and matched_share is not None
            and accuracy >= 0.95
            and matched_share >= 0.8
        )
        passing += ok
        platforms[platform] = {
            "accuracy": accuracy,
            "matched_share": matched_share,
            "passed": ok,
        }
    return {
        "platforms": platforms,
        "passing_platforms": passing,
        "result": PASS if passing >= 2 else FAIL,
    }


def _hint_rules(meetings: Sequence[Meeting]) -> dict[str, object]:
    totals = [hints(m.bins) for m in meetings]

    def total(name: str) -> int:
        return sum(getattr(t, name) for t in totals)

    merge_precision = _ratio(total("right_merges"), total("merge_hints"))
    merge_recall = _ratio(total("fixed_merges"), total("needed_merges"))
    split_precision = _ratio(total("right_splits"), total("split_hints"))
    # A right split hint is one of the speakers that hold two people found.
    split_recall = _ratio(total("right_splits"), total("two_person"))
    merge_ok = (merge_precision or 0) >= 0.9 and (merge_recall or 0) >= 0.5
    split_ok = (split_precision or 0) >= 0.8 and (split_recall or 0) >= 0.5
    return {
        "merge_hints": {
            "precision": merge_precision,
            "fixed_share": merge_recall,
            **_rule(merge_precision, total("needed_merges"), 5, merge_ok),
        },
        "split_hints": {
            "precision": split_precision,
            "found_share": split_recall,
            **_rule(split_precision, total("two_person"), 5, split_ok),
        },
    }


def _reply_rule(meetings: Sequence[Meeting]) -> dict[str, object]:
    counts = [short_replies(m) for m in meetings]
    errors = sum(c.errors for c in counts)
    fixed = sum(c.fixed for c in counts)
    correct = sum(c.correct for c in counts)
    broken = sum(c.broken for c in counts)
    fixed_share, broken_share = _ratio(fixed, errors), _ratio(broken, correct)
    ok = (fixed_share or 0) >= 0.3 and broken_share is not None and broken_share <= 0.02
    return {
        "fixed_share": fixed_share,
        "broken_share": broken_share,
        **_rule(fixed_share, errors, 50, ok),
    }
