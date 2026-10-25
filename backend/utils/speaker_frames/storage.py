"""Where screen-study frames live, and every way they leave.

Layout, under the data directory and apart from every recording, document and
backup path (PR 2's analysis reads it, so it is a contract):

- ``speaker_frames/<recording public_id>/<sequence>-<offset_ms>.jpg``, one file
  per frame, mode 0600 in directories of mode 0700;
- ``speaker_frames/<recording public_id>/index.jsonl`` beside them, one JSON
  object per frame, appended as it is stored;
- ``speaker_frame_state/<recording public_id>.json``, when the recording
  stopped, which starts its 14 days and closes its upload window;
- ``speaker_frame_hashes/``, the hash lists ``purge`` saves before it deletes.

``sequence`` is the audio chunk the frame was taken in and ``offset_ms`` how far
into that chunk, so ``recording_audio_chunks.absolute_start_ms + offset_ms`` puts
the frame on the transcript's clock.

Nothing here logs per frame: a capture's first stored frame in this process and
each deletion write one line, and neither carries any frame content.
"""

from __future__ import annotations

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
MAX_FRAME_BYTES = 1024 * 1024
# The browser sends 1280 px on the long edge; this leaves calibration room.
MAX_FRAME_EDGE_PX = 2560
# A frame's offset into its 2 s audio chunk. Generous, so a tick on a chunk's
# boundary is never refused, and small enough to catch a wrong field.
MAX_OFFSET_MS = 10_000
# How long after a recording stops its queued frames may still arrive. The
# browser drains for 20 s after Stop, behind any audio still uploading.
UPLOAD_WINDOW_AFTER_STOP = timedelta(seconds=60)
# How stale the cached total behind the cap may be. At 2 Hz and 1 MB a frame,
# a minute can overshoot the cap by at most 120 MB.
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


@dataclass(frozen=True)
class FrameSummary:
    frames: int
    bytes: int


@dataclass(frozen=True)
class StoredFrame:
    duplicate: bool
    summary: FrameSummary


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


# --- the cap ---------------------------------------------------------------

_lock = threading.Lock()
_total_cache: dict[str, float] = {}
_summaries: dict[str, FrameSummary] = {}
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


def _scan_summary(directory: Path) -> FrameSummary:
    count = 0
    size = 0
    for entry in _frame_files(directory):
        count += 1
        size += entry.stat(follow_symlinks=False).st_size
    return FrameSummary(frames=count, bytes=size)


def _scan_total_bytes() -> int:
    total = 0
    root = frames_root()
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    total += _scan_summary(Path(entry.path)).bytes
    except FileNotFoundError:
        return 0
    return total


def total_bytes(*, refresh: bool = False) -> int:
    """Every recording's frames together, from a cache at most a minute old."""
    with _lock:
        cached_at = _total_cache.get("at")
        if (
            not refresh
            and cached_at is not None
            and time.monotonic() - cached_at < _TOTAL_CACHE_SECONDS
        ):
            return int(_total_cache["bytes"])
    measured = _scan_total_bytes()
    with _lock:
        _total_cache["at"] = time.monotonic()
        _total_cache["bytes"] = float(measured)
    return measured


def _add_to_total(size: int) -> None:
    with _lock:
        if "bytes" in _total_cache:
            _total_cache["bytes"] += size


def _forget(public_id: str) -> None:
    with _lock:
        _summaries.pop(public_id, None)
        _announced.discard(public_id)
        _total_cache.pop("at", None)


# --- storing ---------------------------------------------------------------


def frames_summary(public_id: str) -> FrameSummary:
    """How many frames a recording has here, and their size on disk."""
    directory = recording_frames_dir(public_id)
    with _lock:
        cached = _summaries.get(public_id)
    if cached is not None:
        return cached
    summary = _scan_summary(directory)
    with _lock:
        _summaries.setdefault(public_id, summary)
        return _summaries[public_id]


