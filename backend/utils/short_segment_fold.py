"""Fold consolidated segments shorter than the minimum into their neighbours.

``transcript_utils.consolidate_diarized_transcript`` calls this so that a
segment under its minimum duration keeps its text: it joins an adjacent
segment, and neighbours whose pieces tiled one turn are rejoined afterwards.
"""

import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# A short segment folds only into a neighbour within this gap, so one fold
# never bridges more than 1.0 s of silence; consecutive folds into the same
# segment can each add up to that. 1.0 s is the gap at which the word-level
# merge starts a new segment.
SHORT_SEGMENT_FOLD_MAX_GAP_S = 1.0
# Consolidation's tolerance for "no gap" between back-to-back segments.
CONTIGUOUS_GAP_TOLERANCE_S = 0.01

_FOLD_PREVIOUS, _FOLD_NONE, _FOLD_NEXT = 0, 1, 2

# A consolidated segment paired with the input segments it was built from, so
# a fold recomputes metadata exactly as consolidation's own merge does.
_Entry = tuple[dict, list[dict]]


def fold_short_segments(
    entries: list[_Entry],
    short_indices: set[int],
    max_duration_s: float,
    merge_metadata: Callable[[list[dict]], dict],
) -> list[dict]:
    """Fold each entry in ``short_indices`` into a neighbour so its text stays.

    A short segment goes to the adjacent segment by the same speaker, and
    otherwise to the nearer one (the earlier on a tie), among neighbours within
    SHORT_SEGMENT_FOLD_MAX_GAP_S that would not grow past ``max_duration_s``.
    With no such neighbour it stays as its own segment. The neighbour keeps its
    speaker and overlapping speakers; ids and edit flags merge as in
    consolidation. Consecutive short segments keep their order: once one stays
    or goes to the later neighbour, none after it goes to the earlier one. A
    short segment without text is dropped.

    After all folds, two neighbours that had only folded or dropped short
    segments between them merge when those pieces tiled, as consolidation
    would have merged them: every gap from the earlier neighbour through each
    short segment to the later one under CONTIGUOUS_GAP_TOLERANCE_S, with the
    same speaker and overlapping speakers, within ``max_duration_s``.

    ``merge_metadata`` is consolidation's own metadata merge, applied to the
    input segments behind every absorbed piece.
    """
    result: list[_Entry] = []
    tiles_with_previous: list[bool] = []
    previous_at: Optional[int] = None
    run: list[_Entry] = []
    for index, entry in enumerate([*entries, None]):
        if entry is not None and index in short_indices:
            run.append(entry)
            continue
        previous_end = None if previous_at is None else result[previous_at][0]["end"]
        forward: list[_Entry] = []
        choice = _FOLD_PREVIOUS
        kept_own = False
        for short in run:
            if not short[0]["text"]:
                continue
            previous = None if previous_at is None else result[previous_at][0]
            following = None if entry is None else entry[0]
            choice = max(
                choice,
                _short_segment_fold(short[0], previous, following, max_duration_s),
            )
            if choice == _FOLD_PREVIOUS and previous_at is not None:
                result[previous_at] = _absorb_entries(
                    result[previous_at], [], [short], merge_metadata
                )
            elif choice == _FOLD_NEXT:
                forward.append(short)
            else:
                result.append(short)
                tiles_with_previous.append(False)
                kept_own = True
        if entry is not None:
            tiles = (
                bool(run)
                and not kept_own
                and previous_end is not None
                and _pieces_tile(
                    previous_end, [short[0] for short in run], entry[0]["start"]
                )
            )
            result.append(_absorb_entries(entry, forward, [], merge_metadata))
            tiles_with_previous.append(tiles)
            previous_at = len(result) - 1
        run = []
    if not result and entries:
        return [entries[-1][0]]
    return _rejoin(result, tiles_with_previous, max_duration_s, merge_metadata)


def _rejoin(
    result: list[_Entry],
    tiles_with_previous: list[bool],
    max_duration_s: float,
    merge_metadata: Callable[[list[dict]], dict],
) -> list[dict]:
    """Merge each entry flagged as tiling into the one before it, where allowed."""
    joined: list[_Entry] = []
    for entry, tiles in zip(result, tiles_with_previous):
        if (
            tiles
            and joined
            and _same_turn_within(joined[-1][0], entry[0], max_duration_s)
        ):
            joined[-1] = _absorb_entries(joined[-1], [], [entry], merge_metadata)
        else:
            joined.append(entry)
    return [segment for segment, _ in joined]


def _pieces_tile(
    previous_end: float, shorts: list[dict], following_start: float
) -> bool:
    """Whether the short segments fill the gap between two neighbours, end to end."""
    end = previous_end
    for short in shorts:
        if abs(short["start"] - end) >= CONTIGUOUS_GAP_TOLERANCE_S:
            return False
        end = short["end"]
    return abs(following_start - end) < CONTIGUOUS_GAP_TOLERANCE_S


def _short_segment_fold(
    short: dict,
    previous: Optional[dict],
    following: Optional[dict],
    max_duration_s: float,
) -> int:
    candidates: list[tuple[int, dict, float]] = []
    for kind, neighbour in ((_FOLD_PREVIOUS, previous), (_FOLD_NEXT, following)):
        if neighbour is None:
            continue
        if kind == _FOLD_PREVIOUS:
            gap = short["start"] - neighbour["end"]
        else:
            gap = neighbour["start"] - short["end"]
        span = max(neighbour["end"], short["end"]) - min(
            neighbour["start"], short["start"]
        )
        if gap <= SHORT_SEGMENT_FOLD_MAX_GAP_S and span <= max_duration_s:
            candidates.append((kind, neighbour, max(gap, 0.0)))
    if not candidates:
        return _FOLD_NONE
    same_speaker = [c for c in candidates if c[1]["speaker"] == short["speaker"]]
    # min() keeps the first of equal gaps, so the earlier neighbour wins a tie.
    return min(same_speaker or candidates, key=lambda candidate: candidate[2])[0]


def _same_turn_within(previous: dict, following: dict, max_duration_s: float) -> bool:
    """Consolidation's same-speaker merge conditions, apart from the gap."""
    span = max(previous["end"], following["end"]) - min(
        previous["start"], following["start"]
    )
    return (
        following["speaker"] == previous["speaker"]
        and set(following["overlapping_speakers"])
        == set(previous["overlapping_speakers"])
        and span <= max_duration_s
    )


def _absorb_entries(
    target: _Entry,
    before: list[_Entry],
    after: list[_Entry],
    merge_metadata: Callable[[list[dict]], dict],
) -> _Entry:
    if not before and not after:
        return target
    parts = [segment for segment, _ in (*before, target, *after)]
    sources = [source for _, group in (*before, target, *after) for source in group]
    logger.debug(
        "Folding %d segment(s) into [%.2fs - %.2fs] %s",
        len(parts) - 1,
        target[0]["start"],
        target[0]["end"],
        target[0]["speaker"],
    )
    absorbed = {
        "start": min(part["start"] for part in parts),
        "end": max(part["end"] for part in parts),
        "speaker": target[0]["speaker"],
        "overlapping_speakers": target[0]["overlapping_speakers"],
        "text": " ".join(part["text"] for part in parts if part["text"]),
        "words": [word for part in parts for word in part.get("words") or []],
    }
    absorbed.update(merge_metadata(sources))
    return absorbed, sources
