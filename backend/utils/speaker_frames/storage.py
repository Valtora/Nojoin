"""Where screen-study frames live, and every way they leave.

Layout, under the data directory and apart from every recording, document and
backup path (PR 2's analysis reads it, so it is a contract):

- ``speaker_frames/<recording public_id>/<sequence>-<offset_ms>.jpg``, one file
  per frame, mode 0600 in directories of mode 0700;
- ``speaker_frames/<recording public_id>/index.jsonl`` beside them, one JSON
  object per frame, appended as it is stored;
- ``speaker_frame_state/<recording public_id>.json``: when the recording
  stopped, which starts its 14 days and its upload window, and whether its
  owner deleted its frames, after which none is ever kept again;
- ``speaker_frame_state/<recording public_id>.lock``, held while a frame is
  stored or the recording's frames are deleted, so the two never interleave;
- ``speaker_frame_hashes/``, the hash lists ``purge`` saves before it deletes.

``sequence`` is the audio chunk the frame was taken in and ``offset_ms`` how far
into that chunk, so ``recording_audio_chunks.absolute_start_ms + offset_ms`` puts
the frame on the transcript's clock.

Nothing here logs per frame: a capture's first stored frame in this process and
each deletion write one line, and neither carries any frame content.

The API keeps two caches. A recording's count and size are checked against its
directory's identity on every read, so a purge or sweep from another process
shows at once. The total behind the cap is rescanned at least once a minute,
so frames another process deletes free room within that minute.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from backend.utils.path_manager import path_manager

from .jpeg import InvalidFrame, jpeg_dimensions

logger = logging.getLogger(__name__)

FRAMES_DIRNAME = "speaker_frames"
STATE_DIRNAME = "speaker_frame_state"
HASHES_DIRNAME = "speaker_frame_hashes"
INDEX_FILENAME = "index.jsonl"

RETENTION = timedelta(days=14)
# New frames are refused once every recording's frames together pass this.
TOTAL_CAP_BYTES = 20 * 1024 * 1024 * 1024
# A 1920 px frame of a busy screen is a few hundred kB at quality 0.8, and
# one refused frame ends the study, so the cap leaves room.
MAX_FRAME_BYTES = 2 * 1024 * 1024
# The browser sends at most 1920 px on the long edge; this leaves room.
MAX_FRAME_EDGE_PX = 2560
# A frame's offset into its 2 s audio chunk. Generous, so a tick on a chunk's
# boundary is never refused, and small enough to catch a wrong field.
MAX_OFFSET_MS = 10_000
# How long after a recording stops its queued frames may still arrive. The
# browser drains for 20 s after Stop, behind any audio still uploading.
UPLOAD_WINDOW_AFTER_STOP = timedelta(seconds=60)
# How often the total behind the cap is rescanned. This process adds its own
# frames as it stores them, so between scans it misses only what another
# process wrote or deleted.
_TOTAL_CACHE_SECONDS = 60.0

_PUBLIC_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_FRAME_NAME = re.compile(r"^(?P<sequence>\d+)-(?P<offset>\d+)\.jpg$")

_DIR_MODE = 0o700
_FILE_MODE = 0o600


class InvalidRecordingId(ValueError):
    """Not a recording public id, so never a directory name."""


class StorageCapReached(RuntimeError):
    """Every recording's frames together have reached the total cap."""


class FramesDeleted(RuntimeError):
    """The recording's owner deleted its frames, so it takes none again."""


class FrameDeletionFailed(RuntimeError):
    """A recording's frames could not all be deleted."""


@dataclass(frozen=True)
class FrameSummary:
    frames: int
    bytes: int


@dataclass(frozen=True)
class StoredFrame:
    duplicate: bool
    summary: FrameSummary


@dataclass(frozen=True)
class FrameTiming:
    """How the browser sampled a frame, kept in the index for the analysis.

    ``tick`` counts the capture's audio ticks from 0, and ``lost_before`` how
    many ticks since the previous stored frame went unsampled, or had their
    kept frame dropped or fail to upload; an unchanged screen is not a loss.
    ``frame_ts_us`` is the VideoFrame's own timestamp and ``grab_delay_ms`` the
    time from the audio tick to drawing that frame.
    """

    tick: int
    lost_before: int
    frame_ts_us: int
    grab_delay_ms: int


@dataclass(frozen=True)
class IncomingFrame:
    """One frame as the browser sent it."""

    sequence: int
    offset_ms: int
    data: bytes
    received_at: datetime
    timing: FrameTiming


