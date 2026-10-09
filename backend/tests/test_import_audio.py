"""What an import keeps: no video, one audio track, a format the pipeline reads.

Fixtures are generated with ffmpeg's built-in sources and encoders; the ffmpeg
cases skip where ffmpeg is not installed, as the suite's other media tests do.
"""

from __future__ import annotations

import json
import os
import random
import resource
import select
import shutil
import signal
import subprocess
from pathlib import Path

import pytest

from backend.utils import import_audio
from backend.utils.audio import get_audio_duration
from backend.utils.import_audio import (
    AudioExtractionError,
    ImportServerError,
    NoAudioStreamError,
    keep_imported_audio,
)
from backend.utils.import_audio_probe import (
    ToolFailure,
    decoded_audio_stream,
    track_span,
)

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg is not installed"
)

_VIDEO = ["-f", "lavfi", "-i", "testsrc=size=64x64:rate=25:duration=2"]
_TONE = ["-f", "lavfi", "-i", "sine=frequency=440:duration=2:sample_rate=48000"]
_SCREEN = [*_VIDEO, *_TONE, "-c:v", "mpeg4", "-c:a", "aac"]


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


_SIXTEEN_CHANNELS = "pan=16c|" + "|".join(f"c{i}=c0" for i in range(16))

# (fixture output arguments, suffix, stored suffix, stored codec, channels)
_EXTRACTIONS = [
    (["-c:v", "mpeg4", "-c:a", "aac"], ".mkv", ".m4a", "aac", 1),
    (["-c:v", "mpeg4", "-c:a", "aac"], ".ts", ".m4a", "aac", 1),
    (["-c:v", "mpeg4", "-c:a", "libmp3lame"], ".avi", ".mp3", "mp3", 1),
    (["-c:v", "mpeg4", "-c:a", "libopus"], ".mkv", ".webm", "opus", 1),
    (["-c:v", "mpeg4", "-c:a", "libvorbis"], ".mkv", ".ogg", "vorbis", 1),
    (["-c:a", "flac"], ".mka", ".flac", "flac", 1),
    (["-c:v", "mpeg4", "-c:a", "pcm_s24le"], ".mov", ".flac", "flac", 1),
    # FLAC holds at most 8 channels: a field recorder's 16 are kept as stereo.
    (["-af", _SIXTEEN_CHANNELS, "-c:a", "pcm_s16le"], ".mka", ".flac", "flac", 2),
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
    # SMPTE 302M, the PCM of broadcast MPEG-TS, is lossless: kept as FLAC.
    (
        ["-c:v", "mpeg2video", "-ac", "2", "-c:a", "s302m", "-strict", "-2"]
        + ["-f", "mpegts"],
        ".ts",
        ".flac",
        "flac",
        2,
    ),
]


@needs_ffmpeg
@pytest.mark.parametrize(
    "case",
    _EXTRACTIONS,
    ids=[f"{row[1]}-{row[3]}-{row[4]}ch" for row in _EXTRACTIONS],
)
def test_the_audio_track_replaces_the_upload(tmp_path: Path, case: tuple) -> None:
    output_args, suffix, stored_suffix, codec, channels = case
    source = tmp_path / f"upload{suffix}"
    inputs = [*_VIDEO, *_TONE] if "-c:v" in output_args else _TONE
    _ffmpeg(*inputs, *output_args, "-shortest", str(source))

    stored = Path(keep_imported_audio(str(source)).path)

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
    ("suffix", "audio_args"),
    [
        (".mkv", ["-c:a", "aac"]),
        (".mkv", ["-c:a", "libmp3lame"]),
        (".mkv", ["-c:a", "libvorbis"]),
        (".mkv", ["-c:a", "flac"]),
        (".mkv", ["-c:a", "pcm_s16le"]),
        (".mkv", ["-c:a", "libopus"]),
        (".avi", ["-c:a", "libmp3lame"]),
    ],
    ids=["mkv-aac", "mkv-mp3", "mkv-vorbis", "mkv-flac", "mkv-pcm", "mkv-opus", "avi"],
)
def test_audio_that_starts_late_is_kept_whole(
    tmp_path: Path, suffix: str, audio_args: list[str]
) -> None:
    """3 s of audio starting 3 s into a 6 s video.

    Matroska's DURATION tag (6 s) is the track's end, not its length, and an
    AVI header counts the video's length; the packets hold 3 s, as does the
    stored file, which starts at zero.
    """
    source = tmp_path / f"late{suffix}"
    _ffmpeg(
        *["-f", "lavfi", "-i", "testsrc=size=64x64:rate=25:duration=6"],
        *["-itsoffset", "3", "-f", "lavfi", "-i", "sine=duration=3:sample_rate=48000"],
        *["-map", "0:v", "-map", "1:a", "-c:v", "mpeg4", *audio_args, str(source)],
    )

    stored = keep_imported_audio(str(source)).path

    assert get_audio_duration(stored) == pytest.approx(3.0, abs=0.1)


