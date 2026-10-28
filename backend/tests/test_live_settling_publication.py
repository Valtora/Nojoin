"""Rolling publication, immutable frontiers and ordinary deltas on PostgreSQL."""

import copy
import threading
from collections.abc import Iterator
from dataclasses import replace

import numpy as np
import pytest
from sqlalchemy import Engine, create_engine, text
from sqlmodel import Session, select

from backend.core.db import driver_urls
from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceState
from backend.models.recording import RecordingStatus
from backend.models.transcript import Transcript
from backend.processing.live_diarization.lines import LabelTimeline
from backend.tests.postgres_setup import fresh_schema
from backend.tests.storage_hygiene_harness import add_recording
from backend.utils.canonical_pipeline import serialize_canonical_delta
from backend.utils.canonical_pipeline.live_labels import (
    LiveLabelDecision,
    apply_live_speaker_labels,
)
from backend.utils.canonical_pipeline.live_settling import (
    SettlingPass,
    close_live_settling,
    expire_live_settling,
    load_live_settling_words,
    publish_live_settling,
)
from backend.utils.canonical_pipeline.live_settling_projection import row_words
from backend.utils.config_manager import config_manager
from backend.utils.transcript_settling import (
    capture_publication_token,
    load_capture_publication,
)


@pytest.fixture
def settling_engine(
    postgres_test_url: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Engine]:
    engine = create_engine(
        driver_urls(postgres_test_url)[1],
        connect_args={"options": "-csearch_path=live_settling_test,public"},
    )
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    fresh_schema(engine, "live_settling_test")
    monkeypatch.setattr(
        config_manager,
        "get",
        lambda key, default=None: {"live_speaker_labels": "off"}.get(key, default),
    )
    yield engine
    engine.dispose()


def settle_with_k(monkeypatch: pytest.MonkeyPatch, k: int) -> None:
    """Set the agreement count each step reads (``current_settling_policy``)."""
    read = config_manager.get
    monkeypatch.setattr(
        config_manager,
        "get",
        lambda key, default=None: (
            k if key == "live_text_settling_k" else read(key, default)
        ),
    )


def _seed(engine: Engine) -> int:
    recording_id = add_recording(
        engine, "settling.wav", status=RecordingStatus.UPLOADING
    )
    with Session(engine) as session:
        session.add(
            Transcript(
                recording_id=recording_id,
                segments=[],
                settling_state={
                    "version": 1,
                    "eligible": True,
                    "generation": "episode-a",
                    "capture_epoch": 0,
                    "sealed": False,
                    "phase": "live",
                    "policy": {"enabled": True},
                },
            )
        )
        session.commit()
    return recording_id


def _request(
    session: Session, recording_id: int, step: int, *, end_ms: int = 2000
) -> SettlingPass:
    transcript, recording = load_capture_publication(session, recording_id, lock=False)
    return SettlingPass(
        recording_id,
        capture_publication_token(recording, transcript),
        f"step-{step}",
        step,
        end_ms - 10000,
        end_ms,
    )


def _words(texts: tuple[str, ...] = ("Hello", " world")) -> list[dict]:
    return [
        {"word": word, "start_ms": 100 + index * 450, "end_ms": 400 + index * 450}
        for index, word in enumerate(texts)
    ]


def _active(session: Session, recording_id: int) -> list[TranscriptUtterance]:
    return list(
        session.exec(
            select(TranscriptUtterance)
            .where(TranscriptUtterance.recording_id == recording_id)
            .where(TranscriptUtterance.state == TranscriptUtteranceState.PROVISIONAL)
            .order_by(TranscriptUtterance.start_ms)
        ).all()
    )


def test_four_observations_lock_words_then_later_machine_work_retains_the_line(
    settling_engine: Engine,
) -> None:
    recording_id = _seed(settling_engine)
    first_ids = None
    for step in range(4):
        with Session(settling_engine) as session:
            request = _request(session, recording_id, step)
            result = publish_live_settling(session, request=request, words=_words())
            assert result.accepted
            session.commit()
            active = _active(session, recording_id)
            words = [word for row in active for word in row_words(row)]
            ids = [word["id"] for word in words]
            first_ids = first_ids or ids
            assert ids == first_ids
            assert all(word["locked"] == (step == 3) for word in words)
            assert all(word["unchanged_count"] == step for word in words)
            transcript = session.get(
                Transcript,
                session.exec(
                    select(Transcript.id).where(Transcript.recording_id == recording_id)
                ).one(),
            )
            assert transcript.segments == []
    with Session(settling_engine) as session:
        frozen = copy.deepcopy(
            [
                (
                    row.public_id,
                    row.text,
                    row.start_ms,
                    row.end_ms,
                    row.confidence_payload,
                )
                for row in _active(session, recording_id)
            ]
        )
        result = publish_live_settling(
            session,
            request=_request(session, recording_id, 4),
            words=_words(("HELLO!", " world")),
        )
        assert result.accepted
        session.commit()
        assert [
            (row.public_id, row.text, row.start_ms, row.end_ms, row.confidence_payload)
            for row in _active(session, recording_id)
        ] == frozen


