"""Exercise interval identity mapping, overlap, subtraction and posterior boundaries."""

from __future__ import annotations

import numpy as np
import pytest
from pyannote.core import Annotation, Segment

from backend.processing.diarization_union import native_annotation, union_diarization
from backend.processing.nemotron_types import NemotronStateError


def annotation(
    rows: list[tuple[float, float, str]], *, uri: str = "meeting"
) -> Annotation:
    value = Annotation(uri=uri)
    for track, (start, end, label) in enumerate(rows):
        value[Segment(start, end), track] = label
    return value


def rows(value: Annotation) -> list[tuple[float, float, str]]:
    return [
        (s.start, s.end, label) for s, _, label in value.itertracks(yield_label=True)
    ]


def test_native_adapter_retains_fp32_boundary_overlap_and_final_frame() -> None:
    probabilities = np.zeros((3, 8), np.float32)
    probabilities[:, 0] = [0.5, 0.5001, 0.7]
    probabilities[:, 1] = [0.6, 0.6, 0.0]
    probabilities[:, 2] = probabilities[:, 1]
    value = native_annotation(probabilities, uri="source")
    assert rows(value) == [(0.0, 0.02, "L1"), (0.0, 0.02, "L2"), (0.01, 0.03, "L0")]
    assert value.uri == "source"


@pytest.mark.parametrize(
    "probabilities",
    [
        np.zeros((3, 7), np.float32),
        np.zeros((1, 3, 8), np.float32),
        np.zeros((3, 8), np.float64),
        np.full((3, 8), np.nan, np.float32),
        np.full((3, 8), np.inf, np.float32),
        np.full((3, 8), 1.01, np.float32),
        np.full((3, 8), -0.01, np.float32),
    ],
)
def test_native_adapter_rejects_malformed_probabilities(
    probabilities: np.ndarray,
) -> None:
    with pytest.raises(NemotronStateError):
        native_annotation(probabilities)


def test_empty_native_uses_fresh_labels_and_normalises() -> None:
    community = annotation([(0, 2, "a"), (1, 3, "a"), (4, 5, "N:a")])
    before = rows(community)
    result = union_diarization(
        community, native_annotation(np.empty((0, 8), np.float32))
    )
    assert rows(result) == [(0, 3, "C:a"), (4, 5, "C:N:a")]
    assert rows(community) == before
    assert result is not community
    assert result.uri == "meeting"


def test_union_subtracts_all_native_activity_preserves_overlap_and_tiny_gap() -> None:
    community = annotation([(0, 6, "a")])
    native = annotation([(1, 3, "x"), (2, 4, "y"), (4.01, 5, "x")])
    before = rows(native)
    result = union_diarization(community, native)
    assert rows(result) == [
        (0, 3, "N:x"),
        (2, 4, "N:y"),
        (4, 6, "N:x"),
    ]
    # The mapped fragment fills the 10 ms gap and joins native x intervals;
    # native y overlap remains, but community adds no x speech within [3,4).
    assert rows(native) == before


def test_cluster_mapping_uses_total_overlap_sorted_tie_and_fresh_zero_overlap() -> None:
    community = annotation([(0, 4, "a"), (5, 6, "a"), (7, 8, "b"), (9, 9.02, "c")])
    native = annotation([(0, 2, "z"), (2, 4, "b")])
    result = union_diarization(community, native)
    assert rows(result) == [
        (0, 2, "N:z"),
        (2, 4, "N:b"),
        (5, 6, "N:b"),
        (7, 8, "C:b"),
        (9, 9.02, "C:c"),
    ]


def test_duplicate_community_and_native_tracks_cannot_overweight_mapping() -> None:
    community = annotation([(0, 1, "a"), (0, 1, "a"), (2, 3.5, "a"), (5, 6, "a")])
    native = annotation([(0, 1, "x"), (0, 1, "x"), (2, 3.5, "y")])
    assert rows(union_diarization(community, native)) == [
        (0, 1, "N:x"),
        (2, 3.5, "N:y"),
        (5, 6, "N:y"),
    ]


def test_identical_native_intervals_keep_both_speakers_and_namespaces() -> None:
    community = annotation([(2, 3, "L0")])
    native = annotation([(0, 1, "L0"), (0, 1, "L1")])
    assert rows(union_diarization(community, native)) == [
        (0, 1, "N:L0"),
        (0, 1, "N:L1"),
        (2, 3, "C:L0"),
    ]


@pytest.mark.parametrize(
    "label,start,end", [(1, 0.0, 2.0), ("a", -1.0, 2.0), ("a", 0.0, float("inf"))]
)
def test_union_rejects_malformed_native_rows(
    label: object, start: float, end: float
) -> None:
    malformed = Annotation()
    malformed[Segment(start, end), "track"] = label
    with pytest.raises(NemotronStateError):
        union_diarization(annotation([(0, 1, "a")]), malformed)
