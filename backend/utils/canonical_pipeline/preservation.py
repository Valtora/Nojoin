"""Conservative final remapping of accepted human work on settled recordings."""

from copy import deepcopy
from heapq import heappop, heappush
from typing import Any, Sequence

from backend.models.pipeline import TranscriptUtterance


def line_is_protected(line: TranscriptUtterance) -> bool:
    """Human edits and explicit split boundaries outlive machine segmentation."""
    return bool(
        line.manual_text_locked
        or line.manual_speaker_locked
        or (line.confidence_payload or {}).get("human_protection")
    )


def _bounds(segment: dict[str, Any]) -> tuple[int, int]:
    return round(float(segment.get("start", 0)) * 1000), round(
        float(segment.get("end", 0)) * 1000
    )


def _overlaps(first: tuple[int, int], second: tuple[int, int]) -> bool:
    return max(first[0], second[0]) < min(first[1], second[1]) or first == second


class _Components:
    """Disjoint interval components with bounded-height, compressed roots."""

    def __init__(self, count: int) -> None:
        self.parents = list(range(count))
        self.sizes = [1] * count

    def root(self, index: int) -> int:
        while self.parents[index] != index:
            self.parents[index] = self.parents[self.parents[index]]
            index = self.parents[index]
        return index

    def join(self, first: int, second: int) -> None:
        first, second = self.root(first), self.root(second)
        if first == second:
            return
        if self.sizes[first] < self.sizes[second]:
            first, second = second, first
        self.parents[second] = first
        self.sizes[first] += self.sizes[second]


def _interval_components(
    old: list[tuple[int, int]], final: list[tuple[int, int]]
) -> list[tuple[list[int], list[int]]]:
    """Discover bipartite overlap components in one recording-clock sweep.

    Expired intervals leave the active sets once. Only opposite-side intervals
    that can overlap are compared, so work is sorting plus overlap edges,
    rather than rescanning the recording each time a component grows.
    Nonpositive spans can only connect through the exact-equality rule.
    """
    components = _Components(len(old) + len(final))
    active: list[dict[int, tuple[int, int]]] = [{}, {}]
    deadlines: list[list[tuple[int, int]]] = [[], []]
    degenerate: dict[tuple[int, int], tuple[list[int], list[int]]] = {}
    events: list[tuple[int, int, int, int]] = []
    for side, bounds in enumerate((old, final)):
        offset = len(old) if side else 0
        for index, (start, end) in enumerate(bounds):
            node = offset + index
            if end <= start:
                degenerate.setdefault((start, end), ([], []))[side].append(node)
            else:
                events.append((start, end, side, node))
    for start, end, side, node in sorted(events):
        for lane in (0, 1):
            while deadlines[lane] and deadlines[lane][0][0] <= start:
                _, expired = heappop(deadlines[lane])
                del active[lane][expired]
        for other, bounds in active[1 - side].items():
            if _overlaps((start, end), bounds):
                components.join(node, other)
        active[side][node] = (start, end)
        heappush(deadlines[side], (end, node))
    _join_degenerate_components(components, degenerate)
    return _group_components(components, len(old), len(final))


def _join_degenerate_components(
    components: _Components,
    spans: dict[tuple[int, int], tuple[list[int], list[int]]],
) -> None:
    for old, final in spans.values():
        if old and final:
            for node in [*old, *final]:
                components.join(old[0], node)


def _group_components(
    components: _Components, old_count: int, final_count: int
) -> list[tuple[list[int], list[int]]]:
    grouped: dict[int, tuple[list[int], list[int]]] = {}
    for node in range(old_count + final_count):
        side = int(node >= old_count)
        index = node - old_count if side else node
        grouped.setdefault(components.root(node), ([], []))[side].append(index)
    return list(grouped.values())


def _retained_segment(line: TranscriptUtterance) -> dict[str, Any]:
    payload = deepcopy(line.confidence_payload or {})
    if line_is_protected(line):
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


def preserve_human_segments(
    lines: Sequence[TranscriptUtterance], segments: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Retain ambiguous protected components and expose final wording only once.

    An exact one-to-one boundary match uses the ordinary finaliser, which pins
    the accepted values. Otherwise a final segment touching protected work is
    consumed as a review candidate. Existing lines inside that connected
    component remain together, avoiding a duplicated whole final paragraph or
    a guessed cut through an untimed human replacement.
    """
    protected = {index for index, line in enumerate(lines) if line_is_protected(line)}
    if not protected:
        return [dict(segment) for segment in segments]
    final_bounds = [_bounds(segment) for segment in segments]
    old_bounds = [(line.start_ms, line.end_ms) for line in lines]
    consumed: set[int] = set()
    additions: list[dict[str, Any]] = []
    for component, candidates in _interval_components(old_bounds, final_bounds):
        protected_component = protected.intersection(component)
        if not protected_component:
            continue
        if (
            len(component) == len(candidates) == 1
            and old_bounds[component[0]] == final_bounds[candidates[0]]
        ):
            continue
        consumed.update(candidates)
        kept = [_retained_segment(lines[i]) for i in component]
        review_text = " ".join(
            str(segments[i].get("text") or "") for i in candidates
        ).strip()
        # Only one line offers the component's final candidate, even for splits.
        for segment in kept:
            segment["confidence_payload"].pop("final_review", None)
        first_protected = min(protected_component)
        if review_text:
            kept[component.index(first_protected)]["confidence_payload"][
                "final_review"
            ] = {
                "text": review_text,
                "reason": "ambiguous_alignment",
            }
        additions.extend(kept)
    result = [dict(segment) for i, segment in enumerate(segments) if i not in consumed]
    result.extend(additions)
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