def utc_iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return (
        moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def is_public_id(value: str) -> bool:
    return bool(_PUBLIC_ID.match(value))


def _require_public_id(public_id: str) -> str:
    if not isinstance(public_id, str) or not _PUBLIC_ID.match(public_id):
        raise InvalidRecordingId("Not a recording id.")
    return public_id


def frames_root() -> Path:
    return Path(path_manager.user_data_directory) / FRAMES_DIRNAME


def state_root() -> Path:
    return Path(path_manager.user_data_directory) / STATE_DIRNAME


def hashes_root() -> Path:
    return Path(path_manager.user_data_directory) / HASHES_DIRNAME


def recording_frames_dir(public_id: str) -> Path:
    return frames_root() / _require_public_id(public_id)


def _state_path(public_id: str) -> Path:
    return state_root() / f"{_require_public_id(public_id)}.json"


def lock_path(public_id: str) -> Path:
    return state_root() / f"{_require_public_id(public_id)}.lock"


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
    # mkdir's mode is masked by the umask, and an existing directory keeps its own.
    os.chmod(path, _DIR_MODE)


def _write_private_file(path: Path, data: bytes) -> None:
    """Write ``data`` beside ``path`` and rename it into place, mode 0600."""
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _FILE_MODE)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


@contextlib.contextmanager
def recording_lock(public_id: str) -> Iterator[None]:
    """Holds the recording's lock file, across threads and processes."""
    _ensure_private_dir(state_root())
    fd = os.open(lock_path(public_id), os.O_RDWR | os.O_CREAT, _FILE_MODE)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


# --- the state file ----------------------------------------------------------