def test_speaker_projection_splits_overlap_per_word_and_delta_returns_tombstone(
    settling_engine: Engine,
) -> None:
    recording_id = _seed(settling_engine)
    provisional = lambda start, end: LiveLabelDecision("UNKNOWN", False, None)
    with Session(settling_engine) as session:
        publish_live_settling(
            session,
            request=_request(session, recording_id, 0),
            words=_words(),
            decide_speaker=provisional,
        )
        session.commit()
        revision, lines, _ = serialize_canonical_delta(session, recording_id)
        old_id = lines[0]["id"]
        old_row = _active(session, recording_id)[0]
        old_words = copy.deepcopy(row_words(old_row))
        expected_ids = [word["id"] for word in old_words]
        timeline = LabelTimeline(
            np.array([0.0, 0.4]),
            np.array([0.7, 1.0]),
            np.array([0, 1]),
            np.array([True, True]),
            2.0,
        )
        write = apply_live_speaker_labels(
            session,
            recording_id=recording_id,
            settled_ms=9000,
            decide=lambda start, end: timeline.label_line(start / 1000, end / 1000),
        )
        assert write.changed
        session.commit()
        _, changed, tombstones = serialize_canonical_delta(
            session, recording_id, after_revision=revision
        )
        assert tombstones == [old_id]
        assert [line["speaker"] for line in changed] == [
            "LIVE_PROVISIONAL_1",
            "LIVE_PROVISIONAL_2",
        ]
        assert [
            word["id"]
            for row in _active(session, recording_id)
            for word in row_words(row)
        ] == expected_ids
        session.refresh(old_row)
        assert old_row.state == TranscriptUtteranceState.SUPERSEDED
        assert row_words(old_row) == old_words


def test_duplicate_and_paused_results_make_no_public_change(
    settling_engine: Engine,
) -> None:
    recording_id = _seed(settling_engine)
    with Session(settling_engine) as session:
        request = _request(session, recording_id, 0)
        assert publish_live_settling(session, request=request, words=_words()).accepted
        session.commit()
        assert not publish_live_settling(
            session, request=request, words=_words()
        ).accepted
        session.commit()
        transcript, _ = load_capture_publication(session, recording_id)
        transcript.settling_state = {**transcript.settling_state, "phase": "paused"}
        session.add(transcript)
        session.commit()
        assert not publish_live_settling(
            session, request=_request(session, recording_id, 4), words=_words()
        ).accepted


def test_expiry_and_closure_lock_without_decode_and_snapshot_only_on_closure(
    settling_engine: Engine,
) -> None:
    recording_id = _seed(settling_engine)
    with Session(settling_engine) as session:
        publish_live_settling(
            session, request=_request(session, recording_id, 0), words=_words()
        )
        session.commit()
        result = expire_live_settling(
            session, request=_request(session, recording_id, 1, end_ms=12000)
        )
        assert result.accepted
        session.commit()
        words = [
            word for row in _active(session, recording_id) for word in row_words(row)
        ]
        assert all(word["lock_reason"] == "expiry" for word in words)
        assert all(word["unchanged_count"] == 0 for word in words)
        assert close_live_settling(session, recording_id=recording_id)
        session.commit()
        transcript, _ = load_capture_publication(session, recording_id, lock=False)
        assert transcript.text == "Hello world"
        assert len(transcript.segments) == 1


