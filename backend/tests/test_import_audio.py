"""What an import keeps: one audio track, in a format the pipeline reads.

Fixtures are generated with ffmpeg's built-in sources and encoders; the ffmpeg
cases skip where ffmpeg is not installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.utils import import_audio
from backend.utils.import_audio import (
    PROBE_TIMEOUT_S,
    AudioExtractionError,
    NoAudioStreamError,
    decoded_audio_stream,
    keep_imported_audio,
)

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg is not installed"
)

_VIDEO = ["-f", "lavfi", "-i", "testsrc=size=64x64:rate=25:duration=2"]
_TONE = ["-f", "lavfi", "-i", "sine=frequency=440:duration=2:sample_rate=48000"]


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def _streams(path: Path | str) -> list[dict]:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries"]
        + ["stream=codec_type,codec_name,channels:format=duration"]
        + ["-of", "json", str(path)],
        capture_output=True,
        check=True,
    )
    data = json.loads(probe.stdout)
    return [
        {**s, "duration": float(data["format"]["duration"])} for s in data["streams"]
    ]


def _audio(**fields) -> dict:
    return {"codec_type": "audio", "channels": 2, **fields}


def _track(index: int, *, default: int, channels: int, **fields) -> dict:
    return _audio(
        index=index, channels=channels, disposition={"default": default}, **fields
    )


# A Matroska track that holds no packets.
_EMPTY = {"tags": {"DURATION": "00:00:00.000000000"}}


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
def test_the_kept_track_is_the_one_ffmpeg_selects(
    streams: list[dict], chosen: int
) -> None:
    """ffmpeg's automatic mapping, checked against ffmpeg 6.1 and 8.0."""
    selected = decoded_audio_stream([{"codec_type": "video"}, *streams])
    assert selected is not None
    assert selected["index"] == chosen


# (fixture output arguments, suffix, stored suffix, stored codec, channels)
_EXTRACTIONS = [
    (["-c:v", "mpeg4", "-c:a", "aac"], ".mkv", ".m4a", "aac", 1),
    (["-c:v", "mpeg4", "-c:a", "aac"], ".ts", ".m4a", "aac", 1),
    (["-c:v", "mpeg4", "-c:a", "libmp3lame"], ".avi", ".mp3", "mp3", 1),
    (["-c:v", "mpeg4", "-c:a", "libopus"], ".mkv", ".webm", "opus", 1),
    (["-c:v", "mpeg4", "-c:a", "libvorbis"], ".mkv", ".ogg", "vorbis", 1),
    (["-c:a", "flac"], ".mka", ".flac", "flac", 1),
    (["-c:v", "mpeg4", "-c:a", "pcm_s24le"], ".mov", ".flac", "flac", 1),
    (["-c:v", "mpeg2video", "-c:a", "mp2"], ".mpg", ".webm", "opus", 1),
    # Cameras write AC-3 5.1(side), a layout libopus refuses: kept as stereo.
    (
        ["-c:v", "mpeg4", "-af", "pan=5.1(side)|c0=c0|c1=c0|c2=c0|c3=c0|c4=c0|c5=c0"]
        + ["-c:a", "ac3", "-f", "mpegts"],
        ".mts",
        ".webm",
        "opus",
        2,
    ),
    # An upload format that carries video is stored as audio too.
    (["-c:v", "mpeg4", "-c:a", "aac"], ".mp4", ".m4a", "aac", 1),
]


@needs_ffmpeg
@pytest.mark.parametrize(
    "case", _EXTRACTIONS, ids=[f"{row[1]}-{row[3]}" for row in _EXTRACTIONS]
)
def test_the_audio_track_replaces_the_upload(tmp_path: Path, case: tuple) -> None:
    output_args, suffix, stored_suffix, codec, channels = case
    source = tmp_path / f"upload{suffix}"
    inputs = [*_VIDEO, *_TONE] if "-c:v" in output_args else _TONE
    _ffmpeg(*inputs, *output_args, "-shortest", str(source))

    stored = Path(keep_imported_audio(str(source)))

    assert not source.exists()
    assert stored.parent == tmp_path
    assert stored.suffix == stored_suffix
    [stream] = _streams(stored)
    assert (stream["codec_type"], stream["codec_name"]) == ("audio", codec)
    assert stream["channels"] == channels
    assert stream["duration"] == pytest.approx(2.0, abs=0.1)
    assert sorted(tmp_path.iterdir()) == [stored]


