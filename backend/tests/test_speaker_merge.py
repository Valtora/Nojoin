"""Unit tests for the embedding-based speaker merge pass."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session, select

from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.processing.embedding_core import EMBEDDING_METHOD_VERSION
from backend.processing.speaker_merge import merge_duplicate_speakers
from backend.tests.sqlite_schemas import (
    GLOBAL_SPEAKERS_SCHEMA,
    P_TAGS_SCHEMA,
    PEOPLE_TAGS_SCHEMA,
    RECORDING_SPEAKER_DRIFT_REFUSALS_SCHEMA,
    RECORDING_SPEAKERS_SCHEMA,
    TRANSCRIPT_UTTERANCES_SCHEMA,
    TRANSCRIPTS_SCHEMA,
)


@dataclass
class FakeSpeaker:
    id: int
    diarization_label: str
    embedding: Optional[list[float]] = None
    merged_into_id: Optional[int] = None
    name: Optional[str] = None
    local_name: Optional[str] = None
    global_speaker_id: Optional[int] = None
    embedding_version: Optional[int] = EMBEDDING_METHOD_VERSION


@dataclass
class FakeGlobalSpeaker:
    id: int
    name: str
    embedding: Optional[list[float]] = None
    embedding_version: Optional[int] = EMBEDDING_METHOD_VERSION


def _make_embedding(value: float, dim: int = 4) -> list[float]:
    return [value] * dim


def _fake_session_with_speakers(
    speakers: list[FakeSpeaker],
    people: tuple[FakeGlobalSpeaker, ...] = (),
) -> MagicMock:
    session = MagicMock()
    session.execute.return_value.scalars.return_value.all.return_value = list(speakers)
    rows: dict[type, list] = {RecordingSpeaker: speakers, GlobalSpeaker: list(people)}
    session.get.side_effect = lambda cls, pk: next(
        (row for row in rows.get(cls, []) if row.id == pk), None
    )
    return session


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_merge_speakers_above_threshold(mock_counts: MagicMock) -> None:
    speakers = [
        FakeSpeaker(
            id=1, diarization_label="SPEAKER_00", embedding=_make_embedding(1.0)
        ),
        FakeSpeaker(
            id=2, diarization_label="SPEAKER_01", embedding=_make_embedding(0.99)
        ),
        FakeSpeaker(
            id=3, diarization_label="SPEAKER_02", embedding=_make_embedding(-1.0)
        ),
    ]
    mock_counts.return_value = {1: 10, 2: 5, 3: 8}
    session = _fake_session_with_speakers(speakers)

    segments = [
        {"speaker": "SPEAKER_01", "overlapping_speakers": []},
        {"speaker": "SPEAKER_00", "overlapping_speakers": ["SPEAKER_01"]},
    ]

    merge_pairs = merge_duplicate_speakers(
        session, recording_id=1, threshold=0.70, segments=segments
    )

    assert len(merge_pairs) == 1
    merged_id, survivor_id = merge_pairs[0]
    assert survivor_id == 1
    assert merged_id == 2
    assert speakers[1].merged_into_id == 1
    assert segments[0]["speaker"] == "SPEAKER_00"
    # The line's own speaker absorbed the one overlapping it, so nothing is left.
    assert segments[1]["overlapping_speakers"] == []


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_no_merge_below_threshold(mock_counts: MagicMock) -> None:
    speakers = [
        FakeSpeaker(
            id=1, diarization_label="SPEAKER_00", embedding=_make_embedding(1.0)
        ),
        FakeSpeaker(
            id=2, diarization_label="SPEAKER_01", embedding=_make_embedding(-1.0)
        ),
    ]
    mock_counts.return_value = {1: 10, 2: 5}
    session = _fake_session_with_speakers(speakers)

    merge_pairs = merge_duplicate_speakers(session, recording_id=1, threshold=0.70)

    assert merge_pairs == []
    assert speakers[0].merged_into_id is None
    assert speakers[1].merged_into_id is None


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_skips_speakers_without_embeddings(mock_counts: MagicMock) -> None:
    speakers = [
        FakeSpeaker(
            id=1, diarization_label="SPEAKER_00", embedding=_make_embedding(1.0)
        ),
        FakeSpeaker(id=2, diarization_label="SPEAKER_01", embedding=None),
    ]
    mock_counts.return_value = {1: 10}
    session = _fake_session_with_speakers(speakers)

    merge_pairs = merge_duplicate_speakers(session, recording_id=1, threshold=0.70)

    assert merge_pairs == []


def test_single_speaker_returns_empty() -> None:
    speakers = [
        FakeSpeaker(
            id=1, diarization_label="SPEAKER_00", embedding=_make_embedding(1.0)
        ),
    ]
    session = _fake_session_with_speakers(speakers)

    merge_pairs = merge_duplicate_speakers(session, recording_id=1, threshold=0.70)

    assert merge_pairs == []


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_transitive_merge(mock_counts: MagicMock) -> None:
    speakers = [
        FakeSpeaker(
            id=1, diarization_label="SPEAKER_00", embedding=_make_embedding(1.0)
        ),
        FakeSpeaker(
            id=2, diarization_label="SPEAKER_01", embedding=_make_embedding(0.98)
        ),
        FakeSpeaker(
            id=3, diarization_label="SPEAKER_02", embedding=_make_embedding(0.95)
        ),
    ]
    mock_counts.return_value = {1: 10, 2: 5, 3: 3}
    session = _fake_session_with_speakers(speakers)

    merge_pairs = merge_duplicate_speakers(session, recording_id=1, threshold=0.70)

    assert len(merge_pairs) == 2
    merged_ids = {pair[0] for pair in merge_pairs}
    survivor_ids = {pair[1] for pair in merge_pairs}
    assert merged_ids == {2, 3}
    assert survivor_ids == {1}


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_survivor_has_most_utterances(mock_counts: MagicMock) -> None:
    speakers = [
        FakeSpeaker(
            id=1, diarization_label="SPEAKER_00", embedding=_make_embedding(1.0)
        ),
        FakeSpeaker(
            id=2, diarization_label="SPEAKER_01", embedding=_make_embedding(0.99)
        ),
    ]
    mock_counts.return_value = {1: 3, 2: 20}
    session = _fake_session_with_speakers(speakers)

    merge_pairs = merge_duplicate_speakers(session, recording_id=1, threshold=0.70)

    assert len(merge_pairs) == 1
    merged_id, survivor_id = merge_pairs[0]
    assert survivor_id == 2
    assert merged_id == 1


# --- observability ----------------------------------------------------------
# A pass that merged nothing used to emit no log line at all, making it
# indistinguishable from a pass that never ran. These lock that shut.


def _captured_metrics(monkeypatch) -> list[dict]:
    events: list[dict] = []
    monkeypatch.setattr(
        "backend.processing.speaker_merge.record_pipeline_metric",
        lambda **kwargs: events.append(kwargs) or kwargs,
    )
    return events


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_metric_is_emitted_even_when_nothing_merges(
    mock_counts: MagicMock, monkeypatch
) -> None:
    events = _captured_metrics(monkeypatch)
    speakers = [
        FakeSpeaker(
            id=1, diarization_label="SPEAKER_00", embedding=_make_embedding(1.0)
        ),
        FakeSpeaker(
            id=2, diarization_label="SPEAKER_01", embedding=_make_embedding(-1.0)
        ),
    ]
    mock_counts.return_value = {}
    session = _fake_session_with_speakers(speakers)

    assert merge_duplicate_speakers(session, recording_id=7, threshold=0.70) == []

    assert len(events) == 1
    payload = events[0]["payload"]
    assert events[0]["stage"] == "speaker_merge_pass"
    assert payload["reason"] is None
    assert payload["merged_pair_count"] == 0
    # The near-miss score is the whole point: it separates "same voice, missed"
    # from "genuinely different voices".
    assert len(payload["pairs"]) == 1
    assert payload["pairs"][0]["cosine"] == -1.0
    assert payload["pairs"][0]["merged"] is False


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_metric_explains_why_a_pass_could_not_run(
    mock_counts: MagicMock, monkeypatch
) -> None:
    events = _captured_metrics(monkeypatch)
    speakers = [
        FakeSpeaker(
            id=1, diarization_label="SPEAKER_00", embedding=_make_embedding(1.0)
        ),
        FakeSpeaker(id=2, diarization_label="SPEAKER_01", embedding=None),
    ]
    mock_counts.return_value = {}
    session = _fake_session_with_speakers(speakers)

    assert merge_duplicate_speakers(session, recording_id=7) == []

    payload = events[0]["payload"]
    assert payload["reason"] == "fewer_than_two_speakers_with_embeddings"
    assert payload["skipped_no_embedding"] == 1
    assert payload["eligible_speaker_count"] == 1


# --- survivor selection -----------------------------------------------------


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_survivor_falls_back_to_speech_duration_without_utterances(
    mock_counts: MagicMock,
) -> None:
    """On the import path no utterance rows exist yet when this pass runs.

    Without speech duration the tiebreak was effectively arbitrary, so a
    two-minute fragment could win over a fifty-minute cluster.
    """
    speakers = [
        FakeSpeaker(
            id=1, diarization_label="SPEAKER_00", embedding=_make_embedding(1.0)
        ),
        FakeSpeaker(
            id=2, diarization_label="SPEAKER_01", embedding=_make_embedding(0.99)
        ),
    ]
    mock_counts.return_value = {}
    session = _fake_session_with_speakers(speakers)

    merge_pairs = merge_duplicate_speakers(
        session,
        recording_id=1,
        threshold=0.70,
        speech_seconds={"SPEAKER_00": 90.0, "SPEAKER_01": 3000.0},
    )

    assert merge_pairs == [(1, 2)]


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_identified_speaker_survives_over_a_larger_anonymous_one(
    mock_counts: MagicMock,
) -> None:
    """A merge must never discard a resolved person for an anonymous cluster."""
    speakers = [
        FakeSpeaker(
            id=1,
            diarization_label="SPEAKER_00",
            embedding=_make_embedding(1.0),
            global_speaker_id=42,
        ),
        FakeSpeaker(
            id=2, diarization_label="SPEAKER_01", embedding=_make_embedding(0.99)
        ),
    ]
    mock_counts.return_value = {}
    session = _fake_session_with_speakers(speakers)

    merge_pairs = merge_duplicate_speakers(
        session,
        recording_id=1,
        threshold=0.70,
        speech_seconds={"SPEAKER_00": 10.0, "SPEAKER_01": 5000.0},
    )

    assert merge_pairs == [(2, 1)]


# --- merges the score must not be allowed to force ---------------------------


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_speakers_resolved_to_different_people_are_never_merged(
    mock_counts: MagicMock, monkeypatch
) -> None:
    events = _captured_metrics(monkeypatch)
    speakers = [
        FakeSpeaker(
            id=1,
            diarization_label="SPEAKER_00",
            embedding=_make_embedding(1.0),
            global_speaker_id=10,
        ),
        FakeSpeaker(
            id=2,
            diarization_label="SPEAKER_01",
            embedding=_make_embedding(1.0),
            global_speaker_id=20,
        ),
    ]
    mock_counts.return_value = {}
    session = _fake_session_with_speakers(speakers)

    assert merge_duplicate_speakers(session, recording_id=1, threshold=0.70) == []

    pair = events[0]["payload"]["pairs"][0]
    assert pair["cosine"] == 1.0
    assert pair["merged"] is False
    assert pair["blocked_by"] == "distinct_global_speakers"


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_speakers_manually_named_differently_are_never_merged(
    mock_counts: MagicMock,
) -> None:
    speakers = [
        FakeSpeaker(
            id=1,
            diarization_label="SPEAKER_00",
            embedding=_make_embedding(1.0),
            local_name="Alice",
        ),
        FakeSpeaker(
            id=2,
            diarization_label="SPEAKER_01",
            embedding=_make_embedding(1.0),
            local_name="Bob",
        ),
    ]
    mock_counts.return_value = {}
    session = _fake_session_with_speakers(speakers)

    assert merge_duplicate_speakers(session, recording_id=1, threshold=0.70) == []


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_embeddings_from_different_methods_are_never_compared(
    mock_counts: MagicMock, monkeypatch
) -> None:
    """A cross-version cosine score is not a similarity and must not merge."""
    events = _captured_metrics(monkeypatch)
    speakers = [
        FakeSpeaker(
            id=1,
            diarization_label="SPEAKER_00",
            embedding=_make_embedding(1.0),
            embedding_version=EMBEDDING_METHOD_VERSION,
        ),
        FakeSpeaker(
            id=2,
            diarization_label="SPEAKER_01",
            embedding=_make_embedding(1.0),
            embedding_version=EMBEDDING_METHOD_VERSION - 1,
        ),
    ]
    mock_counts.return_value = {}
    session = _fake_session_with_speakers(speakers)

    assert merge_duplicate_speakers(session, recording_id=1, threshold=0.70) == []

    payload = events[0]["payload"]
    assert payload["skipped_version_mismatch"] == 1
    assert payload["reason"] == "fewer_than_two_speakers_with_embeddings"


# --- transitive identity collisions -----------------------------------------

# A three-node topology in which no single pair breaks the direct guard but the
# component does. Cosines: A~X 0.844, X~B 0.813, A~B 0.608, so at the 0.70
# default the anonymous X bridges two identified speakers that must stay apart.
BRIDGE_A = [1.0, 0.0, 0.0]
BRIDGE_X = [0.844, 0.536343, 0.0]
BRIDGE_B = [0.608, 0.55906, 0.563727]


def _components(speakers: list[FakeSpeaker]) -> list[set[int]]:
    """Group speakers by the survivor each ended up pointing at."""
    groups: dict[int, set[int]] = {}
    for speaker in speakers:
        root = speaker.merged_into_id or speaker.id
        groups.setdefault(root, set()).add(speaker.id)
    return list(groups.values())


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_anonymous_speaker_cannot_bridge_two_identified_people(
    mock_counts: MagicMock, monkeypatch
) -> None:
    """An anonymous speaker must not drag two identified people into one group.

    The direct A~B link is refused, but union-find used to reach the same result
    through X anyway, collapsing one confirmed identification into the other.
    """
    events = _captured_metrics(monkeypatch)
    speakers = [
        FakeSpeaker(
            id=1,
            diarization_label="SPEAKER_A",
            embedding=BRIDGE_A,
            global_speaker_id=10,
        ),
        FakeSpeaker(id=2, diarization_label="SPEAKER_X", embedding=BRIDGE_X),
        FakeSpeaker(
            id=3,
            diarization_label="SPEAKER_B",
            embedding=BRIDGE_B,
            global_speaker_id=20,
        ),
    ]
    mock_counts.return_value = {}
    session = _fake_session_with_speakers(speakers)

    merge_duplicate_speakers(session, recording_id=1, threshold=0.70)

    by_id = {s.id: s for s in speakers}
    for component in _components(speakers):
        global_ids = {
            by_id[speaker_id].global_speaker_id
            for speaker_id in component
            if by_id[speaker_id].global_speaker_id is not None
        }
        assert len(global_ids) <= 1, (
            f"component {sorted(component)} holds distinct global speakers "
            f"{sorted(global_ids)}"
        )

    # The stronger link wins: X joins A at 0.844 rather than B at 0.813.
    assert by_id[2].merged_into_id == 1
    assert by_id[3].merged_into_id is None

    pairs = {(p["a_id"], p["b_id"]): p for p in events[0]["payload"]["pairs"]}
    assert pairs[(1, 3)]["blocked_by"] == "distinct_global_speakers"
    assert pairs[(2, 3)]["blocked_by"] == "distinct_global_speakers_in_component"
    assert pairs[(2, 3)]["merged"] is False
    assert pairs[(1, 2)]["merged"] is True


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_anonymous_speaker_cannot_bridge_two_manually_named_people(
    mock_counts: MagicMock, monkeypatch
) -> None:
    """The same hole exists for manual names, which are equally strong evidence."""
    events = _captured_metrics(monkeypatch)
    speakers = [
        FakeSpeaker(
            id=1,
            diarization_label="SPEAKER_A",
            embedding=BRIDGE_A,
            local_name="Alice",
        ),
        FakeSpeaker(id=2, diarization_label="SPEAKER_X", embedding=BRIDGE_X),
        FakeSpeaker(
            id=3,
            diarization_label="SPEAKER_B",
            embedding=BRIDGE_B,
            local_name="Bob",
        ),
    ]
    mock_counts.return_value = {}
    session = _fake_session_with_speakers(speakers)

    merge_duplicate_speakers(session, recording_id=1, threshold=0.70)

    by_id = {s.id: s for s in speakers}
    for component in _components(speakers):
        names = {
            by_id[speaker_id].local_name
            for speaker_id in component
            if by_id[speaker_id].local_name
        }
        assert len(names) <= 1, (
            f"component {sorted(component)} holds distinct manual names {sorted(names)}"
        )

    pairs = {(p["a_id"], p["b_id"]): p for p in events[0]["payload"]["pairs"]}
    assert pairs[(1, 3)]["blocked_by"] == "distinct_manual_names"
    assert pairs[(2, 3)]["blocked_by"] == "distinct_manual_names_in_component"


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_the_strongest_link_claims_a_contested_anonymous_speaker(
    mock_counts: MagicMock,
) -> None:
    """With B the closer match, X must join B instead of A.

    Mirrors the bridge case with the scores swapped, so the assertion is about
    the score ordering rather than about speaker or list order.
    """
    speakers = [
        FakeSpeaker(
            id=1,
            diarization_label="SPEAKER_A",
            embedding=BRIDGE_B,
            global_speaker_id=10,
        ),
        FakeSpeaker(id=2, diarization_label="SPEAKER_X", embedding=BRIDGE_X),
        FakeSpeaker(
            id=3,
            diarization_label="SPEAKER_B",
            embedding=BRIDGE_A,
            global_speaker_id=20,
        ),
    ]
    mock_counts.return_value = {}
    session = _fake_session_with_speakers(speakers)

    merge_duplicate_speakers(session, recording_id=1, threshold=0.70)

    by_id = {s.id: s for s in speakers}
    assert by_id[2].merged_into_id == 3
    assert by_id[1].merged_into_id is None


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_a_chain_does_not_join_two_clusters_that_score_below_threshold(
    mock_counts: MagicMock, monkeypatch
) -> None:
    """Complete linkage: B scores 0.608 against A, so X cannot bridge them.

    X joins A on the stronger link. The X~B link clears the threshold on its
    own, but the group it would form holds A and B, and A~B is below it.
    """
    events = _captured_metrics(monkeypatch)
    speakers = [
        FakeSpeaker(
            id=1,
            diarization_label="SPEAKER_A",
            embedding=BRIDGE_A,
            global_speaker_id=10,
        ),
        FakeSpeaker(id=2, diarization_label="SPEAKER_X", embedding=BRIDGE_X),
        FakeSpeaker(id=3, diarization_label="SPEAKER_B", embedding=BRIDGE_B),
    ]
    mock_counts.return_value = {}
    session = _fake_session_with_speakers(speakers)

    merge_duplicate_speakers(session, recording_id=1, threshold=0.70)

    by_id = {s.id: s for s in speakers}
    assert by_id[1].merged_into_id is None
    assert by_id[2].merged_into_id == 1
    assert by_id[3].merged_into_id is None

    pairs = {(p["a_id"], p["b_id"]): p for p in events[0]["payload"]["pairs"]}
    assert pairs[(2, 3)]["merged"] is False
    assert pairs[(2, 3)]["blocked_by"] == "cross_pair_below_threshold"
    assert pairs[(2, 3)]["weakest_cross_cosine"] == pytest.approx(0.608, abs=1e-3)


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_a_chain_merges_when_every_pair_clears_the_threshold(
    mock_counts: MagicMock,
) -> None:
    """A person split three ways still merges, provided each fragment scores
    as the same voice against each other fragment."""
    speakers = [
        FakeSpeaker(
            id=1,
            diarization_label="SPEAKER_A",
            embedding=[1.0, 0.0, 0.0],
            global_speaker_id=10,
        ),
        FakeSpeaker(id=2, diarization_label="SPEAKER_X", embedding=[0.9, 0.436, 0.0]),
        FakeSpeaker(id=3, diarization_label="SPEAKER_B", embedding=[0.8, 0.6, 0.0]),
    ]
    mock_counts.return_value = {}
    session = _fake_session_with_speakers(speakers)

    merge_duplicate_speakers(session, recording_id=1, threshold=0.70)

    by_id = {s.id: s for s in speakers}
    assert by_id[1].merged_into_id is None
    assert by_id[2].merged_into_id == 1
    assert by_id[3].merged_into_id == 1


# The pairwise scores of a real failure: two large clusters R and N scoring
# 0.42 against each other, a second cluster of R's voice R2, and a small
# cluster of short backchannel lines X scoring 0.87 against N and 0.74 against
# R. Single linkage joined all four and N became R.
VOICE_R = [1.0, 0.0, 0.0, 0.0]
VOICE_R2 = [0.75, 0.6614, 0.0, 0.0]
VOICE_X = [0.735, 0.0, 0.678, 0.0]
VOICE_N = [0.42, 0.0, 0.828, 0.371]


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_a_backchannel_cluster_cannot_bridge_two_different_voices(
    mock_counts: MagicMock, monkeypatch
) -> None:
    """R keeps R2, N keeps X, and the metric names the pair that stopped the
    bridge and describes each group."""
    events = _captured_metrics(monkeypatch)
    speakers = [
        FakeSpeaker(
            id=1,
            diarization_label="SPEAKER_R",
            embedding=VOICE_R,
            global_speaker_id=22,
        ),
        FakeSpeaker(id=2, diarization_label="SPEAKER_R2", embedding=VOICE_R2),
        FakeSpeaker(id=3, diarization_label="SPEAKER_X", embedding=VOICE_X),
        FakeSpeaker(id=4, diarization_label="SPEAKER_N", embedding=VOICE_N),
    ]
    mock_counts.return_value = {1: 66, 2: 40, 3: 192, 4: 209}
    session = _fake_session_with_speakers(speakers)

    merge_pairs = merge_duplicate_speakers(session, recording_id=1, threshold=0.70)

    assert sorted(merge_pairs) == [(2, 1), (3, 4)]
    by_id = {s.id: s for s in speakers}
    assert by_id[4].merged_into_id is None

    payload = events[0]["payload"]
    assert payload["linkage"] == "complete"
    pairs = {(p["a_id"], p["b_id"]): p for p in payload["pairs"]}
    assert pairs[(1, 3)]["cosine"] == pytest.approx(0.735, abs=1e-3)
    assert pairs[(1, 3)]["merged"] is False
    assert pairs[(1, 3)]["blocked_by"] == "cross_pair_below_threshold"
    # The weakest pair across the two groups is N against R2.
    assert pairs[(1, 3)]["weakest_cross_cosine"] == pytest.approx(0.315, abs=1e-3)
    assert pairs[(1, 4)]["cosine"] == pytest.approx(0.42, abs=1e-3)

    groups = sorted(payload["groups"], key=lambda g: g["survivor_id"])
    assert [g["survivor_label"] for g in groups] == ["SPEAKER_R", "SPEAKER_N"]
    assert groups[0]["merged_labels"] == ["SPEAKER_R2"]
    assert groups[1]["merged_labels"] == ["SPEAKER_X"]
    assert groups[0]["min_cosine"] == pytest.approx(0.75, abs=1e-3)
    assert groups[1]["mean_cosine"] == pytest.approx(0.87, abs=1e-3)


# --- the library voiceprint as the final guard --------------------------------

# A bridge that complete linkage lets through. R is identified, R2 is a second
# cluster of R's voice, N is another person, and X is a cluster of N's short
# backchannel lines scoring 0.92 against N and 0.90 against R. Every pair inside
# the recording clears 0.70, R~N at 0.72 the weakest, so linkage alone puts all
# four in R's group. Against R's library voiceprint, R2 scores 0.70, X 0.65 and
# N 0.40.
LIBRARY_R = [1.0, 0.0, 0.0, 0.0, 0.0]
GUARD_R = [0.9, 0.4359, 0.0, 0.0, 0.0]
GUARD_R2 = [0.7, 0.5047, 0.5052, 0.0, 0.0]
GUARD_X = [0.65, 0.7227, -0.0787, 0.2215, 0.0]
GUARD_N = [0.4, 0.8259, 0.026, 0.2943, 0.2657]

PERSON_ID = 22


def _guard_speakers() -> list[FakeSpeaker]:
    return [
        FakeSpeaker(
            id=1,
            diarization_label="SPEAKER_R",
            embedding=GUARD_R,
            global_speaker_id=PERSON_ID,
        ),
        FakeSpeaker(id=2, diarization_label="SPEAKER_R2", embedding=GUARD_R2),
        FakeSpeaker(id=3, diarization_label="SPEAKER_X", embedding=GUARD_X),
        FakeSpeaker(id=4, diarization_label="SPEAKER_N", embedding=GUARD_N),
    ]


def _person(**overrides) -> FakeGlobalSpeaker:
    fields = {"id": PERSON_ID, "name": "Alice Example", "embedding": LIBRARY_R}
    fields.update(overrides)
    return FakeGlobalSpeaker(**fields)


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_a_cluster_that_does_not_sound_like_the_person_is_refused(
    mock_counts: MagicMock, monkeypatch
) -> None:
    """N stays out of R's group however well it scores inside the recording.

    X joins N first on the strongest link. The R~X link would then put N in
    R's group, and N scores 0.40 against R's library voiceprint, so it is
    refused and X stays with N. R keeps R2, which sounds like R.
    """
    events = _captured_metrics(monkeypatch)
    speakers = _guard_speakers()
    mock_counts.return_value = {1: 66, 2: 40, 3: 192, 4: 209}
    session = _fake_session_with_speakers(speakers, people=(_person(),))

    merge_pairs = merge_duplicate_speakers(session, recording_id=1, threshold=0.70)

    assert sorted(merge_pairs) == [(2, 1), (3, 4)]

    payload = events[0]["payload"]
    assert payload["identified_voiceprint_floor"] == 0.6
    assert payload["library_voiceprints"] == [
        {
            "global_speaker_id": PERSON_ID,
            "name": "Alice Example",
            "available": True,
            "reason": None,
        }
    ]

    pairs = {(p["a_id"], p["b_id"]): p for p in payload["pairs"]}
    refused = pairs[(1, 3)]
    assert refused["cosine"] == pytest.approx(0.90, abs=1e-3)
    assert refused["merged"] is False
    assert refused["blocked_by"] == "below_identified_voiceprint_floor"
    assert refused["identified_global_speaker_id"] == PERSON_ID
    assert refused["identified_name"] == "Alice Example"
    assert refused["failing_label"] == "SPEAKER_N"
    assert refused["identified_voiceprint_cosine"] == pytest.approx(0.40, abs=1e-3)
    # R~N clears the threshold and complete linkage would allow it, but the
    # library guard runs first and is the reason recorded.
    assert pairs[(1, 4)]["blocked_by"] == "below_identified_voiceprint_floor"
    assert "weakest_cross_cosine" not in pairs[(1, 4)]
    assert pairs[(1, 2)]["merged"] is True
    assert pairs[(3, 4)]["merged"] is True

    groups = {g["survivor_label"]: g for g in payload["groups"]}
    assert groups["SPEAKER_R"]["merged_labels"] == ["SPEAKER_R2"]
    assert groups["SPEAKER_R"]["identified_global_speaker_id"] == PERSON_ID
    assert groups["SPEAKER_R"]["merged_identified_voiceprint_cosines"] == [
        pytest.approx(0.70, abs=1e-3)
    ]
    assert groups["SPEAKER_N"]["merged_labels"] == ["SPEAKER_X"]
    assert groups["SPEAKER_N"]["identified_global_speaker_id"] is None
    assert groups["SPEAKER_N"]["merged_identified_voiceprint_cosines"] == [None]


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_a_genuine_second_cluster_of_the_identified_person_still_merges(
    mock_counts: MagicMock, monkeypatch
) -> None:
    """R2 scores 0.70 against R's library voiceprint, above the floor."""
    events = _captured_metrics(monkeypatch)
    speakers = _guard_speakers()[:2]
    mock_counts.return_value = {}
    session = _fake_session_with_speakers(speakers, people=(_person(),))

    assert merge_duplicate_speakers(session, recording_id=1, threshold=0.70) == [(2, 1)]

    group = events[0]["payload"]["groups"][0]
    assert group["identified_global_speaker_id"] == PERSON_ID
    assert group["merged_identified_voiceprint_cosines"] == [
        pytest.approx(0.70, abs=1e-3)
    ]


