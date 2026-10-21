"""Find, and optionally remove, stored files that no database row names.

One routine behind two entrypoints: the daily ``cleanup_temp_recordings`` task,
whose mode ``NOJOIN_STORAGE_HYGIENE`` sets, and the operator CLI
``python -m backend.storage_hygiene``.

A run lists the documents directory and both recordings roots first, then reads
every stored path in one statement, soft-deleted rows included, then subtracts.
A row that commits after the listing can only add references, and a file written
after it is not in the listing at all. Files are matched by device and inode, as
:func:`document_file_identity` does, so every spelling of a path counts. Nothing
modified in the last 24 hours is a candidate, which covers a file whose row has
not committed yet, and each file's inode and mtime are checked again right
before it is unlinked. Any rule of the circuit breaker turns the run into a
report. Rows whose file is missing are counted, never changed.

Only regular files directly inside each directory are considered: ``temp/`` and
``failed/`` belong to the existing 24 hour sweep, and dotfiles and symlinks are
left alone.
"""

from __future__ import annotations

import logging
import os
import stat
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import CompoundSelect, String, false, literal, select, union_all
from sqlmodel import Session, col

from backend.core.backup.format import RESTORE_LOCK_KEY
from backend.models.document import Document
from backend.models.recording import Recording, RecordingStatus
from backend.utils.document_storage import document_file_identity, documents_root_dir
from backend.utils.path_manager import PathManager
from backend.utils.recording_storage import recordings_root_dir
from backend.utils.storage_hygiene_report import (
    AUDIO,
    DOCUMENTS,
    PARTIAL_UPLOADS,
    PROXIES,
    RULE_BULK,
    RULE_DATA_ROOT,
    RULE_EMPTY_TABLE,
    RULE_MISSING_FILES,
    RULE_RESTORE_LOCK,
    BreakerTrip,
    HygieneReport,
    format_bytes,
    summary_line,
)

logger = logging.getLogger(__name__)

# The same floor as the other 24 hour sweeps and RECORDING_UPLOAD_RETENTION_HOURS.
GRACE_SECONDS = 24 * 60 * 60
# Over this share of stored paths naming missing files, data/ is probably not
# this database's, as when a fresh Postgres volume sits behind an old data mount.
MAX_MISSING_PERCENT = 50
# Over this share of one directory's files or bytes, a run is a bulk removal
# that only the CLI's --allow-bulk may make.
MAX_REMOVED_PERCENT = 10

# What a killed upload leaves: upload_limit.py's ``<dest>.<hex>.upload-tmp`` and
# browser_capture_audio.py's ``<name>.part``.
PARTIAL_UPLOAD_SUFFIXES = (".upload-tmp", ".part")
# A recording in these states has no audio file yet, by design: capture init
# names a file that finalize replaces, and an upload's bytes are still arriving.
PENDING_STATUSES = (RecordingStatus.UPLOADING, RecordingStatus.PAUSED)

STORAGE_HYGIENE_ENV_KEY = "NOJOIN_STORAGE_HYGIENE"
STORAGE_HYGIENE_FLAG = "flag"
STORAGE_HYGIENE_DELETE = "delete"

_RECORDINGS_ROLE = "recordings"
_DELETED, _FAILED, _KEPT = "deleted", "failed", "kept"


@dataclass(frozen=True)
class HygieneOptions:
    """``delete`` unlinks; otherwise the run only reports.

    ``allow_bulk`` lifts the one rule against removing over a tenth of a
    directory. ``now`` and ``redis_client`` are seams for tests: the time the
    grace is measured from, and what opens the client the restore lock is read
    through.
    """

    delete: bool = False
    allow_bulk: bool = False
    now: float | None = None
    redis_client: Callable[[], Any] | None = None


@dataclass(frozen=True)
class _ScanDirectory:
    role: str
    path: Path


