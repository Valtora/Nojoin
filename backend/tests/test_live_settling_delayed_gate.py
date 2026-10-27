"""Delayed speech gates through the real episode fence and rolling adapter."""

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
import torch
from sqlalchemy import Engine
from sqlmodel import Session, select

from backend.models.transcript import Transcript
from backend.processing import live_settling as worker
from backend.processing.engines.errors import TranscriptionError
from backend.tests.test_live_settling_publication import _active
from backend.tests.test_live_settling_publication import (
    settling_engine as settling_engine,
)
from backend.tests.test_live_settling_worker_postgres import worker_db as worker_db
from backend.utils.canonical_pipeline.live_settling_projection import row_words
from backend.utils.transcript_publication import bump_transcript_change
from backend.utils.transcript_settling import (
    CapturePublicationToken,
    load_capture_publication,
)


def _state(engine: Engine, recording_id: int) -> dict[str, Any]:
    with Session(engine) as session:
        transcript = session.exec(
            select(Transcript).where(Transcript.recording_id == recording_id)
        ).one()
        return dict(transcript.settling_state)


def _evidence(
    monkeypatch: pytest.MonkeyPatch, spans: list[tuple[float, float]]
) -> None:
    monkeypatch.setattr(
        worker,
        "_speaker_evidence",
        lambda _: SimpleNamespace(speech_spans=lambda: spans, decide=None),
    )


@pytest.mark.parametrize("cold", [False, True])
@pytest.mark.parametrize("current_vad", [False, True])
def test_delayed_arrival_decodes_once_and_repeated_evidence_stays_consumed(
    worker_db: Any,
    settling_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    cold: bool,
    current_vad: bool,
) -> None:
    if cold:
        with Session(settling_engine) as session:
            transcript = session.exec(select(Transcript)).one()
            transcript.settling_state = {
                **transcript.settling_state,
                "policy": {"enabled": None, "window_s": 10, "k": 3},
            }
            session.add(transcript)
            session.commit()
    first = replace(worker_db.step(0), vad_regions=[])
    with Session(settling_engine) as session:
        initial_revision = session.exec(select(Transcript)).one().change_sequence
    worker.run_settling_step(first, {})
    with Session(settling_engine) as session:
        transcript = session.exec(select(Transcript)).one()
        assert transcript.change_sequence == initial_revision
    assert _state(settling_engine, worker_db.recording_id)["policy"]["enabled"] is (
        None if cold else True
    )
    clips: list[tuple[int, int]] = []

    def decode(step: worker.SettlingStep, _config: Any, audio: Any, start: int) -> Any:
        clips.append((step.sequence, start))
        assert audio.numel() == 64000
        return {
            "asr_segments": [
                {"words": [{"word": "quiet", "start_ms": 500, "end_ms": 900}]}
            ]
        }, "cuda"

    monkeypatch.setattr(worker, "_decode_words", decode)
    _evidence(monkeypatch, [(0.5, 0.9)])
    second = worker_db.step(1)
    if not current_vad:
        second = replace(second, vad_regions=[])
    worker.run_settling_step(second, {"context_window_s": 0})
    worker.run_settling_step(second, {})
    worker.run_settling_step(replace(worker_db.step(2), vad_regions=[]), {})
    assert clips == [(1, 0)]
    state = _state(settling_engine, worker_db.recording_id)
    assert state["gate_chunks"][0]["consumed"]
    with Session(settling_engine) as session:
        words = [
            word
            for row in _active(session, worker_db.recording_id)
            for word in row_words(row)
        ]
        assert words[0]["unchanged_count"] == 0