@pytest.mark.parametrize(
    ("people", "reason"),
    [
        (
            (_person(embedding_version=EMBEDDING_METHOD_VERSION - 1),),
            "embedding_version_mismatch",
        ),
        ((_person(embedding=None),), "no_library_voiceprint"),
        ((), "person_not_found"),
    ],
)
@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_an_unusable_library_voiceprint_leaves_the_merge_to_linkage(
    mock_counts: MagicMock, monkeypatch, people, reason
) -> None:
    """Without a comparable library voiceprint the guard cannot score anyone.

    The pass falls back to complete linkage, which joins all four clusters
    here, and the metric says the library was not consulted.
    """
    events = _captured_metrics(monkeypatch)
    speakers = _guard_speakers()
    mock_counts.return_value = {}
    session = _fake_session_with_speakers(speakers, people=people)

    merge_pairs = merge_duplicate_speakers(session, recording_id=1, threshold=0.70)

    assert sorted(merge_pairs) == [(2, 1), (3, 1), (4, 1)]
    payload = events[0]["payload"]
    [status] = payload["library_voiceprints"]
    assert status["global_speaker_id"] == PERSON_ID
    assert status["available"] is False
    assert status["reason"] == reason
    assert not any(
        p["blocked_by"] == "below_identified_voiceprint_floor" for p in payload["pairs"]
    )
    [group] = payload["groups"]
    assert group["identified_global_speaker_id"] == PERSON_ID
    assert group["merged_identified_voiceprint_cosines"] == [None, None, None]


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_clusters_identified_as_the_same_person_merge_as_before(
    mock_counts: MagicMock, monkeypatch
) -> None:
    """A member carrying the identity is not scored against the library.

    Both clusters are tied to the person already, by identification or by
    hand, so N's own 0.40 against the library voiceprint does not count.
    """
    events = _captured_metrics(monkeypatch)
    speakers = [
        FakeSpeaker(
            id=1,
            diarization_label="SPEAKER_R",
            embedding=GUARD_R,
            global_speaker_id=PERSON_ID,
        ),
        FakeSpeaker(
            id=2,
            diarization_label="SPEAKER_N",
            embedding=GUARD_N,
            global_speaker_id=PERSON_ID,
        ),
    ]
    mock_counts.return_value = {1: 50, 2: 10}
    session = _fake_session_with_speakers(speakers, people=(_person(),))

    assert merge_duplicate_speakers(session, recording_id=1, threshold=0.70) == [(2, 1)]

    pair = events[0]["payload"]["pairs"][0]
    assert pair["merged"] is True
    assert pair["blocked_by"] is None


