"""Silero VAD is loaded once per worker process and device (#776).

The live lane ran VAD on every 2 s chunk and loaded the model each time. The
fakes here count loads; the real model, which ships inside the silero_vad
package, shows that a reused model finds the same speech as a fresh one.
"""

from __future__ import annotations

import threading
from typing import Any

import numpy as np
import pytest
import torch
from scipy.signal import lfilter

from backend.processing import vad

RATE = 16000


class _FakeModel:
    def __init__(self, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.devices: list[str] = []

    def to(self, device: torch.device) -> _FakeModel:
        if device.type == self.fail_on:
            raise RuntimeError(f"no {device.type}")
        self.devices.append(str(device))
        return self


@pytest.fixture
def loads(monkeypatch: pytest.MonkeyPatch) -> list[_FakeModel]:
    """Count model loads; VAD itself answers with one fixed region."""
    made: list[_FakeModel] = []

    def load(*_args: Any, **_kwargs: Any) -> _FakeModel:
        made.append(_FakeModel())
        return made[-1]

    monkeypatch.setattr(vad.silero_vad, "load_silero_vad", load)
    monkeypatch.setattr(
        vad.silero_vad,
        "get_speech_timestamps",
        lambda *_a, **_k: [{"start": 0.5, "end": 1.0}],
    )
    return made


def test_live_chunks_load_the_model_once(loads: list[_FakeModel]) -> None:
    for _ in range(3):
        assert vad.detect_speech_segments(torch.zeros(RATE)) == [
            {"start": 0.5, "end": 1.0}
        ]

    assert len(loads) == 1


def test_the_final_vad_stage_reuses_the_live_model(
    loads: list[_FakeModel], tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import soundfile

    monkeypatch.setattr(
        vad.silero_vad,
        "save_audio",
        lambda path, audio, sampling_rate: soundfile.write(
            path, audio.squeeze(0).numpy(), sampling_rate
        ),
    )
    source = tmp_path / "in.wav"
    soundfile.write(source, np.full(2 * RATE, 0.1, np.float32), RATE)
    vad.detect_speech_segments(torch.zeros(RATE))
    for index in range(2):
        ok, _speech = vad.mute_non_speech_segments(
            str(source), str(tmp_path / f"out{index}.wav")
        )
        assert ok

    assert len(loads) == 1


def test_a_failed_gpu_load_falls_back_to_the_cpu_and_tries_the_gpu_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.utils.config_manager import config_manager

    made: list[_FakeModel] = []

    def load(*_args: Any, **_kwargs: Any) -> _FakeModel:
        made.append(_FakeModel(fail_on="cuda"))
        return made[-1]

    seen: list[str] = []

    def timestamps(audio: torch.Tensor, model: _FakeModel, **_kwargs: Any) -> list:
        seen.append(str(audio.device))
        return []

    monkeypatch.setattr(vad.silero_vad, "load_silero_vad", load)
    monkeypatch.setattr(vad.silero_vad, "get_speech_timestamps", timestamps)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(
        config_manager,
        "get",
        lambda key, default=None: "cuda" if key == "processing_device" else default,
    )

    vad.detect_speech_segments(torch.zeros(RATE))
    vad.detect_speech_segments(torch.zeros(RATE))

    # Each call tries the GPU (a load that fails) and then runs on the one
    # CPU model, which is cached; the GPU never is.
    assert [model.devices for model in made] == [[], ["cpu"], []]
    assert list(vad._VAD_MODELS) == ["cpu"]
    assert seen == ["cpu", "cpu"]


VOWELS = ((730, 1090, 2440), (270, 2290, 3010), (530, 1840, 2480), (300, 870, 2240))


def _speech_like(seconds: float, seed: int) -> torch.Tensor:
    """Voiced syllables through vowel formants, with pauses Silero can find."""
    rng = np.random.default_rng(seed)
    out = [np.zeros(RATE // 2)]
    while sum(part.size for part in out) < seconds * RATE:
        n = int(rng.uniform(0.15, 0.3) * RATE)
        t = np.arange(n) / RATE
        phase = np.cumsum(2 * np.pi * rng.uniform(110, 220) * (1 + 0.05 * t) / RATE)
        source = sum(np.sin(k * phase) / k for k in range(1, 30))
        voiced = np.zeros(n)
        for formant, gain in zip(VOWELS[rng.integers(len(VOWELS))], (1, 0.6, 0.3)):
            r = np.exp(-np.pi * 100 / RATE)
            theta = 2 * np.pi * formant / RATE
            voiced += gain * lfilter(
                [1 - r], [1, -2 * r * np.cos(theta), r * r], source
            )
        out.append(voiced * np.sin(np.pi * np.arange(n) / n))
        if rng.random() < 0.2:
            out.append(np.zeros(int(rng.uniform(0.4, 0.9) * RATE)))
    audio = np.concatenate(out)[: int(seconds * RATE)]
    audio = 0.3 * audio / np.max(np.abs(audio))
    audio += 0.002 * rng.standard_normal(audio.size)
    return torch.from_numpy(audio.astype(np.float32))


def _fresh(audio: torch.Tensor) -> list[dict[str, float]]:
    """What a model loaded for this input alone finds, as before #776."""
    from backend.processing.live_limits import LIVE_MIN_SILENCE_MS

    config = vad.get_vad_config_from_settings()
    return [
        {"start": float(item["start"]), "end": float(item["end"])}
        for item in vad.silero_vad.get_speech_timestamps(
            audio,
            vad.silero_vad.load_silero_vad(),
            sampling_rate=RATE,
            return_seconds=True,
            threshold=config["threshold"],
            min_speech_duration_ms=config["min_speech_duration_ms"],
            min_silence_duration_ms=LIVE_MIN_SILENCE_MS,
            speech_pad_ms=300,
        )
    ]


def test_a_reused_model_finds_the_same_speech_as_a_fresh_one() -> None:
    from backend.processing.live_limits import LIVE_MIN_SILENCE_MS

    inputs = [_speech_like(6, seed) for seed in (1, 2, 1, 3)]
    reused = [
        vad.detect_speech_segments(
            audio, min_silence_duration_ms=LIVE_MIN_SILENCE_MS, speech_pad_ms=300
        )
        for audio in inputs
    ]

    assert len(vad._VAD_MODELS) == 1
    assert reused == [_fresh(audio) for audio in inputs]
    assert all(reused), "the synthetic input must hold speech Silero can find"


def test_threads_never_share_the_model_mid_input() -> None:
    inputs = [_speech_like(4, seed) for seed in range(4)]
    expected = [vad.detect_speech_segments(audio) for audio in inputs]
    found: dict[int, list[list[dict[str, float]]]] = {i: [] for i in range(4)}

    def run(index: int) -> None:
        for _ in range(3):
            found[index].append(vad.detect_speech_segments(inputs[index]))

    threads = [threading.Thread(target=run, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert all(runs == [expected[i]] * 3 for i, runs in found.items())