def store_frame(
    public_id: str,
    *,
    sequence: int,
    offset_ms: int,
    data: bytes,
    received_at: datetime,
) -> StoredFrame:
    """Keep one frame and add its line to the recording's index.

    A frame already stored under the same sequence and offset, a retried upload,
    is left as it is and not indexed twice.

    Raises:
        InvalidRecordingId: ``public_id`` is not a recording id.
        InvalidFrame: ``data`` is not a whole JPEG, or is over 1 MB or 2560 px a side.
        StorageCapReached: the total cap would be passed.
    """
    directory = recording_frames_dir(public_id)
    if sequence < 0 or not 0 <= offset_ms <= MAX_OFFSET_MS:
        raise InvalidFrame("The frame's position in the recording is out of range.")
    if len(data) > MAX_FRAME_BYTES:
        raise InvalidFrame("The frame is larger than 1 MB.")
    width, height = jpeg_dimensions(data)
    if width > MAX_FRAME_EDGE_PX or height > MAX_FRAME_EDGE_PX:
        raise InvalidFrame("The frame is larger than 2560 pixels on a side.")
    if total_bytes() + len(data) > TOTAL_CAP_BYTES:
        raise StorageCapReached("The screen study has reached its storage limit.")

    _ensure_private_dir(frames_root())
    _ensure_private_dir(directory)
    # Primed before this frame lands, so the count below adds it exactly once.
    frames_summary(public_id)
    final = directory / f"{sequence}-{offset_ms}.jpg"
    temp = directory / f".{final.name}.{uuid.uuid4().hex}.tmp"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _FILE_MODE)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        try:
            # A link, not a rename, so of two copies of one frame racing only
            # the first is kept and indexed.
            os.link(temp, final)
        except FileExistsError:
            return StoredFrame(duplicate=True, summary=frames_summary(public_id))
    finally:
        temp.unlink(missing_ok=True)

    line = json.dumps(
        {
            "sequence": sequence,
            "offset_ms": offset_ms,
            "received_at": utc_iso(received_at),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "width": width,
            "height": height,
            "platform_hint": None,
        },
        separators=(",", ":"),
    )
    index_fd = os.open(
        directory / INDEX_FILENAME, os.O_WRONLY | os.O_CREAT | os.O_APPEND, _FILE_MODE
    )
    with os.fdopen(index_fd, "a", encoding="utf-8") as index:
        fcntl.flock(index, fcntl.LOCK_EX)
        index.write(line + "\n")

    _add_to_total(len(data))
    with _lock:
        current = _summaries.get(public_id) or FrameSummary(frames=0, bytes=0)
        summary = FrameSummary(
            frames=current.frames + 1, bytes=current.bytes + len(data)
        )
        _summaries[public_id] = summary
        first = public_id not in _announced
        _announced.add(public_id)
    if first:
        logger.info("Screen study frames are being stored for recording %s", public_id)
    return StoredFrame(duplicate=False, summary=summary)


# --- when the recording stopped ---------------------------------------------


def recording_stopped_at(public_id: str) -> datetime | None:
    try:
        payload = json.loads(_state_path(public_id).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return None
    if not isinstance(payload, dict):
        return None
    return _parse_utc(payload.get("stopped_at"))


def mark_recording_stopped(public_id: str, when: datetime) -> bool:
    """Record when a recording with frames stopped. Never raises.

    Does nothing for a recording with no frames, which is every recording
    while the study is off. Returns whether it wrote.
    """
    try:
        if not recording_frames_dir(public_id).is_dir():
            return False
        _ensure_private_dir(state_root())
        payload = json.dumps({"stopped_at": utc_iso(when)}).encode("utf-8")
        _write_private_file(_state_path(public_id), payload)
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
    start = stopped or (now or datetime.now(UTC))
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    return start + RETENTION


def accepts_frames_after_stop(public_id: str, *, now: datetime) -> bool:
    """Whether a stopped recording is still inside its upload window."""
    stopped = recording_stopped_at(public_id)
    if stopped is None:
        return False
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now - stopped <= UPLOAD_WINDOW_AFTER_STOP


# --- deleting --------------------------------------------------------------


def delete_recording_frames(public_id: str, *, reason: str) -> int:
    """Delete a recording's frames and its state. Never raises.

    Returns how many frames went. ``reason`` is a short fixed word for the one
    log line ("discarded", "retention" and so on), never anything a user typed.
    """
    try:
        directory = recording_frames_dir(public_id)
    except InvalidRecordingId:
        return 0
    removed = 0
    try:
        if directory.is_dir():
            removed = _scan_summary(directory).frames
            shutil.rmtree(directory)
        _state_path(public_id).unlink(missing_ok=True)
    except OSError as error:
        if error.errno != errno.ENOENT:
            logger.warning(
                "Could not delete the screen study frames of recording %s: %s",
                public_id,
                error,
            )
    finally:
        _forget(public_id)
    if removed:
        logger.info(
            "Deleted %d screen study frames of recording %s (%s)",
            removed,
            public_id,
            reason,
        )
    return removed


def delete_frames_for_recordings(public_ids: list[str], *, reason: str) -> int:
    """``delete_recording_frames`` for each of ``public_ids``. Never raises."""
    total = 0
    for public_id in public_ids:
        total += delete_recording_frames(public_id, reason=reason)
    return total


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


def scan_summary(directory: Path) -> FrameSummary:
    return _scan_summary(directory)


async def delete_frames_off_loop(public_ids: list[str], *, reason: str) -> int:
    """``delete_frames_for_recordings`` on a worker thread, for the API's delete paths.

    Each path calls it once its own delete has committed. A recording with no
    frames costs one ``stat``. Never raises.
    """
    import asyncio

    if not public_ids:
        return 0
    return await asyncio.to_thread(
        delete_frames_for_recordings, public_ids, reason=reason
    )
