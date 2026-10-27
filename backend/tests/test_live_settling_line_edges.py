"""Publication regressions for tentative boundaries and failed word alignment."""

import copy
from dataclasses import replace
from typing import Any

from sqlalchemy import Engine
from sqlmodel import Session

from backend.processing.live_limits import DEFAULT_MAX_SEGMENT_S, LIVE_MIN_SILENCE_MS
from backend.tests.test_live_settling_publication import (
    _active,
    _request,
    _seed,
    _words,
)
from backend.tests.test_live_settling_publication import (
    settling_engine as settling_engine,
)
from backend.utils.canonical_pipeline import serialize_canonical_delta
from backend.utils.canonical_pipeline.live_labels import (
    LiveLabelDecision,
    apply_live_speaker_labels,
)
from backend.utils.canonical_pipeline.live_settling import (
    expire_live_settling,
    load_live_settling_words,
    publish_live_settling,
)
from backend.utils.canonical_pipeline.live_settling_projection import (
    SETTLING_KEY,
    line_is_locked,
    row_words,
)
from backend.utils.live_word_settling import plan_settling_window


def _sync_delta(
    session: Session, recording_id: int, cursor: int, client: dict[str, Any]
) -> tuple[int, set[str]]:
    revision, changed, tombstones = serialize_canonical_delta(
        session, recording_id, after_revision=cursor
    )
    for public_id in tombstones:
        client.pop(public_id, None)
    client.update((line["id"], line) for line in changed)
    _, full, _ = serialize_canonical_delta(session, recording_id)
    # Private comparisons and closure metadata update ORM bookkeeping without a
    # public event. Compare every public field except that write timestamp.
    client_content = {
        public_id: {key: value for key, value in line.items() if key != "updated_at"}
        for public_id, line in client.items()
    }
    full_content = {
        line["id"]: {key: value for key, value in line.items() if key != "updated_at"}
        for line in full
    }
    assert client_content == full_content
    word_ids = [word["id"] for line in client.values() for word in line["words"]]
    assert len(word_ids) == len(set(word_ids))
    return revision, set(tombstones)


def test_unknown_tail_rejoins_locked_speaker_prefix_without_freezing_its_boundary(
    settling_engine: Engine,
) -> None:
    recording_id = _seed(settling_engine, k=1)
    client: dict[str, Any] = {}
    cursor = 0
    with Session(settling_engine) as session:
        for step in range(2):
            assert publish_live_settling(
                session,
                request=_request(session, recording_id, step),
                words=_words(),
                decide_speaker=lambda _start, _end: LiveLabelDecision("A", True, 1.0),
            ).accepted
            session.commit()
            cursor, _ = _sync_delta(session, recording_id, cursor, client)
        prefix = copy.deepcopy(row_words(_active(session, recording_id)[0]))
        assert all(word["locked"] for word in prefix)
        assert not line_is_locked(_active(session, recording_id)[0])

        assert publish_live_settling(
            session,
            request=_request(session, recording_id, 2),
            words=[*_words(), {"word": " again", "start_ms": 1000, "end_ms": 1300}],
            decide_speaker=lambda start, _end: LiveLabelDecision(
                "A" if start < 1000 else "UNKNOWN", start < 1000, None
            ),
        ).accepted
        session.commit()
        split = _active(session, recording_id)
        assert [row.speaker_label for row in split] == ["A", "UNKNOWN"]
        assert row_words(split[0]) == prefix
        assert split[0].confidence_payload[SETTLING_KEY]["line_closed"]
        assert not split[0].confidence_payload[SETTLING_KEY]["boundary_locked"]
        assert not line_is_locked(split[0])
        assert not row_words(split[1])[0]["locked"]
        split_ids = {row.public_id for row in split}
        all_words = copy.deepcopy([word for row in split for word in row_words(row)])
        cursor, _ = _sync_delta(session, recording_id, cursor, client)

        assert apply_live_speaker_labels(
            session,
            recording_id=recording_id,
            settled_ms=0,
            decide=lambda _start, _end: LiveLabelDecision("A", True, 1.0),
        ).changed
        session.commit()
        joined = _active(session, recording_id)
        assert len(joined) == 1
        assert joined[0].text == "Hello world again"
        assert joined[0].speaker_label == "A"
        assert row_words(joined[0]) == all_words
        assert not joined[0].confidence_payload[SETTLING_KEY]["line_closed"]
        assert not line_is_locked(joined[0])
        _, tombstones = _sync_delta(session, recording_id, cursor, client)
        assert tombstones == split_ids
        assert len(client) == 1