def test_failed_delayed_arrival_is_spent_without_counting_a_comparison(
    worker_db: Any, settling_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker.run_settling_step(worker_db.step(0), {})
    worker.run_settling_step(replace(worker_db.step(1), vad_regions=[]), {})
    _evidence(monkeypatch, [(2.2, 2.8)])
    calls: list[int] = []

    def fail(step: worker.SettlingStep, *_args: Any) -> Any:
        calls.append(step.sequence)
        raise TranscriptionError("failed", engine="whisper")

    monkeypatch.setattr(worker, "_decode_words", fail)
    failed = replace(worker_db.step(2), vad_regions=[])
    worker.run_settling_step(failed, {})
    worker.run_settling_step(failed, {})
    worker.run_settling_step(replace(worker_db.step(3), vad_regions=[]), {})
    assert calls == [2]
    assert _state(settling_engine, worker_db.recording_id)["gate_chunks"][1]["consumed"]
    with Session(settling_engine) as session:
        words = [
            word
            for row in _active(session, worker_db.recording_id)
            for word in row_words(row)
        ]
        assert words[0]["unchanged_count"] == 0


@pytest.mark.parametrize("reason", ["expired", "muted", "locked", "already_decoded"])
def test_ineligible_old_speech_does_not_open_gate(
    worker_db: Any,
    settling_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    reason: str,
) -> None:
    first = worker_db.step(0)
    if reason != "already_decoded":
        first = replace(first, vad_regions=[])
    worker.run_settling_step(first, {})
    if reason == "locked":
        monkeypatch.setattr(
            worker,
            "_episode_snapshot",
            lambda _: worker.EpisodeSnapshot(
                {"enabled": True, "window_s": 10, "k": 3},
                [],
                0,
                frontier_ms=2000,
                gate_chunks=_state(settling_engine, worker_db.recording_id)[
                    "gate_chunks"
                ],
            ),
        )
    if reason == "muted":
        monkeypatch.setattr(
            worker, "rolling_audio_tail", lambda *_: (torch.zeros(64000), 2)
        )
    final_sequence = 6 if reason == "expired" else 1
    for sequence in range(1, final_sequence):
        worker.run_settling_step(replace(worker_db.step(sequence), vad_regions=[]), {})
    _evidence(monkeypatch, [(0.5, 0.9)])
    monkeypatch.setattr(
        worker, "_decode_words", lambda *_: pytest.fail("ineligible decode")
    )
    worker.run_settling_step(
        replace(worker_db.step(final_sequence), vad_regions=[]), {}
    )
    if reason == "expired":
        state = _state(settling_engine, worker_db.recording_id)
        assert len(state["gate_chunks"]) == 5
        assert all(chunk["end_ms"] > 4000 for chunk in state["gate_chunks"])


def test_stale_token_after_inference_does_not_consume_delayed_arrival(
    worker_db: Any, settling_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker.run_settling_step(replace(worker_db.step(0), vad_regions=[]), {})
    _evidence(monkeypatch, [(0.5, 0.9)])

    def decode(*_args: Any) -> Any:
        with Session(settling_engine) as session:
            transcript = session.exec(select(Transcript)).one()
            transcript.settling_state = {
                **transcript.settling_state,
                "generation": "new",
            }
            session.add(transcript)
            session.commit()
        return {"asr_segments": []}, "cuda"

    monkeypatch.setattr(worker, "_decode_words", decode)
    result = worker.run_settling_step(replace(worker_db.step(1), vad_regions=[]), {})
    assert not result.accepted
    state = _state(settling_engine, worker_db.recording_id)
    assert not state["gate_chunks"][0]["consumed"]
    assert len(state["gate_chunks"]) == 1
    stale = replace(
        worker_db.step(2), expected_token=CapturePublicationToken(0, "episode-a")
    )
    assert not worker.run_settling_step(stale, {}).accepted


def test_public_edit_rejects_hypothesis_but_spends_delayed_arrival(
    worker_db: Any, settling_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker.run_settling_step(replace(worker_db.step(0), vad_regions=[]), {})
    _evidence(monkeypatch, [(0.5, 0.9)])
    calls: list[int] = []

    def decode(step: worker.SettlingStep, *_args: Any) -> Any:
        calls.append(step.sequence)
        with Session(settling_engine) as session:
            load_capture_publication(session, worker_db.recording_id)
            bump_transcript_change(session, worker_db.recording_id)
            session.commit()
        return {
            "asr_segments": [
                {"words": [{"word": "rejected", "start_ms": 500, "end_ms": 900}]}
            ]
        }, "cuda"

    monkeypatch.setattr(worker, "_decode_words", decode)
    assert worker.run_settling_step(
        replace(worker_db.step(1), vad_regions=[]), {}
    ).accepted
    assert worker.run_settling_step(
        replace(worker_db.step(2), vad_regions=[]), {}
    ).accepted
    assert calls == [1]
    state = _state(settling_engine, worker_db.recording_id)
    assert state["gate_chunks"][0]["consumed"]
    assert "last_decode_id" not in state
    with Session(settling_engine) as session:
        assert _active(session, worker_db.recording_id) == []
