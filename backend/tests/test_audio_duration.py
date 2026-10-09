"""Which audio track, and which reported length, a recording is timed by.

A file's container can run longer than its audio (a screen recording whose
microphone stopped), carry several audio tracks, or report a length of zero
for a track it never filled. The stored duration drives the import window
manifests, so it has to be the length of the audio ffmpeg actually decodes.
"""

from __future__ import annotations

import subprocess

import pytest

from backend.processing import audio_preprocessing
from backend.utils import audio
from backend.utils.audio import (
    FFPROBE_TIMEOUT_S,
    NoAudioStreamError,
    UnreadableAudioStreamError,
    audio_duration_from_probe,
    decoded_audio_stream,
)


def _probe(streams: list[dict], container: str | None = None) -> dict:
    return {"format": {"duration": container}, "streams": streams}


def _audio(**fields) -> dict:
    return {"codec_type": "audio", "channels": 2, **fields}


# A Matroska track that holds no packets.
_EMPTY = {"tags": {"DURATION": "00:00:00.000000000"}}


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (_probe([_audio(duration="2.500000")], "6.0"), 2.5),
        (
            _probe([_audio(duration="6.0", tags={"DURATION": "00:00:02.500000000"})]),
            2.5,
        ),
        (_probe([_audio(tags={"DURATION-eng": "01:02:03.500000000"})], "9.0"), 3723.5),
        (_probe([_audio()], "4.25"), 4.25),
        (_probe([_audio(duration="0.000000")], "4.25"), 4.25),
        (_probe([_audio(duration="-0.02")], "4.25"), 4.25),
        (_probe([_audio(duration="N/A", tags={"DURATION": "garbage"})], "4.25"), 4.25),
        (_probe([_audio(duration="3.0")], "0.000000"), 3.0),
        (_probe([_audio(duration="0.000000")], "0.000000"), 0.0),
        (_probe([], "7.5"), 7.5),
        (
            _probe(
                [_audio(disposition={"default": 1}, **_EMPTY), _audio(duration="5.0")],
                "9.0",
            ),
            5.0,
        ),
    ],
    ids=[
        "stream",
        "tag-over-stream",
        "language-tag",
        "container",
        "zero-stream-falls-back",
        "negative-stream-falls-back",
        "unparseable-falls-back",
        "zero-container-loses",
        "zero-only-when-nothing-else",
        "no-streams-listed",
        "empty-default-track-passed-over",
    ],
)
def test_duration_is_the_audio_tracks(data: dict, expected: float) -> None:
    assert audio_duration_from_probe(data, "f") == pytest.approx(expected)


def test_no_reported_duration_is_an_error() -> None:
    with pytest.raises(RuntimeError, match="none reported") as excinfo:
        audio_duration_from_probe(_probe([_audio(duration="N/A")], "N/A"), "f")
    assert not isinstance(excinfo.value, NoAudioStreamError)


@pytest.mark.parametrize(
    ("streams", "error"),
    [
        ([{"codec_type": "video"}], NoAudioStreamError),
        ([_audio(tags={"DURATION": "00:00:00.000000000"})], NoAudioStreamError),
        ([_audio(channels=0, duration="20.0")], UnreadableAudioStreamError),
        (
            [_audio(disposition={"default": 1}, **_EMPTY), _audio(**_EMPTY)],
            NoAudioStreamError,
        ),
    ],
    ids=["video-only", "empty-track", "no-channels", "every-track-empty"],
)
def test_audio_nothing_can_use_is_refused(streams: list[dict], error: type) -> None:
    with pytest.raises(error):
        audio_duration_from_probe(_probe(streams, "20.0"), "f")


def _track(index: int, *, default: int, channels: int, **fields) -> dict:
    return _audio(
        index=index, channels=channels, disposition={"default": default}, **fields
    )


@pytest.mark.parametrize(
    ("streams", "chosen"),
    [
        ([_track(1, default=0, channels=2), _track(2, default=1, channels=2)], 2),
        ([_track(1, default=1, channels=1), _track(2, default=0, channels=2)], 1),
        ([_track(1, default=0, channels=1), _track(2, default=0, channels=2)], 2),
        ([_track(1, default=0, channels=2), _track(2, default=0, channels=2)], 1),
        (
            [
                _track(1, default=1, channels=2, **_EMPTY),
                _track(2, default=0, channels=1),
            ],
            2,
        ),
        ([_track(1, default=1, channels=0), _track(2, default=0, channels=1)], 2),
        (
            [
                _track(1, default=0, channels=2, **_EMPTY),
                _track(2, default=1, channels=2, **_EMPTY),
            ],
            2,
        ),
    ],
    ids=[
        "default-wins",
        "default-beats-channels",
        "most-channels",
        "first-on-tie",
        "audio-beats-empty-default",
        "audio-beats-unread-default",
        "default-among-empty",
    ],
)
def test_the_timed_track_is_the_one_ffmpeg_selects(
    streams: list[dict], chosen: int
) -> None:
    """ffmpeg's automatic mapping, checked against ffmpeg 6.1 and 8.0."""
    selected = decoded_audio_stream([{"codec_type": "video"}, *streams])
    assert selected is not None
    assert selected["index"] == chosen


def test_a_hung_ffprobe_is_ended_and_reported(monkeypatch) -> None:
    seen: dict = {}

    def hung(cmd, **kwargs):
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(audio.subprocess, "run", hung)

    with pytest.raises(RuntimeError, match="Failed to get audio duration"):
        audio.get_audio_duration("meeting.mkv")
    assert seen["timeout"] == FFPROBE_TIMEOUT_S


def test_a_hung_analysis_probe_is_ended(monkeypatch) -> None:
    """/info and the upload bitrate floor probe through analyze_audio_file."""
    seen: dict = {}

    def hung(cmd, **kwargs):
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(audio_preprocessing.subprocess, "run", hung)

    assert audio_preprocessing.analyze_audio_file("meeting.mkv") is None
    assert seen["timeout"] == FFPROBE_TIMEOUT_S
