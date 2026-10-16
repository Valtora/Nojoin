"""The identification step says who came closest, whether or not it matched.

``_identity_from_voiceprint`` decides through ``find_matching_global_speaker``
and records the ranking behind the decision, so a worker log answers "closest
to whom?" rather than only "how close?".
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.processing.embedding_version import EMBEDDING_METHOD_VERSION
from backend.worker.tasks import speaker_assignment


def _person(pk: int, name: str, embedding: list[float]) -> SimpleNamespace:
    return SimpleNamespace(
        id=pk,
        name=name,
        embedding=embedding,
        embedding_version=EMBEDDING_METHOD_VERSION,
        is_voiceprint_locked=True,
    )


def _identify(monkeypatch, people: list[SimpleNamespace], probe: list[float]):
    metrics: list[dict] = []
    monkeypatch.setattr(
        speaker_assignment,
        "_matchable_global_speakers",
        lambda session, user_id: people,
    )
    monkeypatch.setattr(
        speaker_assignment,
        "record_pipeline_metric",
        lambda **kwargs: metrics.append(kwargs),
    )
    identity = speaker_assignment._identity_from_voiceprint(
        MagicMock(),
        SimpleNamespace(id=139, user_id=1),
        probe,
        "SPEAKER_01",
    )
    return identity, metrics


def test_a_miss_names_the_closest_person(monkeypatch, caplog) -> None:
    caplog.set_level(logging.INFO)
    people = [
        _person(1, "Alice Example", [0.56, 0.83, 0.0]),
        _person(2, "Bob Example", [0.3, 0.0, 0.95]),
    ]

    identity, metrics = _identify(monkeypatch, people, [1.0, 0.0, 0.0])

    assert identity is None
    assert "No match found for SPEAKER_01 (closest: Alice Example 0.56)" in caplog.text

    assert metrics[0]["stage"] == "speaker_identification"
    assert metrics[0]["recording_id"] == 139
    payload = metrics[0]["payload"]
    assert payload["label"] == "SPEAKER_01"
    assert payload["matched_name"] is None
    assert payload["comparable_people"] == 2
    assert [c["name"] for c in payload["candidates"]] == [
        "Alice Example",
        "Bob Example",
    ]
    assert payload["candidates"][0]["cosine"] == pytest.approx(0.56, abs=1e-2)


def test_an_ambiguous_match_names_both_rivals(monkeypatch, caplog) -> None:
    caplog.set_level(logging.INFO)
    people = [
        _person(1, "Alice", [0.8, 0.6, 0.0]),
        _person(2, "Alicia", [0.78, 0.626, 0.0]),
    ]

    identity, metrics = _identify(monkeypatch, people, [1.0, 0.0, 0.0])

    assert identity is None
    assert (
        "Ambiguous match for SPEAKER_01: Alice 0.80 and Alicia 0.78 are within "
        "the 0.05 margin." in caplog.text
    )
    assert metrics[0]["payload"]["matched_name"] is None


def test_a_match_is_recorded_with_its_score(monkeypatch, caplog) -> None:
    caplog.set_level(logging.INFO)
    people = [
        _person(22, "Bob Example", [0.84, 0.54, 0.0]),
        _person(2, "Carol Example", [0.3, 0.0, 0.95]),
    ]

    identity, metrics = _identify(monkeypatch, people, [1.0, 0.0, 0.0])

    assert identity is not None
    assert identity.name == "Bob Example"
    assert identity.global_speaker_id == 22
    assert "Identified SPEAKER_01 as Bob Example (Score: 0.84)" in caplog.text
    payload = metrics[0]["payload"]
    assert payload["matched_global_speaker_id"] == 22
    assert payload["score"] == pytest.approx(0.84, abs=1e-2)
    assert payload["candidates"][0]["name"] == "Bob Example"