def test_confirmed_pause_survives_pending_word_retiming_and_speaker_reprojection(
    settling_engine: Engine,
) -> None:
    recording_id = _seed(settling_engine, k=99)
    client: dict[str, Any] = {}
    with Session(settling_engine) as session:
        assert publish_live_settling(
            session,
            request=_request(session, recording_id, 0),
            words=_words(),
            decide_speaker=lambda _start, _end: LiveLabelDecision("A", True, 1.0),
        ).accepted
        session.commit()
        original_ids = [
            word["id"] for word in row_words(_active(session, recording_id)[0])
        ]
        cursor, _ = _sync_delta(session, recording_id, 0, client)
        assert expire_live_settling(
            session,
            request=replace(_request(session, recording_id, 1), silence_start_ms=850),
        ).accepted
        session.commit()
        closed = _active(session, recording_id)[0]
        assert closed.confidence_payload[SETTLING_KEY]["line_closed"]
        assert not line_is_locked(closed)
        assert not any(word["locked"] for word in row_words(closed))
        cursor, _ = _sync_delta(session, recording_id, cursor, client)

        # Retiming leaves only 160 ms between words, below the legacy pause limit.
        retimed = [
            {"word": "Hello", "start_ms": 100, "end_ms": 500},
            {"word": " world", "start_ms": 650, "end_ms": 1040},
            {"word": " again", "start_ms": 1200, "end_ms": 1500},
        ]
        assert retimed[2]["start_ms"] - retimed[1]["end_ms"] < LIVE_MIN_SILENCE_MS
        assert publish_live_settling(
            session,
            request=_request(session, recording_id, 2, end_ms=4000),
            words=retimed,
            decide_speaker=lambda _start, _end: LiveLabelDecision("A", True, 1.0),
        ).accepted
        session.commit()
        rows = _active(session, recording_id)
        assert [row.text for row in rows] == ["Hello world", "again"]
        assert [word["id"] for word in row_words(rows[0])] == original_ids
        assert rows[0].end_ms == 1040
        assert rows[0].confidence_payload[SETTLING_KEY]["line_closed"]
        assert rows[0].confidence_payload[SETTLING_KEY]["boundary_locked"]
        cursor, _ = _sync_delta(session, recording_id, cursor, client)
        assert apply_live_speaker_labels(
            session,
            recording_id=recording_id,
            settled_ms=0,
            decide=lambda _start, _end: LiveLabelDecision("B", False, 1.0),
        ).changed
        session.commit()
        rows = _active(session, recording_id)
        assert [row.text for row in rows] == ["Hello world", "again"]
        assert [row.speaker_label for row in rows] == ["B", "B"]
        assert rows[0].confidence_payload[SETTLING_KEY]["line_closed"]
        _sync_delta(session, recording_id, cursor, client)


def test_confirmed_pause_follows_retokenised_phrase_instead_of_reused_word_id(
    settling_engine: Engine,
) -> None:
    recording_id = _seed(settling_engine, k=99)
    client: dict[str, Any] = {}
    with Session(settling_engine) as session:
        assert publish_live_settling(
            session,
            request=_request(session, recording_id, 0),
            words=[{"word": "goodbye", "start_ms": 100, "end_ms": 1000}],
        ).accepted
        session.commit()
        original = row_words(_active(session, recording_id)[0])[0]
        assert not original["locked"]
        original_id = original["id"]
        cursor, _ = _sync_delta(session, recording_id, 0, client)
        assert expire_live_settling(
            session,
            # Closure now requires more than one second of confirmed silence.
            request=replace(
                _request(session, recording_id, 1, end_ms=2001), silence_start_ms=1000
            ),
        ).accepted
        session.commit()
        assert _active(session, recording_id)[0].confidence_payload[SETTLING_KEY][
            "line_closed"
        ]
        cursor, _ = _sync_delta(session, recording_id, cursor, client)

        # The old terminal UUID now identifies only the phrase's first word.
        assert publish_live_settling(
            session,
            request=_request(session, recording_id, 2, end_ms=4000),
            words=[
                {"word": "good", "start_ms": 100, "end_ms": 500},
                {"word": " bye", "start_ms": 500, "end_ms": 1000},
                {"word": " again", "start_ms": 1400, "end_ms": 1800},
            ],
        ).accepted
        session.commit()
        rows = _active(session, recording_id)
        assert [row.text for row in rows] == ["good bye", "again"]
        words = [word for row in rows for word in row_words(row)]
        assert [word["word"] for word in words] == ["good", " bye", " again"]
        assert words[0]["id"] == original_id
        assert len({word["id"] for word in words}) == 3
        assert (rows[0].start_ms, rows[0].end_ms) == (100, 1000)
        assert rows[0].confidence_payload[SETTLING_KEY]["line_closed"]
        assert rows[0].confidence_payload[SETTLING_KEY]["boundary_locked"]
        assert not any(word["locked"] for word in words)
        _sync_delta(session, recording_id, cursor, client)