def _read_state(public_id: str) -> dict[str, Any]:
    try:
        payload = json.loads(_state_path(public_id).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _update_state(public_id: str, **changes: Any) -> None:
    _ensure_private_dir(state_root())
    state = _read_state(public_id)
    state.update(changes)
    _write_private_file(_state_path(public_id), json.dumps(state).encode("utf-8"))


def recording_stopped_at(public_id: str) -> datetime | None:
    return _parse_utc(_read_state(public_id).get("stopped_at"))


def frames_deleted_by_owner(public_id: str) -> bool:
    """Whether the owner deleted this recording's frames, after which none is kept."""
    return bool(_read_state(public_id).get("frames_deleted_at"))


def mark_recording_stopped(
    public_id: str, when: datetime, *, uploads_closed: bool = False
) -> bool:
    """Record when a recording with frames stopped. Never raises.

    Does nothing for a recording with no frames, which is every recording
    while the study is off. ``uploads_closed`` gives the stop time without its
    upload window, as the sweep does for a stop it learns of late. Returns
    whether it wrote.
    """
    try:
        if not recording_frames_dir(public_id).is_dir():
            return False
        # Under the lock, so it cannot write over an owner's Delete marker.
        with recording_lock(public_id):
            _update_state(
                public_id, stopped_at=utc_iso(when), uploads_closed=uploads_closed
            )
        return True
    except (InvalidRecordingId, OSError):
        logger.warning(
            "Could not record when recording %s stopped for the screen study",
            public_id,
            exc_info=True,
        )
        return False


def delete_after(public_id: str, *, now: datetime | None = None) -> datetime:
    """When a recording's frames go: 14 days after it stopped, or from now."""
    stopped = recording_stopped_at(public_id)
    return _aware(stopped or now or datetime.now(UTC)) + RETENTION


def accepts_frames_after_stop(public_id: str, *, now: datetime) -> bool:
    """Whether a stopped recording is still inside its upload window."""
    state = _read_state(public_id)
    stopped = _parse_utc(state.get("stopped_at"))
    if stopped is None or state.get("uploads_closed") or state.get("frames_deleted_at"):
        return False
    return _aware(now) - stopped <= UPLOAD_WINDOW_AFTER_STOP


# --- the caches -----------------------------------------------------------------


@dataclass
class _TotalCache:
    scanned_at: float | None = None
    bytes: int = 0


@dataclass(frozen=True)
class _CachedSummary:
    directory: tuple[int, int]
    summary: FrameSummary


_lock = threading.Lock()
_total = _TotalCache()
_summaries: dict[str, _CachedSummary] = {}
_announced: set[str] = set()


def _frame_files(directory: Path) -> Iterator[os.DirEntry[str]]:
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if _FRAME_NAME.match(entry.name) and entry.is_file(
                    follow_symlinks=False
                ):
                    yield entry
    except FileNotFoundError:
        return


def scan_summary(directory: Path) -> FrameSummary:
    """Count and size the frame files in ``directory``, read from disk."""
    count = 0
    size = 0
    for entry in _frame_files(directory):
        count += 1
        size += entry.stat(follow_symlinks=False).st_size
    return FrameSummary(frames=count, bytes=size)


def _directory_identity(directory: Path) -> tuple[int, int] | None:
    try:
        stat = directory.stat()
    except FileNotFoundError:
        return None
    return (stat.st_dev, stat.st_ino)


def _scan_total_bytes() -> int:
    total = 0
    try:
        with os.scandir(frames_root()) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    total += scan_summary(Path(entry.path)).bytes
    except FileNotFoundError:
        return 0
    return total


def total_bytes(*, refresh: bool = False) -> int:
    """Every recording's frames together, rescanned at least once a minute."""
    with _lock:
        if (
            not refresh
            and _total.scanned_at is not None
            and time.monotonic() - _total.scanned_at < _TOTAL_CACHE_SECONDS
        ):
            return _total.bytes
    measured = _scan_total_bytes()
    with _lock:
        _total.scanned_at = time.monotonic()
        _total.bytes = measured
    return measured


def _adjust_total(delta: int) -> None:
    with _lock:
        if _total.scanned_at is not None:
            _total.bytes = max(0, _total.bytes + delta)


def frames_summary(public_id: str) -> FrameSummary:
    """How many frames a recording has here, and their size on disk.

    Cached per directory, so a directory another process deleted or replaced
    is read afresh.
    """
    directory = recording_frames_dir(public_id)
    identity = _directory_identity(directory)
    if identity is None:
        with _lock:
            _summaries.pop(public_id, None)
        return FrameSummary(frames=0, bytes=0)
    with _lock:
        cached = _summaries.get(public_id)
        if cached is not None and cached.directory == identity:
            return cached.summary
    summary = scan_summary(directory)
    with _lock:
        _summaries[public_id] = _CachedSummary(directory=identity, summary=summary)
    return summary


# --- storing ---------------------------------------------------------------


def _check_frame(sequence: int, offset_ms: int, data: bytes) -> tuple[int, int]:
    if sequence < 0 or not 0 <= offset_ms <= MAX_OFFSET_MS:
        raise InvalidFrame("The frame's position in the recording is out of range.")
    if len(data) > MAX_FRAME_BYTES:
        raise InvalidFrame("The frame is larger than 2 MB.")
    width, height = jpeg_dimensions(data)
    if width > MAX_FRAME_EDGE_PX or height > MAX_FRAME_EDGE_PX:
        raise InvalidFrame("The frame is larger than 2560 pixels on a side.")
    return width, height


def _index_line(frame: IncomingFrame, size: tuple[int, int]) -> str:
    return json.dumps(
        {
            "sequence": frame.sequence,
            "offset_ms": frame.offset_ms,
            "received_at": utc_iso(frame.received_at),
            "bytes": len(frame.data),
            "sha256": hashlib.sha256(frame.data).hexdigest(),
            "width": size[0],
            "height": size[1],
            # Never set yet: the analysis works out the platform itself.
            "platform_hint": None,
            "tick": frame.timing.tick,
            "lost_before": frame.timing.lost_before,
            "frame_ts_us": frame.timing.frame_ts_us,
            "grab_delay_ms": frame.timing.grab_delay_ms,
        },
        separators=(",", ":"),
    )


def store_frame(public_id: str, frame: IncomingFrame) -> StoredFrame:
    """Keep one frame and add its line to the recording's index.

    Runs under the recording's lock, so it never interleaves with a delete. A
    frame already stored under the same sequence and offset, a retried upload,
    is left as it is and not indexed twice.

    Raises:
        InvalidRecordingId: ``public_id`` is not a recording id.
        InvalidFrame: the data is not a whole JPEG, or is over 2 MB or 2560 px a side.
        StorageCapReached: the total cap would be passed.
        FramesDeleted: the owner deleted this recording's frames.
    """
    directory = recording_frames_dir(public_id)
    size = _check_frame(frame.sequence, frame.offset_ms, frame.data)
    if total_bytes() + len(frame.data) > TOTAL_CAP_BYTES:
        raise StorageCapReached("The screen study has reached its storage limit.")

    with recording_lock(public_id):
        if frames_deleted_by_owner(public_id):
            raise FramesDeleted("This recording's screen study frames were deleted.")
        _ensure_private_dir(frames_root())
        _ensure_private_dir(directory)
        before = frames_summary(public_id)
        final = directory / f"{frame.sequence}-{frame.offset_ms}.jpg"
        if final.exists():
            return StoredFrame(duplicate=True, summary=before)
        _write_private_file(final, frame.data)
        index_fd = os.open(
            directory / INDEX_FILENAME,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            _FILE_MODE,
        )
        with os.fdopen(index_fd, "a", encoding="utf-8") as index:
            index.write(_index_line(frame, size) + "\n")
        summary = FrameSummary(
            frames=before.frames + 1, bytes=before.bytes + len(frame.data)
        )
        identity = _directory_identity(directory)
        with _lock:
            if identity is not None:
                _summaries[public_id] = _CachedSummary(identity, summary)
            first = public_id not in _announced
            _announced.add(public_id)

    _adjust_total(len(frame.data))
    if first:
        logger.info("Screen study frames are being stored for recording %s", public_id)
    return StoredFrame(duplicate=False, summary=summary)


# --- deleting --------------------------------------------------------------


def _remove_frames_locked(public_id: str) -> FrameSummary:
    """Delete the frames directory. Call under the recording's lock. Raises OSError."""
    directory = recording_frames_dir(public_id)
    removed = scan_summary(directory)
    try:
        shutil.rmtree(directory)
    except FileNotFoundError:
        pass
    with _lock:
        _summaries.pop(public_id, None)
        _announced.discard(public_id)
    _adjust_total(-removed.bytes)
    return removed


def delete_frames_for_owner(public_id: str) -> int:
    """The owner's Delete: remove the frames and refuse any frame from now on.

    The refusal is written in the state file under the recording's lock, so a
    frame still on its way when the owner deletes is refused, never kept.

    Raises:
        FrameDeletionFailed: a file could not be removed, so frames remain.
    """
    try:
        with recording_lock(public_id):
            _update_state(public_id, frames_deleted_at=utc_iso(datetime.now(UTC)))
            removed = _remove_frames_locked(public_id)
    except OSError as error:
        logger.error(
            "Could not delete the screen study frames of recording %s: %s",
            public_id,
            error,
        )
        raise FrameDeletionFailed(
            "The screen study frames could not all be deleted."
        ) from error
    logger.info(
        "Deleted %d screen study frames of recording %s (deleted by its owner)",
        removed.frames,
        public_id,
    )
    return removed.frames


def delete_recording_frames(public_id: str, *, reason: str) -> int:
    """Delete a recording's frames and its state. Never raises.

    For a recording that is going or has gone. Returns how many frames went.
    ``reason`` is a short fixed phrase for the one log line ("discarded",
    "retention" and so on), never anything a user typed.
    """
    try:
        directory = recording_frames_dir(public_id)
    except InvalidRecordingId:
        return 0
    if not directory.exists() and not _state_path(public_id).exists():
        return 0
    removed = FrameSummary(frames=0, bytes=0)
    try:
        with recording_lock(public_id):
            removed = _remove_frames_locked(public_id)
            _state_path(public_id).unlink(missing_ok=True)
    except OSError as error:
        if error.errno != errno.ENOENT:
            logger.warning(
                "Could not delete the screen study frames of recording %s: %s",
                public_id,
                error,
            )
    if removed.frames:
        logger.info(
            "Deleted %d screen study frames of recording %s (%s)",
            removed.frames,
            public_id,
            reason,
        )
    return removed.frames


def delete_frames_for_recordings(public_ids: list[str], *, reason: str) -> int:
    """``delete_recording_frames`` for each of ``public_ids``. Never raises."""
    total = 0
    for public_id in public_ids:
        total += delete_recording_frames(public_id, reason=reason)
    return total


async def delete_frames_off_loop(public_ids: list[str], *, reason: str) -> int:
    """``delete_frames_for_recordings`` on a worker thread, for the API's delete paths.

    Each path calls it once its own delete has committed. A recording with no
    frames costs two ``stat`` calls. Never raises.
    """
    if not public_ids:
        return 0
    return await asyncio.to_thread(
        delete_frames_for_recordings, public_ids, reason=reason
    )


# --- listing ---------------------------------------------------------------


def recording_dirs() -> list[Path]:
    """Every directory under the frames root, sorted by name."""
    try:
        return sorted(
            (Path(entry.path) for entry in os.scandir(frames_root()) if entry.is_dir()),
            key=lambda path: path.name,
        )
    except FileNotFoundError:
        return []


def state_files() -> list[Path]:
    try:
        return sorted(state_root().glob("*.json"))
    except OSError:
        return []


def lock_files() -> list[Path]:
    try:
        return sorted(state_root().glob("*.lock"))
    except OSError:
        return []


def frame_hashes(directory: Path) -> list[tuple[str, str]]:
    """``(sha256, file name)`` for each frame file in ``directory``, read from disk."""
    hashes: list[tuple[str, str]] = []
    for entry in sorted(_frame_files(directory), key=lambda item: item.name):
        digest = hashlib.sha256()
        with open(entry.path, "rb") as handle:
            for block in iter(lambda: handle.read(1 << 16), b""):
                digest.update(block)
        hashes.append((digest.hexdigest(), entry.name))
    return hashes


def _reset_caches() -> None:
    """Forget every cache, as a fresh process would. For tests."""
    with _lock:
        _summaries.clear()
        _announced.clear()
        _total.scanned_at = None
        _total.bytes = 0