@dataclass(frozen=True)
class _StoredFile:
    path: Path
    directory: Path
    category: str
    device: int
    inode: int
    size: int
    mtime_ns: int

    def identities(self) -> tuple[tuple[object, ...], ...]:
        """The keys :func:`document_file_identity` could give a row naming this file."""
        return (("inode", self.device, self.inode), ("path", str(self.path)))


@dataclass
class _References:
    recordings: int = 0
    stored: int = 0
    identities: set[tuple[object, ...]] = field(default_factory=set)
    missing: dict[str, int] = field(default_factory=dict)


def storage_hygiene_mode() -> str:
    """What the daily cleanup does with files no row names: ``flag`` or ``delete``.

    Read as ``MCP_ENABLED`` is in config_manager: unset or blank means the
    default, ``flag``, which only reports. Any value other than ``delete`` reads
    as ``flag``, so a typo never turns deletion on. Changing it takes a worker
    restart, like other environment settings.
    """
    raw = os.environ.get(STORAGE_HYGIENE_ENV_KEY)
    if raw is None or not raw.strip():
        return STORAGE_HYGIENE_FLAG
    value = raw.strip().lower()
    if value == STORAGE_HYGIENE_DELETE:
        return STORAGE_HYGIENE_DELETE
    if value != STORAGE_HYGIENE_FLAG:
        logger.warning(
            "Ignoring %s=%r: expected flag or delete, so only reporting.",
            STORAGE_HYGIENE_ENV_KEY,
            raw,
        )
    return STORAGE_HYGIENE_FLAG


def hygiene_directories() -> list[_ScanDirectory]:
    """The documents directory and both recordings roots, resolved, each once.

    Uploads, imports and deletes use ``recordings_root_dir()``, which follows
    ``RECORDINGS_DIR`` and the working directory. The restore and the demo seed
    use ``PathManager().recordings_directory``, which follows the data directory.
    They differ whenever either moves, so both are scanned.
    """
    candidates = (
        (DOCUMENTS, documents_root_dir()),
        (_RECORDINGS_ROLE, recordings_root_dir(create=False)),
        (_RECORDINGS_ROLE, PathManager().recordings_directory),
    )
    directories: list[_ScanDirectory] = []
    seen: set[Path] = set()
    for role, path in candidates:
        resolved = path.expanduser().resolve()
        if resolved not in seen:
            seen.add(resolved)
            directories.append(_ScanDirectory(role=role, path=resolved))
    return directories


def _category(name: str, role: str) -> str:
    if name.endswith(PARTIAL_UPLOAD_SUFFIXES):
        return PARTIAL_UPLOADS
    if role == DOCUMENTS:
        return DOCUMENTS
    # A proxy is ``<audio base>.mp3``, and an MP3 recording's audio is its own proxy.
    if name.lower().endswith(".mp3"):
        return PROXIES
    return AUDIO


def _list_directory(directory: _ScanDirectory) -> list[_StoredFile]:
    try:
        names = os.listdir(directory.path)
    except FileNotFoundError:
        return []
    except OSError as error:
        logger.warning("Storage hygiene could not list %s: %s", directory.path, error)
        return []

    files: list[_StoredFile] = []
    for name in names:
        if name.startswith("."):
            continue
        path = directory.path / name
        try:
            status = os.lstat(path)
        except OSError:
            continue
        if not stat.S_ISREG(status.st_mode):
            continue
        files.append(
            _StoredFile(
                path=path,
                directory=directory.path,
                category=_category(name, directory.role),
                device=status.st_dev,
                inode=status.st_ino,
                size=status.st_size,
                mtime_ns=status.st_mtime_ns,
            )
        )
    return files


def _snapshot(directories: Sequence[_ScanDirectory]) -> list[_StoredFile]:
    files: list[_StoredFile] = []
    for directory in directories:
        files.extend(_list_directory(directory))
    return files


