"""Confirmed phrase ends follow lexical spans through revised word timing."""

from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

from backend.tests.test_live_settling_line_edges import _sync_delta
from backend.tests.test_live_settling_publication import _active, _request, _seed
from backend.tests.test_live_settling_publication import (
    settling_engine as settling_engine,
)
from backend.utils.canonical_pipeline.live_labels import (
    LiveLabelDecision,
    apply_live_speaker_labels,
)
from backend.utils.canonical_pipeline.live_settling import (
    expire_live_settling,
    publish_live_settling,
)
from backend.utils.canonical_pipeline.live_settling_boundaries import remap_phrase_end
from backend.utils.canonical_pipeline.live_settling_projection import (
    SETTLING_KEY,
    row_words,
)


@pytest.mark.parametrize(
    ("initial", "retimed", "phrase", "split_speakers"),
    [
        (
            [("Hello", 100, 500), (" world", 750, 1000)],
            [("Hello", 100, 800), (" world", 1050, 1200)],
            "Hello world",
            False,
        ),
        (
            [("goodbye", 100, 1000)],
            [("good", 100, 800), (" bye", 1050, 1200)],
            "good bye",
            False,
        ),
        (
            [("good", 100, 400), (" bye", 600, 1000)],
            [("goodbye", 1050, 1200)],
            "goodbye",
            False,
        ),
        (
            [("Hello", 100, 500), (" world", 750, 1000)],
            [("Hello", 100, 800), (" earth", 1050, 1200)],
            "Hello earth",
            False,
        ),
        (
            [("good", 100, 400), (" bye", 600, 1000)],
            [("goodbye", 1050, 1200)],
            "goodbye",
            True,
        ),
        (
            [("Hello", 100, 500), (" world", 750, 1000)],
            [("Hello", 100, 800), (" world", 1050, 1200), ("!", None, None)],
            "Hello world!",
            False,
        ),
        (
            [("goodbye", 100, 1000)],
            [("good", 100, 800), (" bye", 1050, 1200), ("!", None, None)],
            "good bye!",
            False,
        ),
        (
            [("Hello", 100, 500), (" world", 750, 1000)],
            [("Hello", 100, 800), (" earth", 1050, 1200), ("!", None, None)],
            "Hello earth!",
            False,
        ),
    ],
    ids=[
        "retiming",
        "split-and-retiming",
        "merge-and-retiming",
        "lexical-change-and-retiming",
        "merge-across-speaker-rows",
        "punctuation-after-unchanged-terminal",
        "punctuation-after-split-terminal",
        "punctuation-after-replaced-terminal",
    ],
)
def test_confirmed_phrase_end_survives_retiming_and_retokenisation(
    settling_engine: Engine,
    initial: list[tuple[str, int, int]],
    retimed: list[tuple[str, int | None, int | None]],
    phrase: str,
    split_speakers: bool,
) -> None:
    recording_id = _seed(settling_engine, k=99)
    client: dict[str, Any] = {}
    with Session(settling_engine) as session:
        assert publish_live_settling(
            session,
            request=_request(session, recording_id, 0),
            words=[
                {"word": text, "start_ms": start, "end_ms": end}
                for text, start, end in initial
            ],
            decide_speaker=(
                lambda start, _end: LiveLabelDecision(
                    "A" if start < 600 else "B", False, None
                )
            )
            if split_speakers
            else None,
        ).accepted
        session.commit()
        cursor, _ = _sync_delta(session, recording_id, 0, client)
        assert expire_live_settling(
            session,
            # Keep a confirmed closure under the strict >1 s line-pause rule.
            request=replace(
                _request(session, recording_id, 1, end_ms=2001), silence_start_ms=1000
            ),
        ).accepted
        session.commit()
        cursor, _ = _sync_delta(session, recording_id, cursor, client)
        words = [
            {"word": text, "start_ms": start, "end_ms": end}
            for text, start, end in [*retimed, (" again", 1400, 1600)]
        ]
        for step in (2, 3):
            assert publish_live_settling(
                session,
                request=_request(session, recording_id, step, end_ms=4000),
                words=words,
            ).accepted
            session.commit()
            rows = _active(session, recording_id)
            assert [row.text for row in rows] == [phrase, "again"]
            assert rows[0].confidence_payload[SETTLING_KEY]["boundary_locked"]
            assert rows[0].confidence_payload[SETTLING_KEY]["closed_at_ms"] == 1000
            assert not any(word["locked"] for row in rows for word in row_words(row))
            cursor, _ = _sync_delta(session, recording_id, cursor, client)


