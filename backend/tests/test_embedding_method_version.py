"""Tests for voiceprint extraction-method versioning.

Cosine similarity between two embeddings only means something when both were
produced the same way. When the extraction method changes, previously stored
voiceprints move to a different region of the vector space; comparing across
that boundary yields a number that looks like a similarity but is not one.
These tests lock the refusal to compare in place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pytest

from backend.processing.embedding import (
    embedding_version_of,
    embeddings_are_comparable,
    find_matching_global_speaker,
)
from backend.processing.embedding_core import (
    EMBEDDING_METHOD_VERSION,
    LEGACY_EMBEDDING_METHOD_VERSION,
)


@dataclass
class FakeGlobalSpeaker:
    id: int
    name: str
    embedding: Optional[list[float]] = field(default=None)
    embedding_version: Optional[int] = EMBEDDING_METHOD_VERSION


def _vec(*values: float) -> list[float]:
    return list(values)


def test_absent_version_is_treated_as_legacy_not_current():
    """A row written before versioning existed is legacy by definition.

    Defaulting the missing value to the current version would silently declare
    every pre-upgrade voiceprint compatible with the new method.
    """
    speaker = FakeGlobalSpeaker(id=1, name="Alice", embedding_version=None)
    assert embedding_version_of(speaker) == LEGACY_EMBEDDING_METHOD_VERSION
    assert LEGACY_EMBEDDING_METHOD_VERSION != EMBEDDING_METHOD_VERSION


def test_unparseable_version_is_treated_as_legacy():
    speaker = FakeGlobalSpeaker(id=1, name="Alice", embedding_version="junk")
    assert embedding_version_of(speaker) == LEGACY_EMBEDDING_METHOD_VERSION


def test_objects_without_the_attribute_are_legacy():
    assert embedding_version_of(object()) == LEGACY_EMBEDDING_METHOD_VERSION


def test_comparability_requires_matching_versions():
    current = FakeGlobalSpeaker(
        id=1, name="A", embedding_version=EMBEDDING_METHOD_VERSION
    )
    other = FakeGlobalSpeaker(
        id=2, name="B", embedding_version=EMBEDDING_METHOD_VERSION
    )
    legacy = FakeGlobalSpeaker(id=3, name="C", embedding_version=None)

    assert embeddings_are_comparable(current, other) is True
    assert embeddings_are_comparable(current, legacy) is False
    assert embeddings_are_comparable(legacy, current) is False


def test_identification_skips_stale_voiceprints():
    """An identical vector must not match when it came from another method."""
    probe = _vec(1.0, 0.0, 0.0)
    stale_but_identical = FakeGlobalSpeaker(
        id=1,
        name="Alice",
        embedding=_vec(1.0, 0.0, 0.0),
        embedding_version=LEGACY_EMBEDDING_METHOD_VERSION,
    )

    match, score = find_matching_global_speaker(
        probe, [stale_but_identical], threshold=0.75, margin=0.05
    )

    assert match is None
    assert score == 0.0


def test_identification_still_matches_current_version_voiceprints():
    probe = _vec(1.0, 0.0, 0.0)
    current = FakeGlobalSpeaker(
        id=1,
        name="Alice",
        embedding=_vec(0.99, 0.01, 0.0),
        embedding_version=EMBEDDING_METHOD_VERSION,
    )
    unrelated = FakeGlobalSpeaker(
        id=2,
        name="Bob",
        embedding=_vec(0.0, 1.0, 0.0),
        embedding_version=EMBEDDING_METHOD_VERSION,
    )

    match, score = find_matching_global_speaker(
        probe, [current, unrelated], threshold=0.75, margin=0.05
    )

    assert match is current
    assert score == pytest.approx(1.0, abs=1e-3)


def test_a_stale_rival_cannot_make_a_valid_match_look_ambiguous():
    """Margin-of-victory must be computed only over comparable candidates.

    A stale voiceprint that happens to score highly would otherwise suppress a
    genuine match by making it look ambiguous.
    """
    probe = _vec(1.0, 0.0, 0.0)
    genuine = FakeGlobalSpeaker(
        id=1,
        name="Alice",
        embedding=_vec(1.0, 0.0, 0.0),
        embedding_version=EMBEDDING_METHOD_VERSION,
    )
    stale_lookalike = FakeGlobalSpeaker(
        id=2,
        name="Bob",
        embedding=_vec(1.0, 0.0, 0.0),
        embedding_version=LEGACY_EMBEDDING_METHOD_VERSION,
    )

    match, _ = find_matching_global_speaker(
        probe, [genuine, stale_lookalike], threshold=0.75, margin=0.05
    )

    assert match is genuine


def test_explicit_method_version_overrides_the_default():
    """Scoring a legacy probe finds legacy voiceprints, not current ones."""
    probe = _vec(1.0, 0.0, 0.0)
    legacy = FakeGlobalSpeaker(
        id=1,
        name="Alice",
        embedding=_vec(1.0, 0.0, 0.0),
        embedding_version=LEGACY_EMBEDDING_METHOD_VERSION,
    )

    match, _ = find_matching_global_speaker(
        probe,
        [legacy],
        threshold=0.75,
        margin=0.05,
        method_version=LEGACY_EMBEDDING_METHOD_VERSION,
    )

    assert match is legacy
