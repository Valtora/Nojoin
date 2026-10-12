"""ffprobe readings behind ``keep_imported_audio``: which track, and how long.

Kept apart from the extraction in ``import_audio`` so each stays readable.
"""

from __future__ import annotations

import json
import signal
import subprocess
import tempfile
import threading
from typing import Any

from backend.utils.audio import ensure_ffmpeg_in_path

# A header probe reads the start of the file, so this only ever ends a hung one.
PROBE_TIMEOUT_S = 60

PROBE_ENTRIES = (
    "format=duration"
    ":stream=index,codec_type,codec_name,channels,duration,start_time,bit_rate"
    ":stream_disposition=default,attached_pic"
    ":stream_tags"
)

# How much of a track's end ``track_span`` reads to find its last packet.
_TAIL_WINDOW_S = 30.0


# Signals that end ffprobe (or an ffmpeg that cannot trap them) from outside:
# the OOM killer (SIGKILL), a disk quota (SIGXFSZ), a container stopping
# (SIGTERM, which ffmpeg traps instead; see ``import_audio._is_server_fault``).
# Any other signal, such as SIGSEGV or SIGABRT, is the tool crashing on the
# file.
SERVER_SIGNALS = frozenset({signal.SIGKILL, signal.SIGXFSZ, signal.SIGTERM})


class ToolFailure(Exception):
    """ffprobe or ffmpeg did not finish for a reason on the server's side.

    It could not be started, ran past its timeout (and was killed for it), or
    was ended by one of ``SERVER_SIGNALS``. It says nothing about the file.
    """


class UnreadableMediaError(RuntimeError):
    """ffprobe could not read the file: it exited with an error or crashed."""


def killed_by_server_signal(returncode: int) -> bool:
    """The process was ended by one of ``SERVER_SIGNALS``."""
    return returncode < 0 and -returncode in SERVER_SIGNALS


def _failure(returncode: int, stderr: bytes, path: str) -> Exception:
    """The exception for ffprobe exiting with ``returncode`` on ``path``."""
    message = stderr.decode(errors="replace").strip()
    if killed_by_server_signal(returncode):
        return ToolFailure(f"ffprobe was ended ({returncode}) on {path}: {message}")
    return UnreadableMediaError(
        f"ffprobe could not read {path} ({returncode}): {message}"
    )


