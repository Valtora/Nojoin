"""Tests for the explicit soundfile-based audio loader in backend.utils.audio.

These cover the loader that replaced the global torchaudio monkey-patch when the
project moved to torch 2.11 (torchaudio now routes I/O through torchcodec and
ignores the backend argument).
"""

import numpy as np
import pytest
import soundfile as sf
import torch

from backend.utils.audio import load_audio


def test_load_audio_mono_returns_channels_first(tmp_path):
    sr = 16000
    samples = (0.1 * np.sin(2 * np.pi * 220 * np.arange(sr) / sr)).astype("float32")
    path = tmp_path / "mono.wav"
    sf.write(path, samples, sr)

    wav, out_sr = load_audio(str(path))

    assert out_sr == sr
    assert isinstance(wav, torch.Tensor)
    assert wav.dtype == torch.float32
    assert wav.shape == (1, sr)  # (channels, frames)
    assert torch.allclose(wav[0], torch.from_numpy(samples), atol=1e-4)


def test_load_audio_stereo_channels_first(tmp_path):
    sr = 16000
    left = np.zeros(sr, dtype="float32")
    right = (0.2 * np.ones(sr)).astype("float32")
    stereo = np.stack([left, right], axis=1)  # soundfile expects (frames, channels)
    path = tmp_path / "stereo.wav"
    sf.write(path, stereo, sr)

    wav, out_sr = load_audio(str(path))

    assert out_sr == sr
    assert wav.shape == (2, sr)  # (channels, frames)
    assert wav.is_contiguous()
    assert torch.allclose(wav[1], torch.full((sr,), 0.2), atol=1e-4)


def test_load_audio_channels_last(tmp_path):
    sr = 8000
    stereo = np.zeros((sr, 2), dtype="float32")
    path = tmp_path / "channels_last.wav"
    sf.write(path, stereo, sr)

    wav, _ = load_audio(str(path), channels_first=False)

    assert wav.shape == (sr, 2)  # (frames, channels)


def test_safe_read_audio_resamples_to_target(tmp_path):
    """safe_read_audio should load via load_audio, mono-mix, and resample."""
    from backend.processing.vad import safe_read_audio

    src_sr = 48000
    samples = (0.05 * np.sin(2 * np.pi * 440 * np.arange(src_sr) / src_sr)).astype(
        "float32"
    )
    path = tmp_path / "resample.wav"
    sf.write(path, samples, src_sr)

    out = safe_read_audio(str(path), sampling_rate=16000)

    assert isinstance(out, torch.Tensor)
    assert out.ndim == 1  # mono, channel dimension squeezed
    # 1s at 48k resampled to 16k -> ~16000 frames
    assert abs(out.shape[0] - 16000) <= 1


def test_extract_audio_clip_rejects_nonpositive_duration(monkeypatch):
    from backend.utils import audio

    monkeypatch.setattr(audio, "ensure_ffmpeg_in_path", lambda: None)

    with pytest.raises(RuntimeError, match="Clip duration must be positive"):
        audio.extract_audio_clip(
            "in.wav", "out.wav", start_seconds=1.0, end_seconds=1.0
        )


def test_extract_audio_clip_unexpected_error_cleans_up_and_raises(
    tmp_path, monkeypatch
):
    """The unexpected-error path must remove any partial clip and raise.

    Regression guard: it previously referenced undefined names and returned
    False from a function annotated to return None.
    """
    from backend.utils import audio

    monkeypatch.setattr(audio, "ensure_ffmpeg_in_path", lambda: None)

    def boom(*args, **kwargs):
        raise RuntimeError("ffmpeg exploded unexpectedly")

    monkeypatch.setattr(audio.subprocess, "run", boom)

    output_path = tmp_path / "out.wav"
    output_path.write_bytes(b"partial")  # simulate a partially-written clip

    with pytest.raises(RuntimeError, match="Failed to extract audio clip"):
        audio.extract_audio_clip(
            "in.wav", str(output_path), start_seconds=0.0, end_seconds=0.5
        )

    assert not output_path.exists()  # partial output cleaned up
