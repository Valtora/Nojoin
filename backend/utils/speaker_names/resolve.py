"""Place standing name events on the final pass's speakers.

Pure: events and timed lines in, a name per label out. Each event's span goes
to the speaker holding at least ``SHARE_FLOOR`` of the attributed speech in it,
which is Valtora's "one final speaker clearly holds the span". At 0.60 the
runner-up holds at most 0.40 however many speakers share the span, which is
stricter than ``inherit_manual_speaker_for_range``, which refuses only an exact
tie. Below the floor the name is offered on the top holder as pending, never
applied as given. A span with under ``MIN_COVERED_MS`` of attributed speech
names nobody, except that a user's name is never dropped: it is offered as
pending on the speaker holding most of what little speech there is, or on the
speaker nearest in time, so the user sees it and decides.

A name given to a live speaker follows all of that label's speech
(``anchor_spans``), not only the turn it was given on.

Each speaker then takes the highest-authority name among the events it holds.
Within that authority the overlap is summed per name (or per person), and the
winner needs ``SHARE_FLOOR`` of the sum; two names that close are a conflict,
offered as pending with the stronger one shown.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .anchors import Line, Span
from .sources import KIND_NAME, SOURCE_USER

SHARE_FLOOR = 0.60
MIN_COVERED_MS = 1000

OUTCOME_HELD = "held"
OUTCOME_NEAR_TIE = "near_tie"
OUTCOME_UNRESOLVED = "unresolved"

REASON_HELD = "held"
REASON_NEAR_TIE = "near_tie"
REASON_CONFLICT = "conflict"
REASON_UNPLACED = "unplaced"


@dataclass(frozen=True)
class NameEvent:
    event_id: int
    kind: str
    name: str
    global_speaker_id: int | None
    source: str
    authority: int
    anchor_start_ms: int
    anchor_end_ms: int
    supersedes_event_id: int | None = None
    # The speech the name follows, when not just the anchor: a live label's
    # lines, as disjoint spans.
    anchor_spans: tuple[Span, ...] = ()

    @property
    def spans(self) -> tuple[Span, ...]:
        return self.anchor_spans or ((self.anchor_start_ms, self.anchor_end_ms),)


@dataclass(frozen=True)
class Placement:
    event: NameEvent
    outcome: str
    # The holder for "held", the top holder for "near_tie", where an
    # "unresolved" user's name is offered, None otherwise.
    label: str | None
    share: float
    covered_ms: int


@dataclass(frozen=True)
class ResolvedName:
    label: str
    name: str
    global_speaker_id: int | None
    source: str
    authority: int
    pending: bool
    reason: str
    event_ids: tuple[int, ...]


def standing_events(events: Iterable[NameEvent]) -> list[NameEvent]:
    """Name events that nothing supersedes.

    A dismissal supersedes the name it dismissed and names nobody itself.
    """
    event_list = list(events)
    superseded = {
        event.supersedes_event_id
        for event in event_list
        if event.supersedes_event_id is not None
    }
    return [
        event
        for event in event_list
        if event.event_id not in superseded and event.kind == KIND_NAME
    ]


def _overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> int:
    return max(0, min(end_a, end_b) - max(start_a, start_b))


def _distance(spans: Sequence[Span], start: int, end: int) -> int:
    return min(max(0, span[0] - end, start - span[1]) for span in spans)


def place_event(event: NameEvent, lines: Sequence[Line]) -> Placement:
    held: dict[str, int] = {}
    for label, start, end in lines:
        overlap = sum(_overlap(*span, start, end) for span in event.spans)
        if overlap:
            held[str(label)] = held.get(str(label), 0) + overlap
    covered = sum(held.values())
    if covered < MIN_COVERED_MS:
        offered = None
        if event.source == SOURCE_USER and held:
            offered = max(held, key=lambda label: held[label])
        elif event.source == SOURCE_USER and lines:
            offered = str(
                min(lines, key=lambda line: _distance(event.spans, *line[1:]))[0]
            )
        return Placement(event, OUTCOME_UNRESOLVED, offered, 0.0, covered)
    # Ties on overlap go to the label first in time order of the lines, so
    # the result does not depend on dict ordering.
    top = max(held, key=lambda label: held[label])
    share = held[top] / covered
    outcome = OUTCOME_HELD if share >= SHARE_FLOOR else OUTCOME_NEAR_TIE
    return Placement(event, outcome, top, share, covered)


def _name_key(event: NameEvent) -> tuple[str, str]:
    if event.global_speaker_id is not None:
        return ("person", str(event.global_speaker_id))
    return ("name", event.name.strip().casefold())


def _resolve_label(label: str, placements: Sequence[Placement]) -> ResolvedName:
    held = [p for p in placements if p.outcome == OUTCOME_HELD]
    pool = held or list(placements)
    authority = max(p.event.authority for p in pool)
    top = [p for p in pool if p.event.authority == authority]
    weights: dict[tuple[str, str], float] = {}
    for placement in top:
        weight = placement.share * placement.covered_ms
        key = _name_key(placement.event)
        weights[key] = weights.get(key, 0.0) + weight
    winner_key = max(weights, key=lambda key: weights[key])
    winners = [p for p in top if _name_key(p.event) == winner_key]
    # The newest act speaks for a name given more than once.
    shown = max(winners, key=lambda p: p.event.event_id).event
    if not held:
        unplaced = all(p.outcome == OUTCOME_UNRESOLVED for p in pool)
        reason = REASON_UNPLACED if unplaced else REASON_NEAR_TIE
    elif weights[winner_key] / sum(weights.values()) < SHARE_FLOOR:
        reason = REASON_CONFLICT
    else:
        reason = REASON_HELD
    return ResolvedName(
        label=label,
        name=shown.name,
        global_speaker_id=shown.global_speaker_id,
        source=shown.source,
        authority=authority,
        pending=reason != REASON_HELD,
        reason=reason,
        event_ids=tuple(sorted(p.event.event_id for p in winners)),
    )


def resolve_names(
    events: Iterable[NameEvent], lines: Sequence[Line]
) -> tuple[dict[str, ResolvedName], list[Placement]]:
    """A name per final label, and where each standing event landed."""
    placements = [place_event(event, lines) for event in standing_events(events)]
    by_label: dict[str, list[Placement]] = {}
    for placement in placements:
        if placement.label is not None:
            by_label.setdefault(placement.label, []).append(placement)
    resolved = {
        label: _resolve_label(label, label_placements)
        for label, label_placements in by_label.items()
    }
    return resolved, placements