def _unfinalised_mkv(path: Path) -> None:
    """An MKV written to a pipe, as a recorder that crashed leaves it:
    no duration, no cues, no DURATION tags."""
    with path.open("wb") as out:
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", *_SCREEN, "-f", "matroska", "pipe:1"],
            stdout=out,
            check=True,
        )


@needs_ffmpeg
def test_a_file_that_reports_no_length_is_measured_from_its_packets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "crashed.mkv"
    _unfinalised_mkv(source)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream_tags"]
        + ["-of", "json", str(source)],
        capture_output=True,
        check=True,
    )
    reported = json.loads(probe.stdout)
    assert "duration" not in reported["format"]
    assert all("DURATION" not in s.get("tags", {}) for s in reported["streams"])

    stored = keep_imported_audio(str(source)).path
    assert get_audio_duration(stored) == pytest.approx(2.0, abs=0.1)

    _unfinalised_mkv(source)
    _cut_extraction_short(monkeypatch)
    with pytest.raises(AudioExtractionError, match="source track runs 2"):
        keep_imported_audio(str(source))


def _cut_extraction_short(monkeypatch: pytest.MonkeyPatch) -> None:
    """ffmpeg exits 0, but writes only the first half second of the track."""
    monkeypatch.setattr(
        import_audio,
        "_output_plan",
        lambda track: import_audio._OutputPlan(
            ".m4a", ["-c:a", "copy", "-t", "0.5"], reencodes_lossy=False
        ),
    )


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
        # Two audio tracks and no video: stored whole, as import always has.
        [
            *_TONE,
            *["-f", "lavfi", "-i", "sine=frequency=880:duration=2"],
            *["-map", "0:a", "-map", "1:a", "-c:a", "aac", "two-tracks.m4a"],
        ],
    ],
    ids=["mp3", "mp3-with-cover-art", "m4a", "m4a-two-tracks"],
)
def test_an_audio_file_is_kept_as_uploaded(tmp_path: Path, fixture: list[str]) -> None:
    source = tmp_path / fixture[-1]
    _ffmpeg(*fixture[:-1], str(source))
    before = source.read_bytes()

    assert keep_imported_audio(str(source)).path == str(source)
    assert source.read_bytes() == before


@needs_ffmpeg
def test_of_two_audio_tracks_in_a_container_the_default_one_is_kept(
    tmp_path: Path,
) -> None:
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

    [stream] = _streams(keep_imported_audio(str(source)).path)

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
    """ffmpeg rejects the job: the partial file goes, the upload stays."""
    source = tmp_path / "screen.mkv"
    _ffmpeg(*_SCREEN, str(source))
    monkeypatch.setattr(
        import_audio,
        "_output_plan",
        lambda track: import_audio._OutputPlan(
            ".m4a", ["-c:a", "no_such_encoder"], reencodes_lossy=False
        ),
    )

    with pytest.raises(AudioExtractionError, match="no_such_encoder"):
        keep_imported_audio(str(source))

    assert sorted(tmp_path.iterdir()) == [source]


@needs_ffmpeg
def test_an_extraction_shorter_than_its_track_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "screen.mkv"
    _ffmpeg(*_SCREEN, str(source))
    _cut_extraction_short(monkeypatch)

    with pytest.raises(AudioExtractionError, match="source track runs 2"):
        keep_imported_audio(str(source))

    assert sorted(tmp_path.iterdir()) == [source]


def _limit_ffmpeg_output(monkeypatch: pytest.MonkeyPatch, max_bytes: int) -> None:
    """Run ffmpeg under a file-size limit, as a full disk quota would stop it."""
    real_run = subprocess.run

    def limit() -> None:
        resource.setrlimit(resource.RLIMIT_FSIZE, (max_bytes, max_bytes))

    def run(cmd, **kwargs):
        if cmd[0] == "ffmpeg":
            kwargs["preexec_fn"] = limit
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(import_audio.subprocess, "run", run)


