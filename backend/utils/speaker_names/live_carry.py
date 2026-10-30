"""Where a live speaker goes at the final pass, and what a live merge joins.

Pure: the live labels' own speech, the user's merges of one live label into
another, and the final pass's lines in; a final label per live label out,
with the final speakers a merge folds together.

A live speaker is a live label, or the labels the user merged into one during
the capture (``live_groups``). Its final speaker is the one its name goes to:
the final speaker holding at least 60% of the group's own speech, else the top
holder, else the one nearest in time (``place_event``, as for a user's name).
A label's own speech is its live lines as the live diariser labelled them. A
line the user moved onto the label is the user's claim, not evidence of where
the label's voice is, so it does not pull the mapping.

A merge makes the group one final speaker without merging anyone the user did
not name. Each member label's own final speaker joins the group's (a fold)
only when it is clearly that member's voice and no one else's: it holds at
least 60% of the member's speech, it is no other live speaker's final speaker,
and at least 60% of the live-labelled speech it holds is the group's; and the
group's own final speaker holds the group at 60% or more. Otherwise only the
final lines the member's live lines cover more than half of move to the
group's final speaker.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from .anchors import Line, Span, turns_of
from .resolve import (
    OUTCOME_HELD,
    OUTCOME_UNRESOLVED,
    SHARE_FLOOR,
    NameEvent,
    Placement,
    place_event,
)
from .sources import AUTHORITY, KIND_NAME, SOURCE_USER


@dataclass(frozen=True)
class LiveCarry:
    # Live label (a member or its group's head) -> the final label its live
    # speaker carries to.
    targets: dict[str, str] = field(default_factory=dict)
    # Final label -> the final label a live merge folds it into.
    folds: dict[str, str] = field(default_factory=dict)
    # Index of a final line -> the final label it moves to, where a fold was
    # refused and the member's live lines cover most of that line.
    moves: dict[int, str] = field(default_factory=dict)
    # Each live speaker's own speech, keyed by the label its group shows as:
    # a name given to the group follows exactly this speech.
    spans: dict[str, tuple[Span, ...]] = field(default_factory=dict)


def live_groups(merges: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Each merged live label -> the label its group now shows as.

    ``merges`` are (label, into) in the order the user made them. Merging a
    label moves its whole group, earlier merges into it included, so a chain
    ends on the last target. A merge into the label's own group changes
    nothing.
    """
    group: dict[str, str] = {}

    def head(label: str) -> str:
        while label in group:
            label = group[label]
        return label

    for label, into in merges:
        source, target = head(label), head(into)
        if source != target:
            group[source] = target
    return {label: head(label) for label in group}


def _merged(spans: Iterable[Span]) -> tuple[Span, ...]:
    # A 1 ms gap joins touching and overlapping spans, so no speech counts twice.
    return tuple(
        (start, end)
        for _, start, end in turns_of(((0, s, e) for s, e in spans), gap_ms=1)
    )


def _overlap(spans: Sequence[Span], start: int, end: int) -> int:
    return sum(
        max(0, min(end, s_end) - max(start, s_start)) for s_start, s_end in spans
    )


def _place(spans: Sequence[Span], lines: Sequence[Line]) -> Placement:
    """Where a user's name for this speech would go."""
    first, last = spans[0][0], spans[-1][1]
    return place_event(
        NameEvent(
            event_id=0,
            kind=KIND_NAME,
            name="",
            global_speaker_id=None,
            source=SOURCE_USER,
            authority=AUTHORITY[SOURCE_USER],
            anchor_start_ms=first,
            anchor_end_ms=max(last, first + 1),
            anchor_spans=tuple(spans),
        ),
        lines,
    )


@dataclass(frozen=True)
class _Speech:
    """The live speakers' own speech against the final pass's lines."""

    own: Mapping[str, tuple[Span, ...]]
    groups: Mapping[str, str]
    lines: Sequence[Line]

    def head(self, label: str) -> str:
        return self.groups.get(label, label)

    def share_of_group(self, holder: str, head: str) -> float:
        """The share of the live-labelled speech in ``holder``'s lines that is the group's."""
        total = mine = 0
        for label, spans in self.own.items():
            speech = sum(
                _overlap(spans, start, end)
                for key, start, end in self.lines
                if key == holder
            )
            total += speech
            if self.head(label) == head:
                mine += speech
        return mine / total if total else 0.0


def _covered_lines(
    spans: Sequence[Span], final_lines: Sequence[Line | None], target: str
) -> list[int]:
    """The final lines, not already the target's, that ``spans`` cover more than half of."""
    return [
        index
        for index, line in enumerate(final_lines)
        if line is not None
        and line[0] != target
        and _overlap(spans, line[1], line[2]) * 2 > line[2] - line[1]
    ]


def carry_live_speakers(
    label_spans: Mapping[str, Sequence[Span]],
    groups: Mapping[str, str],
    final_lines: Sequence[Line | None],
) -> LiveCarry:
    """Map every live label heard to a final label, and fold merged groups.

    ``final_lines`` is aligned with the final segments; an entry is None where
    the segment has no speaker. A live label with no final speaker to go to
    (the final pass has no lines at all) is left out of ``targets``.
    """
    own = {label: _merged(spans) for label, spans in label_spans.items() if spans}
    members: dict[str, list[str]] = {}
    for label in own:
        members.setdefault(groups.get(label, label), []).append(label)
    group_spans = {
        head: _merged(span for label in labels for span in own[label])
        for head, labels in members.items()
    }
    speech = _Speech(own, groups, [line for line in final_lines if line is not None])
    if not speech.lines:
        return LiveCarry(spans=group_spans)
    placed = {head: _place(spans, speech.lines) for head, spans in group_spans.items()}
    # Every member and every group's head, which may have no speech of its own.
    targets = {
        label: str(placed[speech.head(label)].label)
        for label in [*own, *placed]
        if placed[speech.head(label)].label is not None
    }
    # Final label -> the live speakers whose speech it clearly or mostly holds.
    claimed: dict[str, set[str]] = {}
    for head, placement in placed.items():
        if placement.label is not None and placement.outcome != OUTCOME_UNRESOLVED:
            claimed.setdefault(str(placement.label), set()).add(head)

    folds: dict[str, str] = {}
    moves: dict[int, str] = {}
    for head, labels in members.items():
        if len(labels) > 1 and placed[head].label is not None:
            _join_group(
                speech, head, labels, placed[head], claimed, final_lines, folds, moves
            )
    return LiveCarry(targets=targets, folds=folds, moves=moves, spans=group_spans)


def _join_group(  # noqa: PLR0913 - the group, its evidence, and what it adds to
    speech: _Speech,
    head: str,
    labels: Sequence[str],
    group: Placement,
    claimed: Mapping[str, set[str]],
    final_lines: Sequence[Line | None],
    folds: dict[str, str],
    moves: dict[int, str],
) -> None:
    """Fold each member's final speaker into the group's, or move its lines."""
    target = str(group.label)
    for label in labels:
        placement = _place(speech.own[label], speech.lines)
        holder = None if placement.label is None else str(placement.label)
        if holder is None or holder == target or holder in folds:
            continue
        if (
            group.outcome == OUTCOME_HELD
            and placement.outcome == OUTCOME_HELD
            and claimed.get(holder, set()) <= {head}
            and speech.share_of_group(holder, head) >= SHARE_FLOOR
        ):
            folds[holder] = target
            continue
        for index in _covered_lines(speech.own[label], final_lines, target):
            moves.setdefault(index, target)


__all__ = ["LiveCarry", "carry_live_speakers", "live_groups"]