def _reference_statement() -> CompoundSelect:
    """Every stored path, soft-deleted rows included, in one statement.

    One statement is one snapshot of the database, even under READ COMMITTED.
    A proxy that is its recording's audio is read once, as the audio.
    """
    pending = col(Recording.status).in_(PENDING_STATUSES)
    return union_all(
        select(literal(AUDIO, String), col(Recording.audio_path), pending),
        select(literal(PROXIES, String), col(Recording.proxy_path), pending).where(
            col(Recording.proxy_path).is_not(None),
            col(Recording.proxy_path) != col(Recording.audio_path),
        ),
        select(literal(DOCUMENTS, String), col(Document.file_path), false()),
    )


def _read_references(session_factory: Callable[[], Session]) -> _References:
    with session_factory() as session:
        rows = session.connection().execute(_reference_statement()).all()

    references = _References()
    for category, raw_path, pending in rows:
        if category == AUDIO:
            references.recordings += 1
        if not raw_path:
            continue
        identity = document_file_identity(raw_path)
        if identity is not None:
            references.identities.add(identity)
        if pending:
            continue
        references.stored += 1
        if identity is None or identity[0] == "path":
            references.missing[category] = references.missing.get(category, 0) + 1
    return references


def _tally(
    files: Sequence[_StoredFile],
    references: _References,
    cutoff_ns: int,
    report: HygieneReport,
) -> list[_StoredFile]:
    """Count every file, and return the unreferenced ones older than the grace."""
    candidates: list[_StoredFile] = []
    for stored in files:
        entry = report.categories[stored.category]
        entry.scanned += 1
        if stored.mtime_ns >= cutoff_ns:
            continue
        if any(key in references.identities for key in stored.identities()):
            continue
        entry.unreferenced += 1
        entry.unreferenced_bytes += stored.size
        candidates.append(stored)
    for category, count in references.missing.items():
        report.categories[category].missing_rows = count
    return candidates


def _data_root_trips(directories: Sequence[_ScanDirectory]) -> list[BreakerTrip]:
    """A directory pointed at data/ would bring config.json and the keys into the scan."""
    data_root = PathManager().user_data_directory.expanduser().resolve()
    trips: list[BreakerTrip] = []
    for directory in directories:
        if directory.path == data_root or directory.path in data_root.parents:
            trips.append(
                BreakerTrip(
                    RULE_DATA_ROOT,
                    f"{directory.path} is the data directory {data_root} or contains it",
                )
            )
        elif (directory.path / "config.json").exists():
            trips.append(
                BreakerTrip(RULE_DATA_ROOT, f"{directory.path} holds config.json")
            )
    return trips


def _bulk_trips(
    directories: Sequence[_ScanDirectory],
    files: Sequence[_StoredFile],
    candidates: Sequence[_StoredFile],
) -> list[BreakerTrip]:
    trips: list[BreakerTrip] = []
    for directory in directories:
        held = [stored for stored in files if stored.directory == directory.path]
        doomed = [stored for stored in candidates if stored.directory == directory.path]
        held_bytes = sum(stored.size for stored in held)
        doomed_bytes = sum(stored.size for stored in doomed)
        if (
            len(doomed) * 100 > len(held) * MAX_REMOVED_PERCENT
            or doomed_bytes * 100 > held_bytes * MAX_REMOVED_PERCENT
        ):
            trips.append(
                BreakerTrip(
                    RULE_BULK,
                    f"{directory.path}: would remove {len(doomed)} of {len(held)} files "
                    f"({format_bytes(doomed_bytes)} of {format_bytes(held_bytes)}), "
                    f"over {MAX_REMOVED_PERCENT}%",
                )
            )
    return trips


def _breaker(
    directories: Sequence[_ScanDirectory],
    files: Sequence[_StoredFile],
    candidates: Sequence[_StoredFile],
    references: _References,
    allow_bulk: bool,
) -> list[BreakerTrip]:
    trips: list[BreakerTrip] = []
    if references.recordings == 0 and files:
        trips.append(
            BreakerTrip(
                RULE_EMPTY_TABLE,
                f"the recordings table is empty while the directories hold {len(files)} files",
            )
        )
    trips.extend(_data_root_trips(directories))
    missing = sum(references.missing.values())
    if missing * 100 > references.stored * MAX_MISSING_PERCENT:
        trips.append(
            BreakerTrip(
                RULE_MISSING_FILES,
                f"{missing} of {references.stored} stored paths name missing files",
            )
        )
    if not allow_bulk:
        trips.extend(_bulk_trips(directories, files, candidates))
    return trips