@needs_ffmpeg
def test_a_full_disk_is_a_server_failure_not_a_bad_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "screen.mkv"
    _ffmpeg(*_SCREEN, str(source))
    _limit_ffmpeg_output(monkeypatch, 1_000)

    with pytest.raises(ImportServerError):
        keep_imported_audio(str(source))

    assert sorted(tmp_path.iterdir()) == [source]


@needs_ffmpeg
def test_an_ffmpeg_out_of_space_error_is_a_server_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "screen.mkv"
    _ffmpeg(*_SCREEN, str(source))
    real_run = subprocess.run

    def run(cmd, **kwargs):
        if cmd[0] == "ffmpeg":
            raise subprocess.CalledProcessError(
                1, cmd, stderr=b"av_interleaved_write_frame(): No space left on device"
            )
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(import_audio.subprocess, "run", run)

    with pytest.raises(ImportServerError):
        keep_imported_audio(str(source))


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError(2, "No such file or directory", "ffprobe"),
        subprocess.TimeoutExpired("ffprobe", 60),
    ],
    ids=["ffprobe-missing", "ffprobe-hung"],
)
@pytest.mark.parametrize("name", ["screen.mkv", "meeting.m4a"])
def test_ffprobe_failing_to_run_is_a_server_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: Exception, name: str
) -> None:
    source = tmp_path / name
    source.write_bytes(b"audio")

    def run(cmd, **kwargs):
        raise failure

    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(ImportServerError):
        keep_imported_audio(str(source))
    assert source.exists()


@needs_ffmpeg
def test_a_container_ffprobe_cannot_read_is_refused(tmp_path: Path) -> None:
    """Its audio cannot be extracted, and a container is never stored."""
    source = tmp_path / "screen.mkv"
    source.write_bytes(b"not a matroska file")

    with pytest.raises(AudioExtractionError):
        keep_imported_audio(str(source))
    assert source.exists()


@needs_ffmpeg
def test_an_audio_file_ffprobe_cannot_read_is_kept_as_before(tmp_path: Path) -> None:
    source = tmp_path / "meeting.m4a"
    source.write_bytes(b"not an m4a")

    assert keep_imported_audio(str(source)).path == str(source)


@needs_ffmpeg
def test_an_upload_that_cannot_be_removed_leaves_no_extracted_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A server failure; the caller only knows the upload's path, so the new
    file goes too."""
    source = tmp_path / "screen.mkv"
    _ffmpeg(*_SCREEN, str(source))
    real_remove = import_audio.os.remove

    def remove(path):
        if path == str(source):
            raise PermissionError(13, "Permission denied", path)
        real_remove(path)

    monkeypatch.setattr(import_audio.os, "remove", remove)

    with pytest.raises(ImportServerError):
        keep_imported_audio(str(source))

    assert sorted(tmp_path.iterdir()) == [source]


@needs_ffmpeg
def test_a_source_whose_packets_undercount_is_not_refused(tmp_path: Path) -> None:
    """WavPack's last block in MKV reports no duration, so the source's span
    runs about a second short of the correct output; only a shorter output
    is refused. WavPack is lossless and is kept as FLAC."""
    source = tmp_path / "recorder.mkv"
    _ffmpeg(
        *["-f", "lavfi", "-i", "testsrc=size=64x64:rate=25:duration=10"],
        *["-f", "lavfi", "-i", "sine=duration=10:sample_rate=48000"],
        *["-c:v", "mpeg4", "-c:a", "wavpack", "-shortest", str(source)],
    )

    kept = keep_imported_audio(str(source))

    assert kept.path.endswith(".flac")
    assert get_audio_duration(kept.path) == pytest.approx(10.0, abs=0.1)


@needs_ffmpeg
def test_concatenated_mpeg_ts_clips_are_kept_whole(tmp_path: Path) -> None:
    """``cat a.ts b.ts``: ffmpeg mends the restarting timestamps, ffprobe does
    not, so the source looks half as long as the correct output."""
    clips = []
    for name in ("a.ts", "b.ts"):
        clip = tmp_path / name
        _ffmpeg(*_VIDEO, *_TONE, "-c:v", "mpeg4", "-c:a", "aac", str(clip))
        clips.append(clip)
    source = tmp_path / "joined.ts"
    source.write_bytes(b"".join(clip.read_bytes() for clip in clips))

    stored = keep_imported_audio(str(source)).path

    assert get_audio_duration(stored) == pytest.approx(4.0, abs=0.2)


@needs_ffmpeg
def test_ten_minutes_losing_four_seconds_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The tolerance is max(1 s, 0.1%): 0.6 s on ten minutes, so a 4 s loss
    is caught (at 1% it would pass)."""
    source = tmp_path / "long.mka"
    _ffmpeg(
        *["-f", "lavfi", "-i", "sine=duration=600:sample_rate=16000"],
        *["-c:a", "aac", "-b:a", "32k", str(source)],
    )
    monkeypatch.setattr(
        import_audio,
        "_output_plan",
        lambda track: import_audio._OutputPlan(
            ".m4a", ["-c:a", "copy", "-t", "596"], reencodes_lossy=False
        ),
    )

    with pytest.raises(AudioExtractionError, match="source track runs 600"):
        keep_imported_audio(str(source))