@needs_ffmpeg
@pytest.mark.parametrize(
    "fixture",
    [
        [*_TONE, "-c:a", "libmp3lame", "upload.mp3"],
        # Cover art is a video stream flagged attached_pic, not video.
        [
            *_TONE,
            *["-f", "lavfi", "-i", "color=red:size=32x32:duration=1"],
            *["-map", "0", "-map", "1", "-frames:v", "1", "-c:a", "libmp3lame"],
            *["-c:v", "png", "-disposition:v", "attached_pic", "cover.mp3"],
        ],
        [*_TONE, "-c:a", "aac", "upload.m4a"],
    ],
    ids=["mp3", "mp3-with-cover-art", "m4a"],
)
def test_an_audio_file_is_kept_as_uploaded(tmp_path: Path, fixture: list[str]) -> None:
    source = tmp_path / fixture[-1]
    _ffmpeg(*fixture[:-1], str(source))
    before = source.read_bytes()

    assert keep_imported_audio(str(source)) == str(source)
    assert source.read_bytes() == before


@needs_ffmpeg
def test_of_two_audio_tracks_the_default_one_is_kept(tmp_path: Path) -> None:
    """OBS can record a track per source. The second, flagged default, is kept."""
    source = tmp_path / "obs.mkv"
    _ffmpeg(
        *_VIDEO,
        *["-f", "lavfi", "-i", "sine=frequency=440:duration=3"],
        *["-f", "lavfi", "-i", "sine=frequency=880:duration=5"],
        *["-map", "0:v", "-map", "1:a", "-map", "2:a", "-c:v", "mpeg4", "-c:a", "aac"],
        *["-disposition:a:0", "0", "-disposition:a:1", "default"],
        str(source),
    )

    [stream] = _streams(keep_imported_audio(str(source)))

    assert stream["duration"] == pytest.approx(5.0, abs=0.1)


@needs_ffmpeg
def test_a_file_with_only_video_is_refused_and_left_to_the_caller(
    tmp_path: Path,
) -> None:
    source = tmp_path / "screen.mkv"
    _ffmpeg(*_VIDEO, "-c:v", "mpeg4", str(source))

    with pytest.raises(NoAudioStreamError):
        keep_imported_audio(str(source))

    assert sorted(tmp_path.iterdir()) == [source]


@needs_ffmpeg
def test_a_failed_extraction_is_refused_and_leaves_nothing_new(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ffmpeg exits with an error: the partial file goes, the upload stays."""
    source = tmp_path / "screen.mkv"
    _ffmpeg(*_VIDEO, *_TONE, "-c:v", "mpeg4", "-c:a", "aac", str(source))
    monkeypatch.setattr(
        import_audio,
        "_codec_arguments",
        lambda track: (".m4a", ["-c:a", "no_such_encoder"]),
    )

    with pytest.raises(AudioExtractionError, match="no_such_encoder"):
        keep_imported_audio(str(source))

    assert sorted(tmp_path.iterdir()) == [source]


@needs_ffmpeg
def test_an_extraction_shorter_than_its_track_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ffmpeg exits 0, but the file it wrote does not hold the whole track."""
    source = tmp_path / "screen.mkv"
    _ffmpeg(*_VIDEO, *_TONE, "-c:v", "mpeg4", "-c:a", "aac", str(source))
    monkeypatch.setattr(
        import_audio,
        "_codec_arguments",
        lambda track: (".m4a", ["-c:a", "copy", "-t", "0.5"]),
    )

    with pytest.raises(AudioExtractionError, match="source track runs 2"):
        keep_imported_audio(str(source))

    assert sorted(tmp_path.iterdir()) == [source]


def _hung(seen: dict):
    def run(cmd, **kwargs):
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    return run


def test_a_container_ffprobe_cannot_read_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Its audio cannot be extracted, and a container is never stored."""
    source = tmp_path / "screen.mkv"
    source.write_bytes(b"not a matroska file")
    seen: dict = {}
    monkeypatch.setattr(import_audio.subprocess, "run", _hung(seen))

    with pytest.raises(AudioExtractionError):
        keep_imported_audio(str(source))
    assert seen["timeout"] == PROBE_TIMEOUT_S
    assert source.exists()


def test_an_audio_file_ffprobe_cannot_read_is_kept_as_before(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "meeting.mp3"
    source.write_bytes(b"not an mp3")
    monkeypatch.setattr(import_audio.subprocess, "run", _hung({}))

    assert keep_imported_audio(str(source)) == str(source)
