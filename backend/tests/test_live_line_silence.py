"""Long line pauses retain only uninterrupted, accepted capture silence."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import torch
from sqlalchemy import Engine
from sqlmodel import Session

from backend.core import db
from backend.processing import live_settling as worker
from backend.processing import transcribe
from backend.tests.test_live_settling_publication import (
    _active,
    _request,
    _seed,
    _words,
)
from backend.tests.test_live_settling_publication import (
    settling_engine as settling_engine,
)
from backend.tests.test_live_settling_worker import _step
from backend.tests.test_live_settling_worker_postgres import worker_db as worker_db
from backend.utils.canonical_pipeline import live_settling as publication
from backend.utils.transcript_settling import (
    resolve_capture_settling_policy,
    update_capture_settling_state,
)


@pytest.fixture
def silence_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    state: dict[str, Any] = {
        "eligible": True,
        "generation": "episode",
        "phase": "live",
        "capture_epoch": 1,
        "policy": {"enabled": True, "window_s": 10, "k": 3},
    }
    transcript = SimpleNamespace(settling_state=state)
    recording = SimpleNamespace(transcript=transcript)
    session = SimpleNamespace(
        get=lambda *_args: recording,
        execute=None,
        commit=lambda: None,
        close=lambda: None,
    )
    monkeypatch.setattr(db, "get_sync_session", lambda: session)
    monkeypatch.setattr(worker, "publication_is_current", lambda *_args: True)
    monkeypatch.setattr(
        worker, "load_capture_publication", lambda *_args: (transcript, recording)
    )
    monkeypatch.setattr(
        publication, "load_live_settling_words", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        publication, "live_settling_frontier", lambda *_args, **_kwargs: 0
    )
    monkeypatch.setattr(
        publication, "legacy_live_frontier", lambda *_args, **_kwargs: 0
    )
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(
        transcribe, "loaded_transcription_device", lambda _config: "cuda"
    )
    monkeypatch.setattr(worker, "live_speaker_labels_requested", lambda: False)
    spans: list[tuple[float, float]] = []
    evidence = SimpleNamespace(speech_spans=lambda: spans, decide=None)
    monkeypatch.setattr(worker, "_speaker_evidence", lambda _step: evidence)
    monkeypatch.setattr(
        worker, "_decode_words", lambda *_args: ({"asr_segments": []}, "cuda")
    )
    requests: list[publication.SettlingPass] = []

    def accept(
        _session: Any, *, request: publication.SettlingPass, **_kwargs: Any
    ) -> Any:
        requests.append(request)
        # Model the canonical publication boundary; separate PG tests exercise
        # its actual state write rather than this worker seam.
        transcript.settling_state.update(
            trailing_silence_start_ms=request.silence_start_ms,
            silence_audio_end_ms=request.audio_end_ms,
        )
        return publication.SettlingPublicationResult(True)

    monkeypatch.setattr(publication, "publish_live_settling", accept)
    monkeypatch.setattr(publication, "expire_live_settling", accept)

    def run(
        sequence: int, *, speech_end_s: float | None = None, gap_ms: int = 0
    ) -> None:
        step = replace(
            _step(tmp_path, sequence=sequence),
            combined_start_s=sequence * 2.0 + gap_ms / 1000,
            audio_end_ms=(sequence + 1) * 2000 + gap_ms,
            vad_regions=[]
            if speech_end_s is None
            else [{"start": 0.0, "end": speech_end_s}],
        )
        assert worker.run_settling_step(step, {"line_break_pause_s": 5.0}).accepted

    return SimpleNamespace(
        run=run, requests=requests, transcript=transcript, spans=spans
    )


def test_five_second_pause_closes_after_multiple_two_second_chunks(
    silence_worker: Any,
) -> None:
    silence_worker.run(0, speech_end_s=1.0)
    for sequence in range(1, 4):
        silence_worker.run(sequence)
    assert [request.silence_start_ms for request in silence_worker.requests] == [
        1000
    ] * 4
    request = silence_worker.requests[-1]
    assert publication._confirmed_silence(request, {"line_break_pause_s": 5.0}) == 1000


def test_new_vad_speech_resets_accumulated_silence(silence_worker: Any) -> None:
    silence_worker.run(0, speech_end_s=1.0)
    silence_worker.run(1)
    silence_worker.run(2, speech_end_s=1.0)
    silence_worker.run(3)
    assert [request.silence_start_ms for request in silence_worker.requests] == [
        1000,
        1000,
        5000,
        5000,
    ]


def test_delayed_diariser_speech_vetoes_earlier_silence(silence_worker: Any) -> None:
    silence_worker.run(0, speech_end_s=1.0)
    silence_worker.run(1)
    silence_worker.spans[:] = [(3.0, 3.5)]
    silence_worker.run(2)
    silence_worker.run(3)
    assert [request.silence_start_ms for request in silence_worker.requests] == [
        1000,
        1000,
        3500,
        3500,
    ]
    assert (
        publication._confirmed_silence(
            silence_worker.requests[-1], {"line_break_pause_s": 5.0}
        )
        is None
    )


def test_gap_in_capture_coverage_discards_prior_silence(silence_worker: Any) -> None:
    silence_worker.run(0, speech_end_s=1.0)
    silence_worker.run(1)
    silence_worker.run(2, gap_ms=1000)
    assert silence_worker.requests[-1].silence_start_ms == 5000


def test_resume_generation_discards_prior_silence(silence_worker: Any) -> None:
    silence_worker.run(0, speech_end_s=1.0)
    transcript = silence_worker.transcript
    transcript.settling_state["phase"] = "paused"
    update_capture_settling_state(
        transcript, capture_epoch=2, sealed=False, phase="live"
    )
    assert "trailing_silence_start_ms" not in transcript.settling_state
    assert "silence_audio_end_ms" not in transcript.settling_state


def test_cpu_episode_discards_prior_silence(silence_worker: Any) -> None:
    silence_worker.run(0, speech_end_s=1.0)
    transcript = silence_worker.transcript
    resolve_capture_settling_policy(transcript, device="cpu", disable_on_cpu=True)
    assert transcript.settling_state["policy"]["enabled"] is False
    assert "trailing_silence_start_ms" not in transcript.settling_state
    assert "silence_audio_end_ms" not in transcript.settling_state


def test_canonical_publication_persists_silence_before_it_can_close_a_line(
    settling_engine: Engine,
) -> None:
    recording_id = _seed(settling_engine, k=1)
    with Session(settling_engine) as session:
        for step in range(2):
            request = replace(
                _request(session, recording_id, step, end_ms=(step + 1) * 2000),
                silence_start_ms=850,
                line_break_pause_s=5.0,
            )
            assert publication.publish_live_settling(
                session, request=request, words=_words()
            ).accepted
            session.commit()
            transcript, _ = publication.load_capture_publication(session, recording_id)
            assert transcript.settling_state["trailing_silence_start_ms"] == 850
            assert (
                transcript.settling_state["silence_audio_end_ms"]
                == request.audio_end_ms
            )
        for step, end_ms in enumerate((5850, 5851), start=2):
            request = replace(
                _request(session, recording_id, step, end_ms=end_ms),
                silence_start_ms=850,
                line_break_pause_s=5.0,
            )
            assert publication.expire_live_settling(session, request=request).accepted
            session.commit()
            transcript, _ = publication.load_capture_publication(session, recording_id)
            assert transcript.settling_state["trailing_silence_start_ms"] == 850
            assert transcript.settling_state["silence_audio_end_ms"] == end_ms
            assert _active(session, recording_id)[0].confidence_payload[
                publication.SETTLING_KEY
            ]["line_locked"] is (end_ms > 5850)
        before = dict(transcript.settling_state)
        stale = replace(request, silence_start_ms=0, audio_end_ms=8000)
        assert not publication.expire_live_settling(session, request=stale).accepted
        assert transcript.settling_state == before


@pytest.mark.parametrize("untimed", [False, True], ids=["timed", "untimed"])
def test_worker_closes_five_second_pause_across_real_canonical_publications(
    worker_db: Any,
    settling_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    untimed: bool,
) -> None:
    if untimed:
        monkeypatch.setattr(
            worker,
            "_decode_words",
            lambda *_args: (
                {"asr_segments": [{"words": [{"word": "untimed"}]}]},
                "cuda",
            ),
        )
    first = replace(worker_db.step(0), vad_regions=[{"start": 0.0, "end": 1.0}])
    assert worker.run_settling_step(first, {"line_break_pause_s": 5.0}).accepted
    monkeypatch.setattr(
        worker, "_decode_words", lambda *_args: pytest.fail("Silent chunk decoded")
    )
    original_ids: list[str] | None = None
    for sequence in range(1, 6):
        step = replace(
            worker_db.step(sequence), combined_start_s=sequence * 2.0, vad_regions=[]
        )
        assert worker.run_settling_step(step, {"line_break_pause_s": 5.0}).accepted
        with Session(settling_engine) as session:
            transcript, _ = publication.load_capture_publication(
                session, worker_db.recording_id
            )
            assert (
                transcript.settling_state["silence_audio_end_ms"] == step.audio_end_ms
            )
            rows = _active(session, worker_db.recording_id)
            assert len(rows) == 1
            words = publication.row_words(rows[0])
            ids = [word["id"] for word in words]
            original_ids = original_ids or ids
            assert ids == original_ids
            state = rows[0].confidence_payload[publication.SETTLING_KEY]
            assert state["line_closed"] is (sequence >= 3)
            assert state["line_locked"] is (sequence == 5)
            assert all(word["locked"] for word in words) is (sequence == 5)
            if sequence >= 3:
                assert transcript.settling_state["trailing_silence_start_ms"] == (
                    2000 if untimed else 1000
                )