@needs_ffmpeg
def test_mp2_labelled_mp3_in_mp4_is_re_encoded_not_stored_as_mp3(
    tmp_path: Path,
) -> None:
    """ffprobe names MPEG-1 Layer II in MP4 "mp3"; copied into ``.mp3`` it
    would hold MP2 frames, so it goes down the Opus path instead."""
    source = tmp_path / "camera.mp4"
    _ffmpeg(
        *_VIDEO, *_TONE, "-c:v", "mpeg4", "-c:a", "mp2", "-b:a", "192k", str(source)
    )

    kept = keep_imported_audio(str(source))

    assert kept.path.endswith(".webm")
    assert [s["codec_name"] for s in _streams(kept.path)] == ["opus"]
    assert sorted(tmp_path.iterdir()) == [Path(kept.path)]


@needs_ffmpeg
def test_a_truncated_file_with_intact_tags_is_measured_by_a_full_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Its tags say 120 s, its packets stop near 40 s: the tail window finds
    nothing, so the whole stream is read and a short extraction is caught."""
    full = tmp_path / "full.mka"
    _ffmpeg(
        *["-f", "lavfi", "-i", "sine=duration=120:sample_rate=16000"],
        *["-c:a", "aac", "-b:a", "32k", str(full)],
    )
    source = tmp_path / "cut.mka"
    data = full.read_bytes()
    source.write_bytes(data[: len(data) // 3])
    full.unlink()
    _cut_extraction_short(monkeypatch)

    with pytest.raises(AudioExtractionError, match="where the source track runs"):
        keep_imported_audio(str(source))


def _fail_with_signal(monkeypatch: pytest.MonkeyPatch, tool: str, signum: int) -> None:
    real_run = subprocess.run

    def run(cmd, **kwargs):
        if cmd[0] == tool:
            raise subprocess.CalledProcessError(-signum, cmd, stderr=b"")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)


@needs_ffmpeg
@pytest.mark.parametrize("tool", ["ffprobe", "ffmpeg"])
@pytest.mark.parametrize(
    ("signum", "error"),
    [
        (signal.SIGSEGV, AudioExtractionError),
        (signal.SIGABRT, AudioExtractionError),
        (signal.SIGKILL, ImportServerError),
        (signal.SIGXFSZ, ImportServerError),
    ],
    ids=["segv", "abort", "kill", "xfsz"],
)
def test_a_crash_is_the_files_fault_and_a_kill_is_the_servers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tool: str,
    signum: int,
    error: type,
) -> None:
    source = tmp_path / "screen.mkv"
    _ffmpeg(*_SCREEN, str(source))
    _fail_with_signal(monkeypatch, tool, signum)

    with pytest.raises(error):
        keep_imported_audio(str(source))
    assert sorted(tmp_path.iterdir()) == [source]


def _decoded_bytes(path: Path) -> int:
    """How much 16-bit mono PCM the file's audio decodes to."""
    decoded = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a"]
        + ["-ac", "1", "-f", "s16le", "-"],
        capture_output=True,
        check=True,
    )
    return len(decoded.stdout)


