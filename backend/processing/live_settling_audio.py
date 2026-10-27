"""Bounded rolling audio and speech gates on the original capture clock."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.processing.live_audio_buffer import LIVE_SAMPLE_RATE


def rolling_audio_tail(
    temp_dir: Path, latest_sequence: int, window_s: float
) -> tuple[Any, float]:
    """Read a bounded mono tail without changing capture or diariser checkpoints."""
    from backend.processing.live_audio_buffer import _mix_live_audio_channels

    channels, duration = rolling_channel_tail(temp_dir, latest_sequence, window_s)
    return _mix_live_audio_channels(channels), duration


def rolling_channel_tail(
    temp_dir: Path, latest_sequence: int, window_s: float
) -> tuple[Any, float]:
    """Read recent original source channels, keeping the total input cap."""
    import torch

    from backend.processing.live_audio_buffer import (
        _concat_live_audio_channels,
        _read_live_audio_channels,
    )

    remaining = int(window_s * LIVE_SAMPLE_RATE)
    parts = []
    sequence = latest_sequence
    latest_duration_s = 0.0
    while remaining > 0 and sequence >= 0:
        path = temp_dir / f"{sequence}.wav"
        if not path.exists():
            break
        audio = _read_live_audio_channels(str(path))
        if sequence == latest_sequence:
            latest_duration_s = audio.size(-1) / LIVE_SAMPLE_RATE
        selected = audio[:, -remaining:]
        parts.append(selected)
        remaining -= selected.size(-1)
        sequence -= 1
    tail = (
        _concat_live_audio_channels(list(reversed(parts)))
        if parts
        else torch.zeros((1, 0))
    )
    return tail, latest_duration_s


def chunk_has_speech(
    *,
    audio: Any,
    audio_start_s: float,
    chunk_bounds_s: tuple[float, float],
    vad_spans: list[tuple[float, float]],
    diariser_spans: list[tuple[float, float]],
) -> bool:
    """Open for VAD OR available diariser speech after masking muted samples."""
    chunk_start_s, chunk_end_s = chunk_bounds_s
    for start, end in [*vad_spans, *diariser_spans]:
        left = max(start, audio_start_s, chunk_start_s)
        right = min(end, chunk_end_s)
        if right <= left:
            continue
        first = max(0, int(round((left - audio_start_s) * LIVE_SAMPLE_RATE)))
        stop = max(first, int(round((right - audio_start_s) * LIVE_SAMPLE_RATE)))
        if bool((audio[first:stop].abs() > 1e-7).any()):
            return True
    return False


def drained_chunk_bounds(
    temp_dir: Path,
    first_sequence: int,
    latest_sequence: int,
    audio_end_ms: int,
    floor_ms: int,
) -> list[tuple[int, int, int]]:
    """Retain actual drained chunk bounds only within the rolling input cap."""
    from backend.processing.live_audio_buffer import _read_live_audio_channels

    chunks: list[tuple[int, int, int]] = []
    end_ms = audio_end_ms
    for sequence in range(latest_sequence, first_sequence - 1, -1):
        if end_ms <= floor_ms or len(chunks) >= 16:
            break
        audio = _read_live_audio_channels(str(temp_dir / f"{sequence}.wav"))
        start_ms = end_ms - round(audio.size(-1) * 1000 / LIVE_SAMPLE_RATE)
        chunks.append((sequence, start_ms, end_ms))
        end_ms = start_ms
    return list(reversed(chunks))
