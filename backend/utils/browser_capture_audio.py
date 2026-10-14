"""Audio formats at the edges of browser capture.

The browser uploads each 2 s segment as FLAC: lossless, sample-exact and about
a third the size of PCM. The backend decodes it straight into the canonical
16 kHz two-channel WAV that live transcription and finalize already read, in
process and in about a millisecond, so no worker hop or ffmpeg run sits between
an upload and the live lane (issue #417).

Finalize writes the master the same way, streaming every segment's PCM into one
FLAC file. That is exact by construction and several times faster than asking
ffmpeg's concat demuxer to open thousands of small files.
"""

from __future__ import annotations

import os
import wave
from pathlib import Path
from typing import Sequence

from backend.processing.browser_live_audio import (
    BROWSER_LIVE_CHANNEL_COUNT,
    BROWSER_LIVE_SAMPLE_RATE_HZ,
)

BROWSER_FLAC_SEGMENT_SUFFIX = ".flac"
BROWSER_MASTER_SUFFIX = ".flac"
PCM_SAMPLE_WIDTH_BYTES = 2
_PARTIAL_SUFFIX = ".part"


class BrowserAudioFormatError(ValueError):
    """Audio that is not in the format browser capture produces."""


def _partial_path(destination: Path) -> Path:
    # Not a ``<sequence>.<ext>`` name, so directory scans that parse a
    # sequence from the file stem never mistake it for a segment.
    return destination.with_name(destination.name + _PARTIAL_SUFFIX)


def decode_browser_flac_segment(source: Path, destination: Path) -> int:
    """Decode an uploaded FLAC segment to the canonical WAV. Returns its frames.

    Anything other than 16 kHz, two-channel, 16-bit FLAC is refused: the
    browser never sends it, and the live lane and the master both assume it.
    A corrupt stream raises rather than decoding to a shorter segment, because
    libFLAC reports a lost sync instead of skipping the damaged frame.
    """
    import soundfile

    try:
        info = soundfile.info(str(source))
        if (
            info.format != "FLAC"
            or info.subtype != "PCM_16"
            or int(info.samplerate) != BROWSER_LIVE_SAMPLE_RATE_HZ
            or int(info.channels) != BROWSER_LIVE_CHANNEL_COUNT
        ):
            raise BrowserAudioFormatError(
                "Expected 16 kHz two-channel 16-bit FLAC, got "
                f"{info.format}/{info.subtype} at {info.samplerate} Hz with "
                f"{info.channels} channel(s)."
            )
        frames, _ = soundfile.read(str(source), dtype="int16", always_2d=True)
    except (RuntimeError, ValueError) as exc:
        if isinstance(exc, BrowserAudioFormatError):
            raise
        raise BrowserAudioFormatError(f"Unreadable FLAC segment: {exc}") from exc

    frame_count = int(frames.shape[0])
    if frame_count == 0 or frame_count != int(info.frames):
        raise BrowserAudioFormatError(
            f"FLAC segment decoded to {frame_count} of {info.frames} frames."
        )

    partial = _partial_path(destination)
    try:
        with wave.open(str(partial), "wb") as wav_file:
            wav_file.setnchannels(BROWSER_LIVE_CHANNEL_COUNT)
            wav_file.setsampwidth(PCM_SAMPLE_WIDTH_BYTES)
            wav_file.setframerate(BROWSER_LIVE_SAMPLE_RATE_HZ)
            wav_file.writeframes(frames.astype("<i2", copy=False).tobytes())
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)
    return frame_count


def _wav_format(path: Path) -> tuple[int, int, int]:
    with wave.open(str(path), "rb") as wav_file:
        return (
            int(wav_file.getframerate()),
            int(wav_file.getnchannels()),
            int(wav_file.getsampwidth()),
        )


def write_browser_master_audio(
    segment_paths: Sequence[str | Path], destination: str | Path
) -> float:
    """Join the segment WAVs, in order, into one FLAC master. Returns seconds.

    The duration is counted from the frames written, so it matches the audio
    exactly. Every segment must share the first one's format.
    """
    import numpy as np
    import soundfile

    if not segment_paths:
        raise BrowserAudioFormatError("No audio segments to join.")

    paths = [Path(path) for path in segment_paths]
    sample_rate, channels, sample_width = _wav_format(paths[0])
    if sample_width != PCM_SAMPLE_WIDTH_BYTES:
        raise BrowserAudioFormatError(
            f"Segment {paths[0].name} is {sample_width * 8}-bit, not 16-bit PCM."
        )

    destination_path = Path(destination)
    partial = _partial_path(destination_path)
    total_frames = 0
    try:
        with soundfile.SoundFile(
            str(partial),
            mode="w",
            samplerate=sample_rate,
            channels=channels,
            format="FLAC",
            subtype="PCM_16",
        ) as master:
            for path in paths:
                with wave.open(str(path), "rb") as wav_file:
                    segment_format = (
                        int(wav_file.getframerate()),
                        int(wav_file.getnchannels()),
                        int(wav_file.getsampwidth()),
                    )
                    if segment_format != (sample_rate, channels, sample_width):
                        raise BrowserAudioFormatError(
                            f"Segment {path.name} does not match the first "
                            "segment's format."
                        )
                    pcm = np.frombuffer(
                        wav_file.readframes(wav_file.getnframes()), dtype="<i2"
                    )
                master.write(pcm.reshape(-1, channels))
                total_frames += pcm.size // channels
        os.replace(partial, destination_path)
    finally:
        partial.unlink(missing_ok=True)

    return total_frames / sample_rate