@needs_ffmpeg
def test_a_copied_track_decodes_to_the_source_samples(tmp_path: Path) -> None:
    """AAC's encoder delay, declared by the MP4 edit list, survives the copy;
    shifting it to zero would add 1,024 samples of priming."""
    source = tmp_path / "screen.mp4"
    _ffmpeg(*_SCREEN, "-shortest", str(source))
    expected = _decoded_bytes(source)

    stored = Path(keep_imported_audio(str(source)).path)

    assert _decoded_bytes(stored) == expected


def _noisy_recording(path: Path) -> None:
    """Thirty seconds of 32 kb/s MP2 with one byte in 2,000 flipped: ffmpeg
    reports decode errors as it goes, from its first seconds on."""
    _ffmpeg(
        *["-f", "lavfi", "-i", "sine=frequency=440:duration=30"],
        *["-c:a", "mp2", "-b:a", "32k", str(path)],
    )
    data = bytearray(path.read_bytes())
    flips = random.Random(3)
    for _ in range(len(data) // 2000):
        data[flips.randrange(8192, len(data))] ^= 0xFF
    path.write_bytes(bytes(data))


# The test's bound on each wait for ffmpeg, which answers in milliseconds.
_STOP_WAIT_S = 5
# Less than a pipe holds (64 KiB on Linux), so writing it cannot block.
_PIPED_BYTES = 48 * 1024


def _first_report(process: subprocess.Popen) -> bytes:
    """What ffmpeg has written to stderr, waiting at most ``_STOP_WAIT_S``."""
    assert process.stderr is not None
    ready, _, _ = select.select([process.stderr], [], [], _STOP_WAIT_S)
    assert ready, "ffmpeg reported nothing about the noisy file"
    return os.read(process.stderr.fileno(), 65536)


@needs_ffmpeg
def test_ffmpeg_stopped_by_sigterm_is_a_server_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A service or container stopping sends SIGTERM, which ffmpeg traps: it
    exits 255, not -15, after whatever it reported about a noisy file. The
    file is not at fault.

    ffmpeg reads the start of the file from a pipe the test holds open, so it
    is still running, blocked on a read, when the signal goes. The input then
    ends, which is when ffmpeg 7.1 acts on the signal. Each wait is bounded.
    """
    source = tmp_path / "noisy.mka"
    _noisy_recording(source)
    piped = source.read_bytes()[:_PIPED_BYTES]
    real_run = subprocess.run
    stopped: list[subprocess.CalledProcessError] = []

    def run_until_stopped(cmd, **kwargs):
        if cmd[0] != "ffmpeg":
            return real_run(cmd, **kwargs)
        cmd = ["pipe:0" if arg == str(source) else arg for arg in cmd]
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            assert process.stdin is not None
            process.stdin.write(piped)
            process.stdin.flush()
            reported = _first_report(process)
            process.send_signal(signal.SIGTERM)
            # Closes ffmpeg's input, then waits for it to exit.
            stdout, stderr = process.communicate(timeout=_STOP_WAIT_S)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        stopped.append(
            subprocess.CalledProcessError(
                process.returncode, cmd, stdout, reported + stderr
            )
        )
        raise stopped[-1]

    monkeypatch.setattr(import_audio.subprocess, "run", run_until_stopped)

    with pytest.raises(ImportServerError):
        keep_imported_audio(str(source))
    [error] = stopped
    assert (error.returncode, bool(error.stderr.strip())) == (255, True)
    assert sorted(tmp_path.iterdir()) == [source]


@needs_ffmpeg
def test_a_packet_read_past_its_timeout_is_killed_as_a_server_failure(
    tmp_path: Path,
) -> None:
    """The streamed read runs under a watchdog. ffprobe blocked opening a pipe
    nobody writes to stands in for a hung read."""
    stalled = tmp_path / "stalled.mkv"
    os.mkfifo(stalled)

    with pytest.raises(ToolFailure, match="ran past"):
        track_span(str(stalled), 0, None, timeout=0.2)


@pytest.mark.parametrize(
    "name", ["meeting.wav", "meeting.MP3", "meeting.aac", "meeting.flac"]
)
def test_a_format_that_cannot_hold_video_is_kept_without_probing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str
) -> None:
    """Stored as uploaded, as before media containers were accepted, and with
    no ffprobe or ffmpeg needed (upstream CI installs neither)."""
    source = tmp_path / name
    source.write_bytes(b"audio")

    def run(cmd, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", cmd[0])

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "Popen", run)

    assert keep_imported_audio(str(source)) == import_audio.KeptAudio(str(source))
    assert source.read_bytes() == b"audio"
