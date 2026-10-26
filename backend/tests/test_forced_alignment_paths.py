"""Canary alignment reaches the actual live and final word consumers."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import soundfile as sf

from backend.processing import forced_aligner_model, transcribe
from backend.processing.forced_alignment import align_transcription
from backend.processing.live_asr_payload import (
    _build_live_confidence_payload,
    _extract_region_segment_payloads,
    _filter_region_repetitions,
)
from backend.processing.unheard_words import remove_unheard_words
from backend.utils.word_text import reconstruct_text_from_words
from backend.worker.tasks.final_segments import combine_and_consolidate_segments


@pytest.fixture
def aligned_canary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    path = tmp_path / "audio.wav"
    sf.write(path, np.full(6 * 16000, 0.1, dtype=np.float32), 16000, subtype="FLOAT")
    source = {
        "text": "Hello,  world!",
        "language": "en",
        "segments": [
            {"start": 1.0, "end": 5.0, "text": "Hello,  world!"},
        ],
    }
    calls: list[dict[str, Any]] = []

    def align(audio: np.ndarray, **kwargs: Any) -> list[dict[str, Any]]:
        calls.append({"audio": audio.copy(), **kwargs})
        return [
            {"text": "Hello", "start_time": 0.2, "end_time": 0.6},
            {"text": "world", "start_time": 2.0, "end_time": 2.6},
        ]

    monkeypatch.setattr(forced_aligner_model, "align_audio", align)
    engine = SimpleNamespace(transcribe=lambda *_args: source)
    monkeypatch.setattr(transcribe, "_ENGINE_REGISTRY", {"canary": engine})
    config = {"transcription_backend": "canary", "processing_device": "cpu"}
    return SimpleNamespace(path=str(path), source=source, calls=calls, config=config)


def test_canary_dispatcher_reads_exact_segment_and_aligns(aligned_canary: Any) -> None:
    sample = aligned_canary
    result = transcribe.transcribe_audio(sample.path, sample.config)
    assert result["text"] == "Hello,  world!"
    words = result["segments"][0]["words"]
    assert [(word["start"], word["end"]) for word in words] == [(1.2, 1.6), (3.0, 3.6)]
    assert reconstruct_text_from_words(words) == "Hello,  world!"
    assert [(word["source_start"], word["source_end"]) for word in words] == [
        (0, 6),
        (6, 14),
    ]
    assert sample.calls[0]["text"] == "Hello,  world!"
    assert "processing_device" not in sample.calls[0]
    assert sample.calls[0]["audio"].shape == (4 * 16000,)
    assert sample.calls[0]["audio"].dtype == np.float32
    assert "words" not in sample.source["segments"][0]


def test_canary_final_merge_assigns_real_words_to_speakers(aligned_canary: Any) -> None:
    from pyannote.core import Annotation, Segment

    result = transcribe.transcribe_audio(aligned_canary.path, aligned_canary.config)
    speakers = Annotation()
    speakers[Segment(1.0, 2.0)] = "A"
    speakers[Segment(2.5, 4.0)] = "B"
    final = combine_and_consolidate_segments(
        result, speakers, enable_diarization=True, recording_id=1
    )
    assert [segment["speaker"] for segment in final] == ["A", "B"]
    assert [segment["text"] for segment in final] == ["Hello,", "world!"]
    assert [word["start"] for segment in final for word in segment["words"]] == [
        1.2,
        3.0,
    ]
    assert all(word["source_id"] for segment in final for word in segment["words"])


def test_live_context_trim_and_payload_add_offset_once(aligned_canary: Any) -> None:
    result = transcribe.transcribe_audio(aligned_canary.path, aligned_canary.config)
    region = _extract_region_segment_payloads(result, prefix_s=2.0)
    assert len(region) == 1
    assert region[0]["text"] == "world!"
    payload = _build_live_confidence_payload(
        region_segment_payloads=region,
        region_start_ms=10_000,
        region_end_ms=14_000,
    )
    assert payload["asr_word_timestamps_available"] is True
    word = payload["asr_segments"][0]["words"][0]
    assert (word["start_ms"], word["end_ms"]) == (11_000, 11_600)
    assert word["word"] == "  world!"
    assert (word["source_start"], word["source_end"]) == (6, 14)


@pytest.mark.parametrize("failure", [False, True])
def test_real_live_lane_dispatches_canary_and_publishes_evidence(
    aligned_canary: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: bool,
) -> None:
    import torch

    from backend.processing import live_transcribe, vad

    def save(path: str, tensor: Any, *, sampling_rate: int) -> None:
        sf.write(path, tensor.squeeze(0).numpy(), sampling_rate, subtype="FLOAT")

    monkeypatch.setattr(vad.silero_vad, "save_audio", save)
    if failure:
        monkeypatch.setattr(
            forced_aligner_model, "align_audio", lambda *_args, **_kwargs: []
        )
    audio = torch.full((6 * 16000,), 0.1)
    lines = live_transcribe._transcribe_live_regions(
        recording_id=1,
        sequence=2,
        complete=[{"start": 0.0, "end": 6.0}],
        combined=audio,
        combined_channels=audio.unsqueeze(0),
        combined_abs_start=12.0,
        prev_context=torch.zeros(0),
        context_window_samples=0,
        live_dir=tmp_path,
        live_config=aligned_canary.config,
        source_channel_labels={},
        state={},
    )
    assert len(lines) == 1
    assert lines[0]["text"] == "Hello, world!"
    evidence = lines[0]["confidence_payload"]
    segment = evidence["asr_segments"][0]
    assert evidence["asr_word_timestamps_available"] is not failure
    if failure:
        assert segment["alignment_error"]
        assert "words" not in segment
    else:
        assert [(word["start_ms"], word["end_ms"]) for word in segment["words"]] == [
            (13_200, 13_600),
            (15_000, 15_600),
        ]
        assert reconstruct_text_from_words(segment["words"]) == "Hello,  world!"
    assert not (tmp_path / "clip.wav").exists()


def test_forced_words_use_native_phantom_filter(aligned_canary: Any) -> None:
    result = transcribe.transcribe_audio(aligned_canary.path, aligned_canary.config)
    filtered, dropped = remove_unheard_words(result, [(2.0, 4.5)])
    assert dropped.words == 1
    assert filtered["text"] == "Hello,"
    assert len(filtered["segments"][0]["words"]) == 1
    assert filtered["segments"][0]["words"][0]["source_start"] == 0


@pytest.mark.parametrize("backend", ["whisper", "parakeet"])
def test_native_dispatch_never_requests_alignment(
    aligned_canary: Any,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("Native engine requested forced alignment")

    source = aligned_canary.source
    monkeypatch.setattr(forced_aligner_model, "align_audio", forbidden)
    monkeypatch.setattr(
        transcribe,
        "_ENGINE_REGISTRY",
        {backend: SimpleNamespace(transcribe=lambda *_args: source)},
    )
    assert (
        transcribe.transcribe_audio(
            aligned_canary.path, {"transcription_backend": backend}
        )
        is source
    )


@pytest.mark.parametrize("backend", ["whisper", "parakeet"])
def test_native_live_lane_retains_head_repetition_and_confidence_payload(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import torch

    from backend.processing import live_transcribe, vad

    monkeypatch.setattr(vad.silero_vad, "save_audio", lambda *_args, **_kwargs: None)
    source = {
        "text": "yes yes yes please",
        "segments": [
            {
                "start": 0,
                "end": 2,
                "text": "yes yes yes please",
                "words": [
                    {"word": "yes", "start": 0, "end": 0.5},
                    {"word": "please", "start": 1, "end": 1.5},
                ],
            }
        ],
    }
    monkeypatch.setattr(
        transcribe,
        "_ENGINE_REGISTRY",
        {backend: SimpleNamespace(transcribe=lambda *_args: source)},
    )
    audio = torch.full((2 * 16000,), 0.1)
    lines = live_transcribe._transcribe_live_regions(
        recording_id=1,
        sequence=1,
        complete=[{"start": 0, "end": 2}],
        combined=audio,
        combined_channels=audio.unsqueeze(0),
        combined_abs_start=10,
        prev_context=torch.zeros(0),
        context_window_samples=0,
        live_dir=tmp_path,
        live_config={"transcription_backend": backend},
        source_channel_labels={},
        state={},
    )
    assert len(lines) == 1
    assert lines[0]["text"] == "yes please"
    evidence = lines[0]["confidence_payload"]["asr_segments"][0]
    assert evidence["text"] == "yes yes yes please"
    assert evidence["words"] == [
        {"word": "yes", "start_ms": 10000, "end_ms": 10500},
        {"word": "please", "start_ms": 11000, "end_ms": 11500},
    ]


def test_failure_survives_live_repetition_and_final_fallback(
    aligned_canary: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise ValueError("No usable alignment")

    monkeypatch.setattr(forced_aligner_model, "align_audio", broken)
    result = transcribe.transcribe_audio(aligned_canary.path, aligned_canary.config)
    assert result["text"] == "Hello,  world!"
    assert "words" not in result["segments"][0]
    assert result["segments"][0]["alignment_error"]
    region = _extract_region_segment_payloads(result, prefix_s=4.5)
    assert region and region[0]["alignment_error"]
    final = combine_and_consolidate_segments(
        result, None, enable_diarization=False, recording_id=1
    )
    assert final[0]["alignment_error"]
    failed = [
        {"start": 0, "end": 1, "text": "yes"},
        {"start": 1, "end": 2, "text": "yes yes", "alignment_error": "unplaced words"},
    ]
    assert _filter_region_repetitions(failed) == failed


def test_live_repetition_filter_removes_matching_word_evidence() -> None:
    words = [
        {"start": i, "end": i + 0.4, "word": word}
        for i, word in enumerate(["yes", "yes", "yes", "please"])
    ]
    filtered = _filter_region_repetitions(
        [{"text": "yes yes yes please", "words": words}]
    )
    assert filtered[0]["text"] == "yes please"
    assert filtered[0]["words"] == [words[0], words[3]]


def test_unreadable_audio_keeps_text_with_failure() -> None:
    source = {
        "text": "keep this",
        "segments": [{"start": 0, "end": 1, "text": "keep this"}],
    }
    result = align_transcription(source, "/nonexistent-alignment-audio.wav", {})
    assert result["text"] == "keep this"
    assert result["segments"][0]["alignment_error"]


def test_release_clears_aligner_before_engine_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    monkeypatch.setattr(
        forced_aligner_model,
        "release_forced_aligner_model_cache",
        lambda: order.append("aligner"),
    )
    monkeypatch.setattr(
        transcribe,
        "_ENGINE_REGISTRY",
        {"canary": SimpleNamespace(release=lambda: order.append("asr"))},
    )
    transcribe.release_model_cache()
    assert order == ["aligner", "asr"]
