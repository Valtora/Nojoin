"""Rolling worker cadence, gate and audio contracts without inference services."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import soundfile as sf
import torch

from backend.processing import live_settling as worker
from backend.processing import transcribe
from backend.processing.engines.errors import TranscriptionError
from backend.processing.live_settling_audio import chunk_has_speech, rolling_audio_tail
from backend.utils.transcript_settling import CapturePublicationToken


def _step(tmp_path: Path, *, sequence: int = 6) -> worker.SettlingStep:
    live_dir = tmp_path / "live"
    live_dir.mkdir(exist_ok=True)
    for index in range(sequence + 1):
        sf.write(
            tmp_path / f"{index}.wav", np.ones((32000, 2), np.float32) * 0.1, 16000
        )
    return worker.SettlingStep(
        42,
        sequence,
        tmp_path,
        live_dir,
        (sequence + 1) * 2000,
        0,
        [{"start": sequence * 2, "end": (sequence + 1) * 2}],
        CapturePublicationToken(1, "episode"),
    )


@pytest.fixture
def rolling_lane(monkeypatch: pytest.MonkeyPatch) -> Any:
    observations: list[dict[str, Any]] = []
    snapshot = worker.EpisodeSnapshot({"enabled": True, "window_s": 10, "k": 3}, [])
    monkeypatch.setattr(worker, "_episode_snapshot", lambda _step: snapshot)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(
        transcribe, "loaded_transcription_device", lambda _config: "cuda"
    )
    monkeypatch.setattr(worker, "_speaker_evidence", lambda _step: None)

    def publish(step: Any, _snapshot: Any, payload: Any, _evidence: Any) -> Any:
        observations.append({"step": step.sequence, "payload": payload})
        return worker.SettlingStepResult(True)

    original_publisher = worker._publish_step
    monkeypatch.setattr(worker, "_publish_step", publish)
    return SimpleNamespace(
        observations=observations, snapshot=snapshot, publisher=original_publisher
    )


@pytest.mark.parametrize(
    "vad,diariser,expected",
    [
        (True, False, True),
        (False, True, True),
        (True, True, True),
        (False, False, False),
    ],
)
@pytest.mark.parametrize("muted", [False, True])
def test_speech_or_gate_uses_unmuted_new_chunk_only(
    vad: bool, diariser: bool, expected: bool, muted: bool
) -> None:
    audio = torch.zeros(64000)
    if not muted:
        audio[32000:] = 0.1
    assert chunk_has_speech(
        audio=audio,
        audio_start_s=0,
        chunk_bounds_s=(2, 4),
        vad_spans=[(2.1, 3)] if vad else [],
        diariser_spans=[(2.2, 3.5)] if diariser else [],
    ) is (expected and not muted)
    assert not chunk_has_speech(
        audio=audio,
        audio_start_s=0,
        chunk_bounds_s=(2, 4),
        vad_spans=[(0, 1)],
        diariser_spans=[(0, 1)],
    )


def test_rolling_tail_caps_total_input_and_stops_at_missing_audio(
    tmp_path: Path,
) -> None:
    _step(tmp_path)
    tail, duration = rolling_audio_tail(tmp_path, 6, 10)
    assert tail.numel() == 160000
    assert duration == 2
    (tmp_path / "4.wav").unlink()
    tail, _ = rolling_audio_tail(tmp_path, 6, 10)
    assert tail.numel() == 64000


def test_only_latest_step_decodes_with_captured_window_and_absolute_times(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rolling_lane: Any
) -> None:
    step = _step(tmp_path)
    clips: list[int] = []

    def decode(_step: Any, _config: Any, audio: Any, start: int) -> Any:
        clips.append(audio.numel())
        assert start == 4000
        return {
            "asr_segments": [
                {
                    "words": [
                        {
                            "word": "hello",
                            "start_ms": start + 100,
                            "end_ms": start + 500,
                        }
                    ]
                }
            ]
        }, "cuda"

    monkeypatch.setattr(worker, "_decode_words", decode)
    result = worker.run_settling_step(
        step, {"processing_device": "auto", "context_window_s": 5}
    )
    assert result.accepted and result.handled
    assert clips == [160000]
    assert [item["step"] for item in rolling_lane.observations] == [6]
    assert (
        rolling_lane.observations[0]["payload"]["asr_segments"][0]["words"][0][
            "start_ms"
        ]
        == 4100
    )


def test_lead_up_is_configurable_and_inside_total_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rolling_lane: Any
) -> None:
    step = _step(tmp_path)
    rolling_lane.snapshot.words.append(
        {"word": "tail", "start_ms": 12000, "end_ms": 12500, "locked": False}
    )
    inputs: list[tuple[int, int]] = []

    def decode(_step: Any, _config: Any, audio: Any, start: int) -> Any:
        inputs.append((start, audio.numel()))
        return {"asr_segments": []}, "cuda"

    monkeypatch.setattr(worker, "_decode_words", decode)
    worker.run_settling_step(step, {"context_window_s": 2})
    assert inputs == [(10000, 64000)]


def test_delayed_gate_includes_missed_gap_before_existing_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rolling_lane: Any
) -> None:
    step = replace(_step(tmp_path), vad_regions=[])
    rolling_lane.snapshot.words.append(
        {"word": "tail", "start_ms": 12000, "end_ms": 12500, "locked": False}
    )
    rolling_lane.snapshot.gate_chunks.append(
        {
            "sequence": 3,
            "start_ms": 6000,
            "end_ms": 8000,
            "missed": True,
            "consumed": False,
        }
    )
    monkeypatch.setattr(
        worker,
        "_speaker_evidence",
        lambda _: SimpleNamespace(
            speech_spans=lambda: [(6.5, 7.0)],
            decide=None,
        ),
    )
    inputs: list[tuple[int, int]] = []

    def decode(_step: Any, _config: Any, audio: Any, start: int) -> Any:
        inputs.append((start, audio.numel()))
        return {"asr_segments": []}, "cuda"

    monkeypatch.setattr(worker, "_decode_words", decode)
    worker.run_settling_step(step, {"context_window_s": 0})
    assert inputs == [(6000, 128000)]


def test_silent_step_expires_without_decode_or_new_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rolling_lane: Any
) -> None:
    step = _step(tmp_path)
    step.vad_regions.clear()
    monkeypatch.setattr(
        worker, "_decode_words", lambda *_: pytest.fail("silent decode")
    )
    assert worker.run_settling_step(step, {}).accepted
    assert rolling_lane.observations == [{"step": 6, "payload": None}]


def test_silent_cold_step_persists_gate_history_without_resolving_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rolling_lane: Any
) -> None:
    step = _step(tmp_path)
    step.vad_regions.clear()
    rolling_lane.snapshot.policy["enabled"] = None
    assert worker.run_settling_step(step, {}).accepted
    assert rolling_lane.observations == [{"step": 6, "payload": None}]
    assert rolling_lane.snapshot.policy["enabled"] is None


@pytest.mark.parametrize("enabled", [None, True])
def test_failed_decode_adds_no_observation_and_only_resolved_policy_expires(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rolling_lane: Any,
    enabled: bool | None,
) -> None:
    def fail(*_args: Any) -> Any:
        raise TranscriptionError("decode failed", engine="whisper")

    monkeypatch.setattr(worker, "_decode_words", fail)
    rolling_lane.snapshot.policy["enabled"] = enabled
    result = worker.run_settling_step(_step(tmp_path), {})
    assert result.handled and result.accepted
    assert rolling_lane.observations == [{"step": 6, "payload": None}]


def test_failed_speech_decode_locks_expired_words_as_is_without_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rolling_lane: Any
) -> None:
    from backend.utils.live_word_settling import expire_word_state

    rolling_lane.snapshot.words.extend(
        [
            {
                "id": "expired-word",
                "word": "As-is,",
                "start_ms": 1000,
                "end_ms": 1400,
                "locked": False,
                "unchanged_count": 1,
                "last_decode_id": "successful",
            },
            {
                "id": "tail-word",
                "word": "tail",
                "start_ms": 12000,
                "end_ms": 12400,
                "locked": False,
                "unchanged_count": 2,
                "last_decode_id": "successful",
            },
        ]
    )
    calls: list[str] = []

    def fail(*_args: Any) -> Any:
        calls.append("decode")
        raise TranscriptionError("decode failed", engine="whisper")

    def expire(step: Any, snapshot: Any, payload: Any, _evidence: Any) -> Any:
        assert payload is None
        calls.append("expiry")
        snapshot.words[:] = expire_word_state(
            snapshot.words, window_start_ms=step.audio_end_ms - 10000
        )
        return worker.SettlingStepResult(True)

    monkeypatch.setattr(worker, "_decode_words", fail)
    monkeypatch.setattr(worker, "_publish_step", expire)
    assert worker.run_settling_step(_step(tmp_path), {}).accepted
    first, tail = rolling_lane.snapshot.words
    assert first == {
        "id": "expired-word",
        "word": "As-is,",
        "start_ms": 1000,
        "end_ms": 1400,
        "locked": True,
        "lock_reason": "expiry",
        "unchanged_count": 1,
        "last_decode_id": "successful",
    }
    assert not tail["locked"] and tail["unchanged_count"] == 2
    assert calls == ["decode", "expiry"]


@pytest.mark.parametrize("cpu", ["explicit", "placement", "fallback", "unavailable"])
def test_cpu_and_actual_fallback_disable_repetition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rolling_lane: Any, cpu: str
) -> None:
    disabled: list[int] = []
    monkeypatch.setattr(
        worker, "_disable_cpu_episode", lambda step: disabled.append(step.sequence)
    )
    monkeypatch.setattr(
        transcribe,
        "loaded_transcription_device",
        lambda _: "cpu" if cpu == "placement" else "cuda",
    )
    monkeypatch.setattr(torch.cuda, "is_available", lambda: cpu != "unavailable")
    monkeypatch.setattr(
        worker, "_decode_words", lambda *_: ({"asr_segments": []}, "cpu")
    )
    result = worker.run_settling_step(
        _step(tmp_path), {"processing_device": "cpu" if cpu == "explicit" else "auto"}
    )
    assert not result.handled
    assert disabled == [6]
    assert not rolling_lane.observations


@pytest.mark.parametrize("backend", ["whisper", "parakeet", "canary"])
@pytest.mark.parametrize("alignment_failure", [False, True])
def test_engine_dispatch_preserves_absolute_word_times_and_alignment_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    alignment_failure: bool,
) -> None:
    from backend.processing import forced_alignment, vad

    step = replace(_step(tmp_path, sequence=0), audio_end_ms=8000)
    source = {
        "text": "Hello, world!",
        "segments": [{"start": 0.0, "end": 2.0, "text": "Hello, world!"}],
    }
    timed = [
        {"word": "Hello,", "start": 0.2, "end": 0.8},
        {"word": "world!", "start": 1.1, "end": 1.8},
    ]
    calls: list[float] = []

    def infer(path: str, _config: Any) -> Any:
        calls.append(sf.info(path).duration)
        if backend == "canary":
            return source
        return {**source, "segments": [{**source["segments"][0], "words": timed}]}

    def align(result: Any, path: str, _config: Any) -> Any:
        assert sf.info(path).duration == 2
        segment = result["segments"][0]
        return {
            **result,
            "segments": [
                {
                    **segment,
                    **(
                        {"alignment_error": "unavailable"}
                        if alignment_failure
                        else {"words": timed}
                    ),
                }
            ],
        }

    engine = SimpleNamespace(transcribe=infer, loaded_device=lambda _: "cuda")
    monkeypatch.setattr(transcribe, "_ENGINE_REGISTRY", {backend: engine})
    monkeypatch.setattr(forced_alignment, "align_transcription", align)
    monkeypatch.setattr(
        vad.silero_vad,
        "save_audio",
        lambda path, audio, sampling_rate: sf.write(
            path, audio.squeeze(0).numpy(), sampling_rate
        ),
    )
    payload, device = worker._decode_words(
        step, {"transcription_backend": backend}, torch.ones(32000) * 0.1, 6000
    )
    words = payload["asr_segments"][0]["words"]
    assert calls == [2]
    assert device == "cuda"
    assert payload["source_channel_activity"]["channel_count"] == 2
    assert "source_channel_evidence" in payload
    if backend == "canary" and alignment_failure:
        assert payload["alignment_error"] == "unavailable"
        assert words[0]["word"] == "Hello, world!"
        assert words[0]["start_ms"] is None and words[0]["end_ms"] is None
    else:
        assert [word["start_ms"] for word in words] == [6200, 7100]
        assert [word["end_ms"] for word in words] == [6800, 7800]
    assert not list(step.live_dir.glob("settling-*.wav"))


@pytest.mark.parametrize("failure", [False, True])
def test_task_drops_backlog_comparisons_and_feeds_labels_after_asr_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rolling_lane: Any, failure: bool
) -> None:
    from backend.models.recording import RecordingStatus
    from backend.processing import live_transcribe
    from backend.processing.live_diarization import step as label_step
    from backend.tests.test_live_transcription import (
        _FakeRecording,
        _FakeSession,
        _make_segment_wav,
        _patch_live_deps,
        _run_live_task,
    )

    audio_store = _patch_live_deps(
        monkeypatch,
        speech_map=lambda audio: [{"start": 0.0, "end": audio.numel() / 16000}],
    )
    for sequence in range(4):
        path = _make_segment_wav(tmp_path, sequence, 2, audio_store)
        # The older helper writes silent fixtures; a speech gate needs actual PCM.
        audio_store.save_audio(str(path), torch.ones(32000) * 0.1)
    monkeypatch.setattr(
        live_transcribe, "recording_upload_temp_dir", lambda *_args, **_kwargs: tmp_path
    )
    monkeypatch.setattr(
        live_transcribe, "_resolve_live_engine_config", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(live_transcribe, "live_speaker_labels_requested", lambda: True)
    transcript = SimpleNamespace(
        segments=[], settling_state={"eligible": True, "policy": {"enabled": True}}
    )
    session = _FakeSession(_FakeRecording(RecordingStatus.UPLOADING, transcript))
    calls: list[tuple[int, int]] = []
    labels: list[int] = []

    def decode(step: Any, _config: Any, audio: Any, _start: int) -> Any:
        calls.append((step.sequence, audio.numel()))
        if failure:
            raise TranscriptionError("failed", engine="whisper")
        return {"asr_segments": []}, "cuda"

    monkeypatch.setattr(worker, "_decode_words", decode)
    monkeypatch.setattr(
        label_step, "run_live_speaker_labels", lambda run: labels.append(run.sequence)
    )
    _run_live_task(monkeypatch, 42, 0, session)
    _run_live_task(monkeypatch, 42, 1, session)
    assert calls == [(3, 128000)]
    assert labels == [3]
    assert live_transcribe.read_live_state(tmp_path / "live")["next_expected"] == 4
    assert len(rolling_lane.observations) == 1
    assert (rolling_lane.observations[0]["payload"] is None) is failure


@pytest.mark.parametrize("public_changed", [False, True])
@pytest.mark.parametrize("owner_enabled", [False, True])
def test_manifest_checkpoint_refreshes_meeting_edge_only_for_public_rolling_changes(
    monkeypatch: pytest.MonkeyPatch, public_changed: bool, owner_enabled: bool
) -> None:
    from backend.core import db
    from backend.models.recording import RecordingStatus
    from backend.processing import live_persistence
    from backend.tests.test_live_transcription import _FakeRecording, _FakeSession

    recording = _FakeRecording(RecordingStatus.UPLOADING, SimpleNamespace(segments=[]))
    recording.user = SimpleNamespace(settings={"enable_meeting_edge": owner_enabled})
    session = _FakeSession(recording)
    monkeypatch.setattr(db, "get_sync_session", lambda: session)
    result = live_persistence._persist_live_run(
        recording_id=42,
        sequence=0,
        run=[0],
        new_segments=[],
        state={},
        live_config={"_settling_public_changed": public_changed},
        live_config_hash="config",
        live_model_name="model",
    )
    assert result.accepted
    assert result.dispatch_meeting_edge is (public_changed and owner_enabled)


def test_redecoded_words_keep_their_own_audio_source_when_the_window_changes_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.processing import vad

    step = _step(tmp_path, sequence=1)
    microphone = np.zeros((32000, 2), np.float32)
    microphone[:, 1] = 0.2
    system = np.zeros((32000, 2), np.float32)
    system[:, 0] = 0.2
    sf.write(tmp_path / "0.wav", microphone, 16000)
    sf.write(tmp_path / "1.wav", system, 16000)
    monkeypatch.setattr(
        transcribe,
        "transcribe_audio",
        lambda *_args, **_kwargs: {
            "text": "mic system",
            "segments": [
                {
                    "start": 0,
                    "end": 4,
                    "text": "mic system",
                    "words": [
                        {"word": "mic", "start": 0.5, "end": 1.5},
                        {"word": "system", "start": 2.5, "end": 3.5},
                    ],
                }
            ],
        },
    )
    monkeypatch.setattr(transcribe, "loaded_transcription_device", lambda _: "cuda")
    monkeypatch.setattr(
        vad.silero_vad,
        "save_audio",
        lambda path, audio, sampling_rate: sf.write(
            path, audio.squeeze(0).numpy(), sampling_rate
        ),
    )
    payload, _ = worker._decode_words(step, {}, torch.ones(64000), 0)
    words = payload["asr_segments"][0]["words"]
    assert [word["source_channel_evidence"]["dominant_source"] for word in words] == [
        "microphone",
        "system",
    ]


@pytest.mark.parametrize(
    "case",
    [
        ("live", "episode", True),
        ("paused", "episode", False),
        ("live", "replaced", False),
    ],
)
def test_rejected_step_drops_observation_without_stalling_current_episode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rolling_lane: Any,
    case: tuple[str, str, bool],
) -> None:
    from backend.core import db
    from backend.models.recording import RecordingStatus
    from backend.tests.test_live_transcription import _FakeRecording, _FakeSession
    from backend.utils.canonical_pipeline import live_settling as publication

    phase, generation, expected = case
    step = _step(tmp_path)
    transcript = SimpleNamespace(
        settling_state={
            "eligible": True,
            "generation": generation,
            "phase": phase,
            "policy": {"enabled": True},
        }
    )
    recording = _FakeRecording(RecordingStatus.UPLOADING, transcript)
    recording.capture_epoch = 1
    monkeypatch.setattr(db, "get_sync_session", lambda: _FakeSession(recording))
    monkeypatch.setattr(
        publication,
        "publish_live_settling",
        lambda *_args, **_kwargs: publication.SettlingPublicationResult(False),
    )
    result = rolling_lane.publisher(
        step, rolling_lane.snapshot, {"asr_segments": []}, None
    )
    assert result.handled and result.accepted is expected
    assert not result.public_changed
