"""Live speakers the user merged during a capture, at the final pass.

Pure: the live labels' own speech, the user's merges of one live label into
another, the final pass's lines and where each live speaker was placed in;
the final speakers a merge folds together out.

A live speaker is a live label, or the labels the user merged into one
(``live_groups``). It is placed as a user's name on its speech is placed
(``users_act_on``): on the final speaker holding at least 60% of it, else the
top holder, else the one nearest in time. Its speech is its labels' live lines
as the live diariser labelled them; a line the user moved is the user's claim,
not evidence of where the voice is, so it does not count.

A merge makes the group one final speaker without merging anyone the user did
not name. Each member label's own final speaker joins the group's (a fold)
only when it is clearly that member's voice and no one else's: it holds at
least 60% of the member's speech, no other live speaker is placed on it, and
at least 60% of the live-labelled speech it holds is the group's; and the group
itself is held at 60% or more. Otherwise the member's own lines go to the
group's final speaker line by line, as lines moved by hand do.
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


def merged_spans(spans: Iterable[Span]) -> tuple[Span, ...]:
    """Disjoint spans; a 1 ms gap joins touching ones, so no speech counts twice."""
    return tuple(
        (start, end)
        for _, start, end in turns_of(((0, s, e) for s, e in spans), gap_ms=1)
    )


def group_spans(
    own: Mapping[str, Sequence[Span]], groups: Mapping[str, str]
) -> dict[str, tuple[Span, ...]]:
    """Each live speaker's speech, keyed by the label its group shows as."""
    members: dict[str, list[Span]] = {}
    for label, spans in own.items():
        members.setdefault(groups.get(label, label), []).extend(spans)
    return {head: merged_spans(spans) for head, spans in members.items() if spans}


def users_act_on(spans: tuple[Span, ...]) -> NameEvent:
    """A user's act on this speech, placed as a user's name on it would be."""
    return NameEvent(
        event_id=0,
        kind=KIND_NAME,
        name="",
        global_speaker_id=None,
        source=SOURCE_USER,
        authority=AUTHORITY[SOURCE_USER],
        anchor_start_ms=spans[0][0],
        anchor_end_ms=max(spans[-1][1], spans[0][0] + 1),
        anchor_spans=spans,
    )


def _overlap(spans: Sequence[Span], start: int, end: int) -> int:
    return sum(
        max(0, min(end, s_end) - max(start, s_start)) for s_start, s_end in spans
    )


@dataclass(frozen=True)
class MergedGroups:
    # Final label -> the final label a live merge folds it into.
    folds: dict[str, str] = field(default_factory=dict)
    # Member live label -> the final label its own lines go to, line by line,
    # where its final speaker could not be folded.
    by_line: dict[str, str] = field(default_factory=dict)


def fold_merged_groups(
    own: Mapping[str, Sequence[Span]],
    groups: Mapping[str, str],
    lines: Sequence[Line],
    placed: Mapping[str, Placement],
) -> MergedGroups:
    """Decide how each merged group becomes one final speaker.

    ``own`` is every live label's own speech, ``placed`` each live speaker's
    placement keyed by the label its group shows as.
    """
    result = MergedGroups()
    claimed: dict[str, set[str]] = {}
    for head, placement in placed.items():
        if placement.label is not None and placement.outcome != OUTCOME_UNRESOLVED:
            claimed.setdefault(str(placement.label), set()).add(head)
    members: dict[str, list[str]] = {}
    for label in own:
        members.setdefault(groups.get(label, label), []).append(label)
    for head, labels in members.items():
        group = placed.get(head)
        if len(labels) < 2 or group is None or group.label is None:
            continue
        target = str(group.label)
        for label in labels:
            if not own[label]:
                continue
            placement = place_event(users_act_on(tuple(own[label])), lines)
            holder = None if placement.label is None else str(placement.label)
            if holder is None or holder == target or holder in result.folds:
                continue
            if (
                group.outcome == OUTCOME_HELD
                and placement.outcome == OUTCOME_HELD
                and claimed.get(holder, set()) <= {head}
                and _group_share(holder, head, own, groups, lines) >= SHARE_FLOOR
            ):
                result.folds[holder] = target
            else:
                result.by_line[label] = target
    return result


def _group_share(
    holder: str,
    head: str,
    own: Mapping[str, Sequence[Span]],
    groups: Mapping[str, str],
    lines: Sequence[Line],
) -> float:
    """The share of the live-labelled speech in ``holder``'s lines that is the group's."""
    total = mine = 0
    for label, spans in own.items():
        speech = sum(
            _overlap(spans, start, end) for key, start, end in lines if key == holder
        )
        total += speech
        if groups.get(label, label) == head:
            mine += speech
    return mine / total if total else 0.0


__all__ = [
    "MergedGroups",
    "fold_merged_groups",
    "group_spans",
    "live_groups",
    "merged_spans",
    "users_act_on",
]
