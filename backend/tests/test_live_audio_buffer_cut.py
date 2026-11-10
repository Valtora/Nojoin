"""Where the live audio buffer is cut, and where the next run's audio starts."""

from __future__ import annotations

from pathlib import Path

import pytest
import torch

from backend.processing import vad
from backend.processing.live_audio_buffer import LIVE_SAMPLE_RATE, stage_live_audio


@pytest.fixture
def saved(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Samples written per file name by the staged carry-over."""
    written: dict[str, int] = {}

    def save_audio(path: str, tensor: torch.Tensor, **_kwargs: object) -> None:
        written[Path(path).name] = tensor.shape[-1]

    monkeypatch.setattr(vad.silero_vad, "save_audio", save_audio)
    return written


@pytest.mark.parametrize("samples", [32001, 16208])
def test_consuming_the_whole_buffer_leaves_nothing_to_replay(
    tmp_path: Path, saved: dict[str, int], samples: int
) -> None:
    """A run that settles every sample leaves no carry-over, and the next run
    starts where this one's audio ended. Cutting at ``samples / 16000`` seconds
    used to truncate back to one sample short, replaying it."""
    audio = torch.zeros(1, samples)

    with stage_live_audio(
        live_dir=tmp_path,
        buffer_path=str(tmp_path / "buffer.wav"),
        context_path=str(tmp_path / "context.wav"),
        combined_channels=audio,
        combined_abs_start=10.0,
        cut_point=samples / LIVE_SAMPLE_RATE,
        prev_context_channels=torch.zeros(1, 0),
        context_window_samples=0,
    ) as staged:
        assert "buffer.wav" not in saved
        assert staged.new_abs_start == pytest.approx(10.0 + samples / LIVE_SAMPLE_RATE)


def test_a_cut_inside_the_buffer_carries_the_samples_after_it(
    tmp_path: Path, saved: dict[str, int]
) -> None:
    """The next run's origin is the first carried sample, not the requested cut."""
    audio = torch.zeros(1, 32000)

    with stage_live_audio(
        live_dir=tmp_path,
        buffer_path=str(tmp_path / "buffer.wav"),
        context_path=str(tmp_path / "context.wav"),
        combined_channels=audio,
        combined_abs_start=0.0,
        # 1.00003 s is between samples 16000 and 16001.
        cut_point=1.00003,
        prev_context_channels=torch.zeros(1, 0),
        context_window_samples=0,
    ) as staged:
        carried = saved["buffer.wav"]
        assert staged.new_abs_start == pytest.approx(
            (32000 - carried) / LIVE_SAMPLE_RATE
        )


def test_a_cut_just_past_the_buffer_does_not_advance_beyond_it(
    tmp_path: Path, saved: dict[str, int]
) -> None:
    audio = torch.zeros(1, 32000)

    with stage_live_audio(
        live_dir=tmp_path,
        buffer_path=str(tmp_path / "buffer.wav"),
        context_path=str(tmp_path / "context.wav"),
        combined_channels=audio,
        combined_abs_start=0.0,
        # A VAD end 40 microseconds after the last sample.
        cut_point=2.00004,
        prev_context_channels=torch.zeros(1, 0),
        context_window_samples=0,
    ) as staged:
        assert "buffer.wav" not in saved
        assert staged.new_abs_start == pytest.approx(2.0)
