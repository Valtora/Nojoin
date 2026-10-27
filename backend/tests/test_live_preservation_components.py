"""Final preservation discovers overlap components without repeated full scans."""

import random
from typing import Any

import pytest

from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceState
from backend.utils.canonical_pipeline import preservation


def _reference_components(
    old: list[tuple[int, int]], final: list[tuple[int, int]]
) -> set[tuple[tuple[int, ...], tuple[int, ...]]]:
    """Small exhaustive bipartite graph, including equal zero-length spans."""
    edges: dict[int, set[int]] = {
        index: set() for index in range(len(old) + len(final))
    }
    for i, first in enumerate(old):
        for j, second in enumerate(final, start=len(old)):
            if max(first[0], second[0]) < min(first[1], second[1]) or first == second:
                edges[i].add(j)
                edges[j].add(i)
    result = set()
    while edges:
        pending = {next(iter(edges))}
        seen: set[int] = set()
        while pending:
            node = pending.pop()
            if node not in seen:
                seen.add(node)
                pending.update(edges[node] - seen)
        result.add(
            (
                tuple(sorted(i for i in seen if i < len(old))),
                tuple(sorted(i - len(old) for i in seen if i >= len(old))),
            )
        )
        for node in seen:
            del edges[node]
    return result


def test_sweep_matches_bipartite_overlap_rules_for_degenerate_and_touching_spans() -> (
    None
):
    generator = random.Random(735)
    cases = [
        ([(0, 0), (0, 0), (0, 1), (1, 2)], [(0, 0), (1, 1), (1, 2)]),
        ([(3, 1), (1, 2)], [(3, 1), (2, 3)]),
        ([(0, 2), (0, 2)], []),
        ([], [(0, 2), (0, 2)]),
    ]
    for _ in range(100):
        old = [
            (generator.randrange(8), generator.randrange(8))
            for _ in range(generator.randrange(9))
        ]
        final = [
            (generator.randrange(8), generator.randrange(8))
            for _ in range(generator.randrange(9))
        ]
        cases.append((old, final))
    for old, final in cases:
        actual = {
            (tuple(first), tuple(second))
            for first, second in preservation._interval_components(old, final)
        }
        assert actual == _reference_components(old, final), (old, final)


def test_two_hour_drift_chain_keeps_one_review_with_linear_overlap_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    count = 2000
    lines = [
        TranscriptUtterance(
            public_id=f"old-{index}",
            recording_id=1,
            sort_key=f"{index:09}",
            start_ms=index * 2000,
            end_ms=(index + 1) * 2000,
            text=f"original {index}",
            speaker_label="UNKNOWN",
            source_kind="live",
            state=TranscriptUtteranceState.STABLE,
            revision=1,
            manual_text_locked=index == 0,
        )
        for index in range(count)
    ]
    final: list[dict[str, Any]] = [
        {"start": index * 2 + 1, "end": index * 2 + 3, "text": f"final {index}"}
        for index in range(count)
    ]
    checks = 0
    original_overlap = preservation._overlaps

    def measured_overlap(first: tuple[int, int], second: tuple[int, int]) -> bool:
        nonlocal checks
        checks += 1
        # Fail promptly with the former repeated-scans implementation, without
        # timing CI or waiting for the quadratic/cubic work to finish.
        assert checks <= 4 * count
        return original_overlap(first, second)

    monkeypatch.setattr(preservation, "_overlaps", measured_overlap)
    result = preservation.preserve_human_segments(lines, final)
    assert [segment["id"] for segment in result] == [line.public_id for line in lines]
    assert [segment["text"] for segment in result] == [line.text for line in lines]
    reviews = [
        segment["confidence_payload"]["final_review"]
        for segment in result
        if "final_review" in segment["confidence_payload"]
    ]
    assert reviews == [
        {
            "text": " ".join(segment["text"] for segment in final),
            "reason": "ambiguous_alignment",
        }
    ]
    assert len({segment["id"] for segment in result}) == count
    assert count <= checks <= 4 * count