def test_order_does_not_restart_after_a_silent_gap(
    settling_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    settle_with_k(monkeypatch, 1)
    recording_id = _seed(settling_engine)
    with Session(settling_engine) as session:
        for step in range(2):
            publish_live_settling(
                session, request=_request(session, recording_id, step), words=_words()
            )
            session.commit()
        last_order = max(
            word["order"]
            for row in _active(session, recording_id)
            for word in row_words(row)
        )
        words = [{"word": "Again", "start_ms": 29000, "end_ms": 29500}]
        publish_live_settling(
            session,
            request=_request(session, recording_id, 2, end_ms=30000),
            words=words,
        )
        session.commit()
        current = load_live_settling_words(
            session, recording_id=recording_id, window_start_ms=20000
        )
        assert (
            next(word["order"] for word in current if word["word"] == "Again")
            > last_order
        )


def test_a_publisher_waiting_on_fence_rejects_the_step_it_lost(
    settling_engine: Engine,
) -> None:
    recording_id = _seed(settling_engine)
    outcome = []
    started = threading.Event()
    with Session(settling_engine) as first, Session(settling_engine) as snapshot:
        request = _request(snapshot, recording_id, 0)
        load_capture_publication(first, recording_id)

        def publish() -> None:
            with Session(settling_engine) as second:
                started.set()
                outcome.append(
                    publish_live_settling(
                        second, request=request, words=_words()
                    ).accepted
                )
                second.commit()

        thread = threading.Thread(target=publish)
        thread.start()
        assert started.wait(3)
        publish_live_settling(first, request=request, words=_words())
        first.commit()
        thread.join(5)
        assert not thread.is_alive()
    assert outcome == [False]


def test_private_comparisons_do_not_emit_deltas_but_lock_transition_does(
    settling_engine: Engine,
) -> None:
    recording_id = _seed(settling_engine)
    with Session(settling_engine) as session:
        publish_live_settling(
            session,
            # This test exercises a closed line's lock transition. Agreement
            # alone locks words; explicit trailing silence closes their line.
            request=replace(_request(session, recording_id, 0), silence_start_ms=850),
            words=_words(),
        )
        session.commit()
        revision, _, _ = serialize_canonical_delta(session, recording_id)
        for step in (1, 2):
            result = publish_live_settling(
                session, request=_request(session, recording_id, step), words=_words()
            )
            session.commit()
            assert result.changed == 0
            assert serialize_canonical_delta(
                session, recording_id, after_revision=revision
            ) == (revision, [], [])
        publish_live_settling(
            session, request=_request(session, recording_id, 3), words=_words()
        )
        session.commit()
        _, changed, _ = serialize_canonical_delta(
            session, recording_id, after_revision=revision
        )
        assert changed[0]["locked"]


def test_failed_alignment_retains_exact_untimed_atom_and_real_line_bounds(
    settling_engine: Engine,
) -> None:
    from dataclasses import replace

    recording_id = _seed(settling_engine)
    with Session(settling_engine) as session:
        request = replace(
            _request(session, recording_id, 0, end_ms=6000),
            input_start_ms=2000,
            metadata={"alignment_error": "aligner unavailable"},
        )
        words = [
            {"word": "Exact punctuation, stays!", "start_ms": None, "end_ms": None}
        ]
        assert publish_live_settling(session, request=request, words=words).accepted
        session.commit()
        row = _active(session, recording_id)[0]
        assert (row.start_ms, row.end_ms, row.text) == (
            2000,
            6000,
            "Exact punctuation, stays!",
        )
        assert row.confidence_payload["alignment_error"] == "aligner unavailable"
        assert row_words(row)[0]["start_ms"] is None
        assert close_live_settling(session, recording_id=recording_id)
        session.commit()
        assert (
            row_words(_active(session, recording_id)[0])[0]["lock_reason"] == "closure"
        )


def test_successful_timed_decode_clears_alignment_error_and_publishes_the_change(
    settling_engine: Engine,
) -> None:
    from dataclasses import replace

    recording_id = _seed(settling_engine)
    with Session(settling_engine) as session:
        request = replace(
            _request(session, recording_id, 0),
            metadata={"alignment_error": "alignment partially unavailable"},
        )
        publish_live_settling(session, request=request, words=_words())
        session.commit()
        revision, original, _ = serialize_canonical_delta(session, recording_id)
        result = publish_live_settling(
            session, request=_request(session, recording_id, 1), words=_words()
        )
        session.commit()
        assert result.changed == 1
        revision, changed, tombstones = serialize_canonical_delta(
            session, recording_id, after_revision=revision
        )
        assert not tombstones
        assert len(changed) == 1
        assert changed[0]["words"] == original[0]["words"]
        assert "alignment_error" not in changed[0]
        assert (
            "alignment_error"
            not in _active(session, recording_id)[0].confidence_payload
        )
        result = publish_live_settling(
            session, request=_request(session, recording_id, 2), words=_words()
        )
        session.commit()
        assert result.changed == 0
        assert serialize_canonical_delta(
            session, recording_id, after_revision=revision
        ) == (revision, [], [])


def test_resumed_cuda_does_not_adopt_or_duplicate_legacy_cpu_words(
    settling_engine: Engine,
) -> None:
    from backend.utils.canonical_pipeline.live_settling import legacy_live_frontier

    recording_id = _seed(settling_engine)
    with Session(settling_engine) as session:
        old = TranscriptUtterance(
            recording_id=recording_id,
            public_id="cpu-line",
            sort_key="000",
            start_ms=0,
            end_ms=3000,
            text="CPU wording",
            source_kind="live",
            state=TranscriptUtteranceState.PROVISIONAL,
            confidence_payload={
                "asr_segments": [
                    {"words": [{"word": "CPU wording", "start_ms": 0, "end_ms": 3000}]}
                ]
            },
        )
        session.add(old)
        session.commit()
        snapshot = copy.deepcopy(old.confidence_payload)
        assert legacy_live_frontier(session, recording_id=recording_id) == 3000
        words = [
            {"word": "CPU wording", "start_ms": 0, "end_ms": 3000},
            {"word": "CUDA tail", "start_ms": 3100, "end_ms": 3900},
        ]
        assert publish_live_settling(
            session,
            request=_request(session, recording_id, 0, end_ms=4000),
            words=words,
        ).accepted
        session.commit()
        assert [row.text for row in _active(session, recording_id)] == [
            "CPU wording",
            "CUDA tail",
        ]
        session.refresh(old)
        assert old.confidence_payload == snapshot
        assert "live_text_settling" not in old.confidence_payload


def test_word_source_aggregation_tracks_audio_energy_and_retains_locked_provenance(
    settling_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.processing.browser_live_audio import (
        source_channel_from_confidence_payload,
    )
    from backend.utils.canonical_pipeline.live_settling_sources import (
        partition_source_metadata,
    )

    microphone = {"channel_count": 2, "sample_count": 100, "channel_rms": [1, 0]}
    system = {"channel_count": 2, "sample_count": 100, "channel_rms": [0, 1]}
    mixed = partition_source_metadata(
        [{"source_channel_activity": microphone}, {"source_channel_activity": system}]
    )
    assert mixed["source_channel_activity"]["source_overlap"]
    assert source_channel_from_confidence_payload(mixed) is None
    settle_with_k(monkeypatch, 1)
    recording_id = _seed(settling_engine)
    with Session(settling_engine) as session:
        words = [{**word, "source_channel_activity": microphone} for word in _words()]
        for step in range(2):
            publish_live_settling(
                session, request=_request(session, recording_id, step), words=words
            )
            session.commit()
        frozen = copy.deepcopy(_active(session, recording_id)[0].confidence_payload)
        changed = [{**word, "source_channel_activity": system} for word in words]
        publish_live_settling(
            session, request=_request(session, recording_id, 2), words=changed
        )
        session.commit()
        assert _active(session, recording_id)[0].confidence_payload == frozen


def test_speaker_lock_rechecks_mutable_timing_but_retains_immutable_word_speaker() -> (
    None
):
    from backend.utils.canonical_pipeline.live_settling_projection import word_decisions

    original = {
        "id": "word-a",
        "word": "yes",
        "start_ms": 100,
        "end_ms": 400,
        "locked": False,
    }
    row = TranscriptUtterance(
        recording_id=1,
        sort_key="000",
        start_ms=100,
        end_ms=400,
        text="yes",
        speaker_label="LIVE_PROVISIONAL_1",
        confidence_payload={
            "live_speaker_label": {"locked": True},
            "asr_segments": [{"words": [original]}],
        },
    )
    decide = lambda start, end: LiveLabelDecision("LIVE_PROVISIONAL_2", True, 0.9)
    changed = {**original, "start_ms": 600, "end_ms": 900}
    assert (
        word_decisions([changed], [row], decide, speaker_enabled=True)[0].speaker
        == "LIVE_PROVISIONAL_2"
    )
    row.confidence_payload["asr_segments"][0]["words"][0]["locked"] = True
    immutable = {**original, "locked": True}
    assert (
        word_decisions([immutable], [row], decide, speaker_enabled=True)[0].speaker
        == "LIVE_PROVISIONAL_1"
    )