def test_retimed_phrase_end_survives_speaker_split_and_rejoin(
    settling_engine: Engine,
) -> None:
    recording_id = _seed(settling_engine, k=99)
    with Session(settling_engine) as session:
        assert publish_live_settling(
            session,
            request=_request(session, recording_id, 0),
            words=[
                {"word": "Hello", "start_ms": 100, "end_ms": 500},
                {"word": " world", "start_ms": 750, "end_ms": 1000},
            ],
        ).accepted
        session.commit()
        assert expire_live_settling(
            session,
            request=replace(
                _request(session, recording_id, 1, end_ms=2001), silence_start_ms=1000
            ),
        ).accepted
        session.commit()
        assert publish_live_settling(
            session,
            request=_request(session, recording_id, 2, end_ms=4000),
            words=[
                {"word": "Hello", "start_ms": 100, "end_ms": 800},
                {"word": " world", "start_ms": 1050, "end_ms": 1200},
                {"word": " again", "start_ms": 1400, "end_ms": 1600},
            ],
        ).accepted
        session.commit()
        expected = [
            word["id"]
            for row in _active(session, recording_id)
            for word in row_words(row)
        ]
        assert apply_live_speaker_labels(
            session,
            recording_id=recording_id,
            settled_ms=0,
            decide=lambda start, _end: LiveLabelDecision(
                "A" if start < 1050 else "B", False, None
            ),
        ).changed
        session.commit()
        rows = _active(session, recording_id)
        assert [(row.text, row.speaker_label) for row in rows] == [
            ("Hello", "A"),
            ("world", "B"),
            ("again", "B"),
        ]
        assert [word["id"] for row in rows for word in row_words(row)] == expected
        assert apply_live_speaker_labels(
            session,
            recording_id=recording_id,
            settled_ms=0,
            decide=lambda _start, _end: LiveLabelDecision("A", False, None),
        ).changed
        session.commit()
        rows = _active(session, recording_id)
        assert [row.text for row in rows] == ["Hello world", "again"]
        assert [word["id"] for row in rows for word in row_words(row)] == expected
        assert rows[0].confidence_payload[SETTLING_KEY]["closed_at_ms"] == 1000


def test_phrase_mapping_keeps_repetitions_and_multiple_ends_ordered() -> None:
    current = [
        {"id": "first", "word": "good", "start_ms": 100, "end_ms": 800},
        {"id": "split", "word": " bye", "start_ms": 1050, "end_ms": 1200},
        {"id": "second", "word": " good", "start_ms": 1300, "end_ms": 1350},
        {"id": "later", "word": " bye", "start_ms": 1400, "end_ms": 1500},
    ]
    first = [{"id": "first", "word": "goodbye", "start_ms": 100, "end_ms": 1000}]
    second = [{"id": "second", "word": "goodbye", "start_ms": 1200, "end_ms": 1600}]
    assert remap_phrase_end(first, current) == 1
    assert remap_phrase_end(second, current, after_index=1) == 3


def test_phrase_mapping_rejects_identical_wording_from_unrelated_later_audio() -> None:
    previous = [{"id": "old", "word": "goodbye", "start_ms": 100, "end_ms": 1000}]
    current = [{"id": "later", "word": "goodbye", "start_ms": 3000, "end_ms": 3500}]
    assert remap_phrase_end(previous, current) is None


@pytest.mark.parametrize("split", [False, True])
def test_phrase_mapping_keeps_wholly_unaligned_atoms_outside_anchor_groups(
    split: bool,
) -> None:
    previous = [
        {
            "id": "old",
            "word": "goodbye" if split else "bye",
            "start_ms": 100,
            "end_ms": 1000,
        }
    ]
    current = [
        {"id": "old", "word": "good", "start_ms": 100, "end_ms": 800},
        {
            "id": "split" if split else "old",
            "word": "bye",
            "start_ms": 1050,
            "end_ms": 1200,
        },
        {
            "id": "atom",
            "word": "!",
            "start_ms": None,
            "end_ms": None,
            "observed_audio_start_ms": 1200,
            "observed_audio_end_ms": 1300,
        },
        {"id": "later", "word": "again", "start_ms": 1400, "end_ms": 1600},
    ]
    if not split:
        current = current[1:]
    assert remap_phrase_end(previous, current) == (1 if split else 0)