def test_the_guard_reads_the_library_through_an_orm_session(tmp_path) -> None:
    """The person is loaded with ``session.get`` on a real session, not a mock."""
    engine = create_engine(f"sqlite:///{tmp_path / 'merge.sqlite'}", future=True)
    with engine.begin() as connection:
        for schema in (
            GLOBAL_SPEAKERS_SCHEMA,
            RECORDING_SPEAKERS_SCHEMA,
            RECORDING_SPEAKER_DRIFT_REFUSALS_SCHEMA,
            TRANSCRIPT_UTTERANCES_SCHEMA,
            TRANSCRIPTS_SCHEMA,
            # GlobalSpeaker loads its tags with the row.
            PEOPLE_TAGS_SCHEMA,
            P_TAGS_SCHEMA,
        ):
            connection.execute(text(schema))

    with Session(engine) as session:
        person = _person()
        session.add(
            GlobalSpeaker(
                id=person.id,
                name=person.name,
                embedding=person.embedding,
                embedding_version=person.embedding_version,
            )
        )
        for speaker in _guard_speakers():
            session.add(
                RecordingSpeaker(
                    id=speaker.id,
                    recording_id=1,
                    diarization_label=speaker.diarization_label,
                    embedding=speaker.embedding,
                    embedding_version=speaker.embedding_version,
                    global_speaker_id=speaker.global_speaker_id,
                )
            )
        session.commit()

        merge_pairs = merge_duplicate_speakers(
            session,
            recording_id=1,
            threshold=0.70,
            speech_seconds={"SPEAKER_X": 255.0, "SPEAKER_N": 1405.0},
        )
        session.commit()
        merged_into = {
            row.id: row.merged_into_id for row in session.exec(select(RecordingSpeaker))
        }

    assert sorted(merge_pairs) == [(2, 1), (3, 4)]
    assert merged_into == {1: None, 2: 1, 3: 4, 4: None}