def run_ffprobe(arguments: list[str], path: str, *, timeout: float) -> dict[str, Any]:
    """Run ffprobe with ``arguments`` on ``path`` and return its JSON output.

    Raises:
        ToolFailure: ffprobe could not be started, timed out or was killed.
        UnreadableMediaError: ffprobe exited with an error or wrote no JSON.
    """
    ensure_ffmpeg_in_path()
    cmd = ["ffprobe", "-v", "error", *arguments, "-of", "json", path]
    try:
        result = subprocess.run(cmd, capture_output=True, check=True, timeout=timeout)
    except subprocess.CalledProcessError as exc:
        raise _failure(exc.returncode, exc.stderr or b"", path) from exc
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise ToolFailure(f"ffprobe did not finish on {path}: {exc}") from exc
    try:
        # Tag values are raw container metadata and need not be UTF-8.
        data = json.loads(result.stdout.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise UnreadableMediaError(f"ffprobe wrote no JSON for {path}") from exc
    if not isinstance(data, dict):
        raise UnreadableMediaError(f"ffprobe wrote no JSON object for {path}")
    return data


def probe_streams(path: str) -> dict[str, Any]:
    """The format duration and the streams of ``path`` (``PROBE_ENTRIES``)."""
    return run_ffprobe(["-show_entries", PROBE_ENTRIES], path, timeout=PROBE_TIMEOUT_S)


def seconds(value: object) -> float | None:
    """Parse an ffprobe time: seconds, or a Matroska ``HH:MM:SS.f`` tag."""
    if value in (None, "", "N/A"):
        return None
    text = str(value)
    try:
        if ":" in text:
            hours, minutes, rest = text.split(":")
            return int(hours) * 3600 + int(minutes) * 60 + float(rest)
        return float(text)
    except ValueError:
        return None


def _duration_tag(stream: dict) -> str | None:
    """A Matroska track's DURATION statistics tag.

    mkvmerge can write it with a language suffix, which ffprobe reports as a
    separate key such as ``DURATION-eng``.
    """
    for key, value in (stream.get("tags") or {}).items():
        name = key.upper()
        if name == "DURATION" or name.startswith("DURATION-"):
            return value
    return None


def is_empty_track(stream: dict) -> bool:
    """The stream's DURATION tag says the track holds no audio."""
    tagged = seconds(_duration_tag(stream))
    return tagged is not None and tagged <= 0


def has_audio(stream: dict) -> bool:
    """ffprobe read the stream's format and found the track non-empty.

    This stands in for ffmpeg's own test, whether the stream yielded packets
    while the input was probed, which ffprobe does not report.
    """
    return int(stream.get("channels") or 0) > 0 and not is_empty_track(stream)


def decoded_audio_stream(streams: list[dict]) -> dict | None:
    """The audio stream ffmpeg decodes when no stream is mapped explicitly.

    ffmpeg's automatic selection prefers a stream that has audio over one that
    is empty or unread, then the stream flagged default, then the one with the
    most channels, then the first.
    """
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if not audio:
        return None
    ranked = max(
        enumerate(audio),
        key=lambda item: (
            has_audio(item[1]),
            bool((item[1].get("disposition") or {}).get("default")),
            int(item[1].get("channels") or 0),
            -item[0],
        ),
    )
    return ranked[1]


def track_end(track: dict, probe: dict) -> float | None:
    """Where the headers say ``track``'s last packet ends, or None.

    Matroska's DURATION tag is the track's end timestamp, so it counts a late
    start; otherwise the stream's start plus its duration; otherwise the
    container's duration. Only a hint for where ``track_span`` should look.
    """
    for end in (
        seconds(_duration_tag(track)),
        _sum_or_none(track.get("start_time"), track.get("duration")),
        seconds((probe.get("format") or {}).get("duration")),
    ):
        if end is not None and end > 0:
            return end
    return None


def _sum_or_none(start: object, duration: object) -> float | None:
    length = seconds(duration)
    if length is None:
        return None
    return max(seconds(start) or 0.0, 0.0) + length


def track_span(
    path: str, stream_index: int, reported_end: float | None, *, timeout: float
) -> float | None:
    """Seconds from the start of a stream's first packet to the end of its last.

    Read from the packets, because a header length can count what is not
    there: a late start (Matroska's DURATION tag is an end timestamp) or the
    video's length (AVI). Reads the first packet and the last
    ``_TAIL_WINDOW_S`` before ``reported_end``. When no packet lands in that
    window, or no end is reported (an MKV whose recorder never finalised it),
    the whole stream is read, bounded by ``timeout``. ffprobe's output is read
    line by line and only the first start and the latest end are kept, so a
    full read of a long recording needs no more memory than a short one. None
    when the stream has no timed packets.

    Raises:
        ToolFailure, UnreadableMediaError: as ``run_ffprobe``.
    """
    arguments = ["-select_streams", str(stream_index)]
    arguments += ["-show_entries", "packet=pts_time,dts_time,duration_time"]
    if reported_end is not None:
        window_start = max(0.0, reported_end - _TAIL_WINDOW_S)
        intervals = ["-read_intervals", f"%+#1,{window_start:.3f}%"]
        first, end = _packet_bounds([*arguments, *intervals], path, PROBE_TIMEOUT_S)
        if first is not None and end is not None and end >= window_start:
            return end - first
    first, end = _packet_bounds(arguments, path, timeout)
    if first is None or end is None:
        return None
    return end - first


def _packet_bounds(
    arguments: list[str], path: str, timeout: float
) -> tuple[float | None, float | None]:
    """The first packet's start and the latest packet end ffprobe lists."""
    ensure_ffmpeg_in_path()
    cmd = ["ffprobe", "-v", "error", *arguments, "-of", "csv=p=0", path]
    first: float | None = None
    end: float | None = None
    with tempfile.TemporaryFile() as stderr:
        try:
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=stderr)
        except OSError as exc:
            raise ToolFailure(f"ffprobe could not be started: {exc}") from exc
        timed_out = threading.Event()

        def stop() -> None:
            timed_out.set()
            process.kill()

        watchdog = threading.Timer(timeout, stop)
        watchdog.start()
        try:
            with process:
                for line in process.stdout or ():
                    at, finish = _packet_times(line)
                    if at is None:
                        continue
                    if first is None:
                        first = at
                    end = finish if end is None else max(end, finish)
        finally:
            watchdog.cancel()
        if timed_out.is_set():
            raise ToolFailure(f"ffprobe ran past {timeout} s on {path}; killed")
        if process.returncode != 0:
            stderr.seek(0)
            raise _failure(process.returncode, stderr.read(), path)
    return first, end


def _packet_times(line: bytes) -> tuple[float | None, float]:
    """A csv packet line's start (pts, else dts) and end."""
    fields = line.decode(errors="replace").strip().split(",")
    fields += [""] * (3 - len(fields))
    at = seconds(fields[0])
    if at is None:
        at = seconds(fields[1])
    if at is None:
        return None, 0.0
    return at, at + (seconds(fields[2]) or 0.0)
