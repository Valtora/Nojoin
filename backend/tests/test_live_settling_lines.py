"""Natural live lines outlast decode steps, word locks and the rolling window."""

import copy
import json
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import soundfile as sf
from sqlalchemy import Engine
from sqlmodel import Session

from backend.processing import live_settling as worker
from backend.processing.live_limits import DEFAULT_MAX_SEGMENT_S
from backend.tests.test_live_settling_publication import (
    _active,
    _request,
    _seed,
    _words,
)
from backend.tests.test_live_settling_publication import (
    settling_engine as settling_engine,
)
from backend.tests.test_live_settling_worker_postgres import worker_db as worker_db
from backend.utils.canonical_pipeline import serialize_canonical_delta
from backend.utils.canonical_pipeline.live_labels import LiveLabelDecision
from backend.utils.canonical_pipeline.live_settling import (
    close_live_settling,
    expire_live_settling,
    load_live_settling_words,
    publish_live_settling,
)
from backend.utils.canonical_pipeline.live_settling_projection import (
    SETTLING_KEY,
    LineBoundaries,
    line_is_locked,
    partition_words,
    row_words,
)
from backend.utils.config_manager import config_manager
from backend.utils.word_text import reconstruct_text_from_words


def _continuous(end_ms: int) -> list[dict[str, Any]]:
    return [
        {"word": f" word-{index}", "start_ms": index * 500, "end_ms": (index + 1) * 500}
        for index in range(max(0, (end_ms - 10000) // 500), end_ms // 500)
    ]


def _apply_delta(
    session: Session, recording_id: int, cursor: int, client: dict[str, Any]
) -> int:
    cursor, changed, tombstones = serialize_canonical_delta(
        session, recording_id, after_revision=cursor
    )
    for public_id in tombstones:
        client.pop(public_id, None)
    client.update((line["id"], line) for line in changed)
    _, full, _ = serialize_canonical_delta(session, recording_id)
    # Private JSON changes update timestamps without emitting public deltas.
    assert {key: value | {"updated_at": None} for key, value in client.items()} == {
        line["id"]: line | {"updated_at": None} for line in full
    }
    return cursor


def test_twelve_steps_extend_one_open_line_through_word_locks_and_window_expiry(
    settling_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        config_manager,
        "get",
        lambda key, default=None: 30 if key == "live_max_segment_s" else default,
    )
    recording_id = _seed(settling_engine)
    client: dict[str, Any] = {}
    identities: dict[str, str] = {}
    locked: dict[str, dict[str, Any]] = {}
    cursor = 0
    with Session(settling_engine) as session:
        for step in range(12):
            end = (step + 1) * 2000
            old = _active(session, recording_id)
            old_id = old[0].public_id if old else None
            old_revision = old[0].revision if old else None
            result = publish_live_settling(
                session,
                request=_request(session, recording_id, step, end_ms=end),
                words=_continuous(end),
            )
            assert result.accepted
            session.commit()
            rows = _active(session, recording_id)
            assert len(rows) == 1
            assert (rows[0].start_ms, rows[0].end_ms) == (0, end)
            assert not line_is_locked(rows[0])
            assert (
                not rows[0].confidence_payload[SETTLING_KEY].get("line_closed", False)
            )
            words = row_words(rows[0])
            assert [word["word"] for word in words] == [
                f" word-{i}" for i in range(end // 500)
            ]
            assert len({word["id"] for word in words}) == len(words)
            for word in words:
                assert word["id"] == identities.setdefault(word["word"], word["id"])
                if word["id"] in locked:
                    assert word == locked[word["id"]]
                if word["locked"]:
                    locked[word["id"]] = copy.deepcopy(word)
            if old:
                # Growing open lines now retain identity so selections survive;
                # the former supersession assertion pinned the replaced policy.
                assert result.tombstones == ()
                assert rows[0].public_id == old_id
                assert old_revision is not None
                assert rows[0].revision == old_revision + 1
                _, changed, tombstones = serialize_canonical_delta(
                    session, recording_id, after_revision=cursor
                )
                assert tombstones == []
                assert len(changed) == 1
                assert changed[0]["id"] == old_id
                assert changed[0]["revision"] == rows[0].revision
            cursor = _apply_delta(session, recording_id, cursor, client)
        assert locked
        retained = load_live_settling_words(
            session, recording_id=recording_id, window_start_ms=22000
        )
        assert len(retained) == 48
        assert close_live_settling(session, recording_id=recording_id)
        session.commit()
        row = _active(session, recording_id)[0]
        assert line_is_locked(row)
        assert all(word["locked"] for word in row_words(row))
        _apply_delta(session, recording_id, cursor, client)


@pytest.mark.parametrize(
    "gap_ms, count", [(319, 1), (320, 1), (500, 1), (1000, 1), (1001, 2), (1200, 2)]
)
def test_pause_boundary_uses_the_final_word_gap_threshold(
    settling_engine: Engine, gap_ms: int, count: int
) -> None:
    recording_id = _seed(settling_engine)
    words = [
        {"word": "One", "start_ms": 1700, "end_ms": 2000},
        {"word": " two", "start_ms": 2000 + gap_ms, "end_ms": 2400 + gap_ms},
    ]
    with Session(settling_engine) as session:
        assert publish_live_settling(
            session,
            request=_request(session, recording_id, 0, end_ms=4000),
            words=words,
        ).accepted
        session.commit()
        rows = _active(session, recording_id)
        assert len(rows) == count
        assert [word["word"] for row in rows for word in row_words(row)] == [
            "One",
            " two",
        ]
        assert rows[0].confidence_payload[SETTLING_KEY]["line_closed"] is (count == 2)
        assert not rows[-1].confidence_payload[SETTLING_KEY]["line_closed"]


def test_closed_line_locks_after_silence_and_later_machine_work_cannot_change_it(
    settling_engine: Engine,
) -> None:
    recording_id = _seed(settling_engine, k=1)
    with Session(settling_engine) as session:
        for step in range(2):
            publish_live_settling(
                session, request=_request(session, recording_id, step), words=_words()
            )
            session.commit()
        row = _active(session, recording_id)[0]
        assert all(word["locked"] for word in row_words(row))
        assert not line_is_locked(row)
        # A real speech-free step closes the line without an extra comparison.
        request = replace(
            _request(session, recording_id, 2, end_ms=4000), silence_start_ms=2000
        )
        before_sequence = request.source_revision
        assert expire_live_settling(session, request=request).changed == 1
        session.commit()
        assert line_is_locked(row)
        assert _request(session, recording_id, 3).source_revision == before_sequence + 1
        snapshot = copy.deepcopy(
            (
                row.public_id,
                row.text,
                row.speaker_label,
                row.start_ms,
                row.end_ms,
                row.confidence_payload,
            )
        )
        result = publish_live_settling(
            session,
            request=_request(session, recording_id, 3, end_ms=6000),
            words=[
                *_words(("Changed", " context")),
                {"word": "After", "start_ms": 4100, "end_ms": 4500},
            ],
            decide_speaker=lambda start, end: LiveLabelDecision("OTHER", True, 1.0),
        )
        assert result.accepted
        session.commit()
        session.refresh(row)
        assert (
            row.public_id,
            row.text,
            row.speaker_label,
            row.start_ms,
            row.end_ms,
            row.confidence_payload,
        ) == snapshot
        assert [line.text for line in _active(session, recording_id)] == [
            "Hello world",
            "After",
        ]


def test_partial_speaker_locks_survive_pending_words_extending_an_open_line(
    settling_engine: Engine,
) -> None:
    recording_id = _seed(settling_engine, k=1)
    with Session(settling_engine) as session:
        decide = lambda start, end: LiveLabelDecision("A", True, 1.0)
        for step in range(2):
            publish_live_settling(
                session,
                request=_request(session, recording_id, step),
                words=_continuous(2000),
                decide_speaker=decide,
            )
            session.commit()
        original = copy.deepcopy(row_words(_active(session, recording_id)[0]))
        publish_live_settling(
            session,
            request=_request(session, recording_id, 2, end_ms=4000),
            words=_continuous(4000),
            decide_speaker=lambda start, end: LiveLabelDecision("A", False, 1.0),
        )
        session.commit()
        assert len(_active(session, recording_id)) == 1
        publish_live_settling(
            session,
            request=_request(session, recording_id, 3, end_ms=4000),
            words=_continuous(4000),
            decide_speaker=lambda start, end: LiveLabelDecision("B", False, 1.0),
        )
        session.commit()
        rows = _active(session, recording_id)
        assert [row.speaker_label for row in rows] == ["A", "B"]
        assert row_words(rows[0]) == original
        assert len([word for row in rows for word in row_words(row)]) == 8


def test_default_maximum_is_the_legacy_line_limit_and_never_splits_a_word() -> None:
    words = [
        {
            "word": f" word-{index}",
            "start_ms": index * 500,
            "end_ms": (index + 1) * 500,
            "id": str(index),
            "locked": True,
        }
        for index in range(68)
    ]
    decisions = [LiveLabelDecision("A", True, 1.0) for _word in words]
    parts = partition_words(words, decisions)
    assert len(parts) == 2
    assert parts[0].words[-1]["end_ms"] == DEFAULT_MAX_SEGMENT_S * 1000
    assert parts[0].line_locked
    assert not parts[-1].closed
    assert [word for part in parts for word in part.words] == words
    unlimited = partition_words(
        words, decisions, boundaries=LineBoundaries(max_segment_ms=0)
    )
    assert len(unlimited) == 1


def test_default_maximum_closes_a_long_run_without_losing_words_or_deltas(
    settling_engine: Engine,
) -> None:
    recording_id = _seed(settling_engine)
    client: dict[str, Any] = {}
    cursor = 0
    with Session(settling_engine) as session:
        for step in range(17):
            end = (step + 1) * 2000
            assert publish_live_settling(
                session,
                request=_request(session, recording_id, step, end_ms=end),
                words=_continuous(end),
            ).accepted
            session.commit()
            rows = _active(session, recording_id)
            assert len(rows) == (1 if end <= DEFAULT_MAX_SEGMENT_S * 1000 else 2)
            assert all(
                row.end_ms - row.start_ms <= DEFAULT_MAX_SEGMENT_S * 1000
                for row in rows
            )
            assert [word["word"] for row in rows for word in row_words(row)] == [
                f" word-{i}" for i in range(end // 500)
            ]
            cursor = _apply_delta(session, recording_id, cursor, client)
        assert rows[0].confidence_payload[SETTLING_KEY]["line_closed"]
        assert not rows[-1].confidence_payload[SETTLING_KEY]["line_closed"]


def test_worker_uses_merged_maximum_and_does_not_mistake_short_asr_for_silence(
    worker_db: Any, settling_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def decode(
        step: worker.SettlingStep,
        config: dict[str, Any],
        audio: Any,
        input_start_ms: int,
    ) -> tuple[dict[str, Any], str]:
        words = [
            word
            for word in _continuous(step.audio_end_ms)
            if word["end_ms"] > input_start_ms
        ]
        return {"asr_segments": [{"words": words}]}, "cuda"

    monkeypatch.setattr(worker, "_decode_words", decode)
    for sequence in range(12):
        assert worker.run_settling_step(
            worker_db.step(sequence), {"max_segment_s": 30}
        ).accepted
    with Session(settling_engine) as session:
        rows = _active(session, worker_db.recording_id)
        assert len(rows) == 1
        assert len(row_words(rows[0])) == 48
        assert not line_is_locked(rows[0])
    # The VAD still says speech, even if this successful decode ends early.
    step = worker_db.step(12)
    monkeypatch.setattr(
        worker,
        "_decode_words",
        lambda *_args: ({"asr_segments": [{"words": _continuous(24000)}]}, "cuda"),
    )
    assert worker.run_settling_step(step, {"max_segment_s": 30}).accepted
    with Session(settling_engine) as session:
        assert not _active(session, worker_db.recording_id)[0].confidence_payload[
            SETTLING_KEY
        ]["line_closed"]


def test_entire_ami_smoke_wording_is_preserved_across_rolling_publications(
    settling_engine: Engine,
) -> None:
    # AMI CC BY 4.0 line times; word times below are synthetic for replay only.
    fixture_path = Path(__file__).parent / "fixtures" / "settling-smoke-A.json"
    fixture_bytes = fixture_path.read_bytes()
    fixture = json.loads(fixture_bytes)
    all_words: list[dict[str, Any]] = []
    for line in fixture["utterances"]:
        tokens = re.findall(r"\s*\S+", line["text"])
        start, end = line["start_ms"], line["end_ms"]
        words = [
            {
                "word": token,
                "start_ms": start + (end - start) * i // len(tokens),
                "end_ms": start + (end - start) * (i + 1) // len(tokens),
            }
            for i, token in enumerate(tokens)
        ]
        assert reconstruct_text_from_words(words) == line["text"]
        all_words.extend(words)
    assert {line["speaker"] for line in fixture["utterances"]} == {"SPEAKER_00"}
    identities: dict[int, str] = {}
    client: dict[str, Any] = {}
    cursor = 0
    recording_id = _seed(settling_engine)
    with Session(settling_engine) as session:
        capture_end = all_words[-1]["end_ms"]
        for step, audio_end in enumerate(range(2000, capture_end + 2000, 2000)):
            observed = [word for word in all_words if word["end_ms"] <= audio_end]
            window = [
                word for word in observed if word["start_ms"] >= audio_end - 10000
            ]
            request = _request(session, recording_id, step, end_ms=audio_end)
            if window:
                result = publish_live_settling(
                    session,
                    request=request,
                    words=window,
                    decide_speaker=lambda _start, _end: LiveLabelDecision(
                        "SPEAKER_00", True, 1.0
                    ),
                )
            else:
                result = expire_live_settling(session, request=request)
            assert result.accepted
            session.commit()
            rows = _active(session, recording_id)
            actual = [word for row in rows for word in row_words(row)]
            assert [
                {key: word[key] for key in ("word", "start_ms", "end_ms")}
                for word in actual
            ] == observed
            assert len({word["id"] for word in actual}) == len(observed)
            for word in actual:
                assert word["id"] == identities.setdefault(word["start_ms"], word["id"])
            expected: list[list[dict[str, Any]]] = []
            for word in observed:
                if (
                    not expected
                    or word["start_ms"] - expected[-1][-1]["end_ms"] > 1000
                    or word["end_ms"] - expected[-1][0]["start_ms"]
                    > DEFAULT_MAX_SEGMENT_S * 1000
                ):
                    expected.append([])
                expected[-1].append(word)
            assert [[word["start_ms"] for word in row_words(row)] for row in rows] == [
                [word["start_ms"] for word in group] for group in expected
            ]
            cursor = _apply_delta(session, recording_id, cursor, client)
        assert close_live_settling(session, recording_id=recording_id)
        session.commit()
        assert all(line_is_locked(row) for row in _active(session, recording_id))
        _apply_delta(session, recording_id, cursor, client)
        assert " ".join(row.text for row in _active(session, recording_id)) == " ".join(
            line["text"] for line in fixture["utterances"]
        )
    assert fixture_path.read_bytes() == fixture_bytes


@pytest.mark.parametrize("diariser_veto", [False, True])
def test_worker_confirms_trailing_silence_before_locking_and_reopening_same_speaker(
    worker_db: Any,
    settling_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    diariser_veto: bool,
) -> None:
    spans: list[tuple[float, float]] = []

    def decide(_start: int, _end: int) -> LiveLabelDecision:
        return LiveLabelDecision("A", True, 1.0)

    evidence = SimpleNamespace(decide=decide, speech_spans=lambda: spans)
    monkeypatch.setattr(worker, "_speaker_evidence", lambda _step: evidence)
    decoded = _continuous(2000)
    monkeypatch.setattr(
        worker,
        "_decode_words",
        lambda *_args: ({"asr_segments": [{"words": decoded}]}, "cuda"),
    )
    for sequence in range(4):
        assert worker.run_settling_step(worker_db.step(sequence), {}).accepted
    client: dict[str, Any] = {}
    with Session(settling_engine) as session:
        row = _active(session, worker_db.recording_id)[0]
        public_id = row.public_id
        locked = copy.deepcopy(row_words(row))
        assert all(word["locked"] for word in locked)
        assert not line_is_locked(row)
        cursor = _apply_delta(session, worker_db.recording_id, 0, client)
    audio_end = 8000
    # Exactly one second stays open; a further millisecond closes it unless
    # the diariser heard later speech. Decode's 320 ms gate is independent.
    durations = [1000, 1, 1001]
    for sequence, duration in enumerate(durations, start=4):
        step = worker_db.step(sequence)
        sf.write(step.temp_dir / f"{sequence}.wav", [0.1] * (duration * 16), 16000)
        audio_end += duration
        step = replace(
            step, audio_end_ms=audio_end, combined_start_s=8.0, vad_regions=[]
        )
        if diariser_veto:
            spans[:] = [(8.0, min(audio_end, 9000) / 1000)]
        result = worker.run_settling_step(step, {})
        assert result.accepted
        closed = sequence == 6 or (sequence == 5 and not diariser_veto)
        with Session(settling_engine) as session:
            rows = _active(session, worker_db.recording_id)
            assert len(rows) == 1 and rows[0].public_id == public_id
            assert row_words(rows[0]) == locked
            assert line_is_locked(rows[0]) is closed
            assert rows[0].confidence_payload[SETTLING_KEY]["line_closed"] is closed
            cursor = _apply_delta(session, worker_db.recording_id, cursor, client)
            assert client[public_id]["locked"] is closed
        if closed:
            assert result.public_changed
            break
    step = worker_db.step(sequence + 1)
    step = replace(
        step,
        audio_end_ms=audio_end + 2000,
        combined_start_s=audio_end / 1000,
        vad_regions=[{"start": 0.0, "end": 2.0}],
    )
    decoded = [
        {"word": "After", "start_ms": audio_end + 100, "end_ms": audio_end + 500}
    ]
    assert worker.run_settling_step(step, {}).accepted
    with Session(settling_engine) as session:
        rows = _active(session, worker_db.recording_id)
        assert [row.speaker_label for row in rows] == ["A", "A"]
        assert rows[0].public_id == public_id and row_words(rows[0]) == locked
        assert line_is_locked(rows[0]) and not line_is_locked(rows[1])
        assert rows[1].text == "After" and rows[1].public_id != public_id
        _apply_delta(session, worker_db.recording_id, cursor, client)