def _open_redis_client() -> Any:
    import redis

    from backend.core.redis import get_redis_url

    return redis.from_url(get_redis_url(), socket_connect_timeout=5, socket_timeout=5)


def restore_lock_trip(
    open_client: Callable[[], Any] | None = None,
) -> BreakerTrip | None:
    """Why deleting must wait for the restore lock, or None when it is free.

    Fails closed. A restore proceeds without the lock when Redis is down, so a
    lock store that cannot be read may hide a restore that is running.
    """
    opener = open_client or _open_redis_client
    try:
        client = opener()
        try:
            held = bool(client.exists(RESTORE_LOCK_KEY))
        finally:
            client.close()
    except Exception as error:  # noqa: BLE001 -- boundary: an unreadable lock store must refuse deletion rather than fail the daily cleanup
        return BreakerTrip(
            RULE_RESTORE_LOCK, f"the restore lock could not be read: {error}"
        )
    if held:
        return BreakerTrip(RULE_RESTORE_LOCK, "a restore is running")
    return None


def _unchanged(stored: _StoredFile, status: os.stat_result) -> bool:
    return stat.S_ISREG(status.st_mode) and (
        status.st_dev,
        status.st_ino,
        status.st_mtime_ns,
    ) == (stored.device, stored.inode, stored.mtime_ns)


def _unlink_if_unchanged(stored: _StoredFile) -> str:
    """Unlink a candidate unless it changed or went since the snapshot."""
    try:
        status = os.lstat(stored.path)
    except FileNotFoundError:
        return _KEPT
    except OSError as error:
        logger.warning("Storage hygiene could not check %s: %s", stored.path, error)
        return _FAILED
    if not _unchanged(stored, status):
        return _KEPT
    try:
        os.unlink(stored.path)
    except FileNotFoundError:
        return _KEPT
    except OSError as error:
        logger.warning("Storage hygiene could not remove %s: %s", stored.path, error)
        return _FAILED
    return _DELETED


def _delete(candidates: Sequence[_StoredFile], report: HygieneReport) -> None:
    for stored in candidates:
        outcome = _unlink_if_unchanged(stored)
        entry = report.categories[stored.category]
        if outcome == _DELETED:
            entry.deleted += 1
            entry.deleted_bytes += stored.size
        elif outcome == _FAILED:
            entry.failed += 1


def run_storage_hygiene(
    session_factory: Callable[[], Session],
    options: HygieneOptions | None = None,
) -> HygieneReport:
    """Report the files no row names and, when asked and nothing trips, remove them.

    ``session_factory`` opens the session the stored paths are read in. A new
    one, so its first read comes after the directory listing.
    """
    options = options or HygieneOptions()
    now = time.time() if options.now is None else options.now
    cutoff_ns = int(now * 1_000_000_000) - GRACE_SECONDS * 1_000_000_000

    lock_trip = restore_lock_trip(options.redis_client)
    directories = hygiene_directories()
    files = _snapshot(directories)
    references = _read_references(session_factory)

    report = HygieneReport(
        delete_requested=options.delete,
        directories=[directory.path for directory in directories],
    )
    candidates = _tally(files, references, cutoff_ns, report)
    report.breaker = _breaker(
        directories, files, candidates, references, options.allow_bulk
    )
    if lock_trip is not None:
        report.breaker.append(lock_trip)

    if options.delete and not report.breaker:
        # Again, right before unlinking: a restore may have started since.
        lock_trip = restore_lock_trip(options.redis_client)
        if lock_trip is None:
            _delete(candidates, report)
        else:
            report.breaker.append(lock_trip)

    logger.info("%s", summary_line(report))
    return report