def test_confirmed_pause_closes_untimed_atom_and_later_atom_remains_separate(
    settling_engine: Engine,
) -> None:
    recording_id = _seed(settling_engine)
    client: dict[str, Any] = {}
    with Session(settling_engine) as session:
        assert publish_live_settling(
            session,
            request=replace(
                _request(session, recording_id, 0),
                input_start_ms=0,
                metadata={"alignment_error": "aligner unavailable"},
            ),
            words=[
                {"word": "Exact punctuation, stays!", "start_ms": None, "end_ms": None}
            ],
        ).accepted
        session.commit()
        cursor, _ = _sync_delta(session, recording_id, 0, client)
        assert expire_live_settling(
            session,
            request=replace(
                _request(session, recording_id, 1, end_ms=4000), silence_start_ms=2000
            ),
        ).accepted
        session.commit()
        row = _active(session, recording_id)[0]
        assert (row.start_ms, row.end_ms) == (0, 2000)
        assert row.confidence_payload[SETTLING_KEY]["line_closed"]
        assert not line_is_locked(row)
        assert not row_words(row)[0]["locked"]
        cursor, _ = _sync_delta(session, recording_id, cursor, client)

        # Expiry freezes the exact atom after its confirmed pause, with no alignment.
        assert expire_live_settling(
            session, request=_request(session, recording_id, 2, end_ms=12000)
        ).accepted
        session.commit()
        assert line_is_locked(row)
        frozen = copy.deepcopy(row_words(row))
        assert frozen[0]["start_ms"] is None and frozen[0]["end_ms"] is None
        assert frozen[0]["lock_reason"] == "expiry"
        cursor, _ = _sync_delta(session, recording_id, cursor, client)
        assert publish_live_settling(
            session,
            request=replace(
                _request(session, recording_id, 3, end_ms=14000),
                input_start_ms=12000,
                metadata={"alignment_error": "aligner unavailable"},
            ),
            words=[{"word": "Later atom.", "start_ms": None, "end_ms": None}],
        ).accepted
        session.commit()
        rows = _active(session, recording_id)
        assert [row.text for row in rows] == [
            "Exact punctuation, stays!",
            "Later atom.",
        ]
        assert [row.speaker_label for row in rows] == ["UNKNOWN", "UNKNOWN"]
        assert row_words(rows[0]) == frozen
        assert (rows[1].start_ms, rows[1].end_ms) == (12000, 14000)
        assert all(
            word["start_ms"] is None and word["end_ms"] is None
            for row in rows
            for word in row_words(row)
        )
        _sync_delta(session, recording_id, cursor, client)


def test_sustained_alignment_failures_respect_maximum_without_splitting_atoms(
    settling_engine: Engine,
) -> None:
    recording_id = _seed(settling_engine)
    client: dict[str, Any] = {}
    frozen: dict[str, dict[str, Any]] = {}
    cursor = 0
    with Session(settling_engine) as session:
        # Cross two 30 s line limits, retaining the original three-line check.
        for step in range(35):
            end_ms = (step + 1) * 2000
            previous = load_live_settling_words(session, recording_id=recording_id)
            input_start = plan_settling_window(
                previous, audio_end_ms=end_ms, window_ms=10000
            )
            text = f"Unaligned observation {step}, exactly intact!"
            assert publish_live_settling(
                session,
                request=replace(
                    _request(session, recording_id, step, end_ms=end_ms),
                    input_start_ms=input_start,
                    metadata={"alignment_error": "aligner unavailable"},
                ),
                words=[{"word": text, "start_ms": None, "end_ms": None}],
            ).accepted
            session.commit()
            rows = _active(session, recording_id)
            atoms = [word for row in rows for word in row_words(row)]
            assert atoms[-1]["word"] == text
            assert all(
                0 < row.end_ms - row.start_ms <= DEFAULT_MAX_SEGMENT_S * 1000
                for row in rows
            )
            for atom in atoms:
                assert atom["start_ms"] is None and atom["end_ms"] is None
                assert atom["word"].startswith("Unaligned observation ")
                assert atom["word"].endswith(", exactly intact!")
                if atom["id"] in frozen:
                    assert atom == frozen[atom["id"]]
                if atom["locked"]:
                    frozen[atom["id"]] = copy.deepcopy(atom)
            cursor, _ = _sync_delta(session, recording_id, cursor, client)
        assert frozen
        assert len(rows) >= 3
        assert all(
            row.confidence_payload[SETTLING_KEY]["line_closed"] for row in rows[:-1]
        )
        assert all(line_is_locked(row) for row in rows[:-1])
