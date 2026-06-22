"""Tests for the explicit soundfile-based audio loader in backend.utils.audio.

These cover the loader that replaced the global torchaudio monkey-patch when the
project moved to torch 2.11 (torchaudio now routes I/O through torchcodec and
ignores the backend argument).
"""

import numpy as np
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
