"""The circuit breaker: the rules under which a storage hygiene run removes nothing.

Every rule only ever refuses. Only the bulk rule can be lifted, for one named
directory and an expected count of files, so no single flag removes most of a
directory: the split-brain rule refuses that whatever is passed. The clock rule
is the only one that writes: when a deleting run passes every other rule, a
probe in each directory, which it removes again.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from backend.core.backup.format import RESTORE_LOCK_KEY
from backend.utils.path_manager import PathManager
from backend.utils.storage_hygiene_report import (
    RULE_BULK,
    RULE_CLOCK,
    RULE_DATA_ROOT,
    RULE_EMPTY_TABLE,
    RULE_MISSING_FILES,
    RULE_RESTORE_LOCK,
    RULE_SPLIT_BRAIN,
    RULE_WORKING_DIRECTORY,
    STORED_CATEGORIES,
    BreakerTrip,
    DirectoryReport,
    format_bytes,
)
from backend.utils.storage_hygiene_scan import References, ScanDirectory

logger = logging.getLogger("backend.utils.storage_hygiene")

# Over this share of one category's stored paths naming missing files, data/ is
# probably not this database's, as when a fresh Postgres volume sits behind an
# old data mount. Measured per category so plentiful documents cannot dilute
# missing audio.
MAX_MISSING_PERCENT = 50
# Over this share of a directory's old files unreferenced, the database probably
# does not belong to this data directory. No option lifts it.
MAX_UNREFERENCED_PERCENT = 50
# Over this share of one directory's files or bytes, a run is a bulk removal,
# which needs --allow-bulk for that directory and the expected count.
MAX_REMOVED_PERCENT = 10
# Over this many seconds between a fresh probe's mtime and the worker's clock,
# the grace would let go of files written within it.
MAX_CLOCK_LAG_SECONDS = 60 * 60
# A dotfile, so no listing, this run's or a concurrent one's, ever includes it.
CLOCK_PROBE_PREFIX = ".nojoin-hygiene-clock-"


def _data_root_trips(directories: Sequence[ScanDirectory]) -> list[BreakerTrip]:
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


def _missing_trips(references: References) -> list[BreakerTrip]:
    trips: list[BreakerTrip] = []
    for category in STORED_CATEGORIES:
        stored = references.stored.get(category, 0)
        missing = references.missing.get(category, 0)
        if missing * 100 > stored * MAX_MISSING_PERCENT:
            trips.append(
                BreakerTrip(
                    RULE_MISSING_FILES,
                    f"{missing} of {stored} stored {category} paths name missing files",
                )
            )
    return trips


def _working_directory_trips(references: References) -> list[BreakerTrip]:
    """Relative stored paths resolve against the working directory, as the app's do.

    When most of them are missing from here, this is not the directory the app
    runs in, and every relative path would point at the wrong file.
    """
    stored, missing = references.relative_stored, references.relative_missing
    if missing * 100 > stored * MAX_MISSING_PERCENT:
        return [
            BreakerTrip(
                RULE_WORKING_DIRECTORY,
                f"{missing} of {stored} stored paths relative to {Path.cwd()} name "
                "missing files; run from the directory the app runs in",
            )
        ]
    return []


def _split_brain_trips(directories: Sequence[DirectoryReport]) -> list[BreakerTrip]:
    return [
        BreakerTrip(
            RULE_SPLIT_BRAIN,
            f"{directory.path}: {directory.candidates} of {directory.old} files older "
            "than 24 hours are named by no row; the database may not belong to this "
            "data directory",
        )
        for directory in directories
        if directory.candidates * 100 > directory.old * MAX_UNREFERENCED_PERCENT
    ]


def _bulk_trips(
    directories: Sequence[DirectoryReport], allowances: Mapping[Path, int]
) -> list[BreakerTrip]:
    trips: list[BreakerTrip] = []
    for directory in directories:
        allowed = allowances.get(directory.path)
        if allowed is not None:
            if allowed != directory.candidates:
                trips.append(
                    BreakerTrip(
                        RULE_BULK,
                        f"{directory.path}: expected {allowed} files, but the run "
                        f"would remove {directory.candidates}",
                    )
                )
            continue
        if (
            directory.candidates * 100 > directory.files * MAX_REMOVED_PERCENT
            or directory.candidate_bytes * 100
            > directory.file_bytes * MAX_REMOVED_PERCENT
        ):
            trips.append(
                BreakerTrip(
                    RULE_BULK,
                    f"{directory.path}: would remove {directory.candidates} of "
                    f"{directory.files} files ({format_bytes(directory.candidate_bytes)} "
                    f"of {format_bytes(directory.file_bytes)}), over {MAX_REMOVED_PERCENT}%",
                )
            )
    scanned = {directory.path for directory in directories}
    trips.extend(
        BreakerTrip(RULE_BULK, f"{path}: allowed, but this run does not scan it")
        for path in allowances
        if path not in scanned
    )
    return trips


def breaker_trips(
    scanned: Sequence[ScanDirectory],
    directories: Sequence[DirectoryReport],
    references: References,
    allowances: Mapping[Path, int],
) -> list[BreakerTrip]:
    trips: list[BreakerTrip] = []
    if references.recordings == 0 and any(d.files for d in directories):
        trips.append(
            BreakerTrip(
                RULE_EMPTY_TABLE,
                "the recordings table is empty while the directories hold "
                f"{sum(d.files for d in directories)} files",
            )
        )
    trips.extend(_data_root_trips(scanned))
    trips.extend(_missing_trips(references))
    trips.extend(_working_directory_trips(references))
    trips.extend(_split_brain_trips(directories))
    trips.extend(_bulk_trips(directories, allowances))
    return trips


def _write_probe(path: Path) -> None:
    """Create the probe and push a write through, so the filesystem sets its
    mtime as it does for every stored file."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, b"Nojoin storage hygiene clock probe\n")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_probe(path: Path) -> None:
    try:
        # A probe that was never created leaves nothing to remove, and on a
        # read-only filesystem unlinking it would fail for that reason too.
        if os.path.lexists(path):
            os.unlink(path)
    except OSError as error:
        logger.warning("Storage hygiene could not remove its probe %s: %s", path, error)


def _clock_trip(directory: Path) -> BreakerTrip | None:
    probe = directory / f"{CLOCK_PROBE_PREFIX}{uuid.uuid4().hex}"
    try:
        _write_probe(probe)
        # The mtime the filesystem gives the probe, read back after the write.
        lag = time.time() - os.stat(probe).st_mtime_ns / 1_000_000_000
    except OSError as error:
        return BreakerTrip(
            RULE_CLOCK,
            f"{directory}: could not write and read back a probe to check its "
            f"clock: {str(error).rstrip('.')}",
        )
    finally:
        _remove_probe(probe)
    if lag > MAX_CLOCK_LAG_SECONDS:
        return BreakerTrip(
            RULE_CLOCK,
            f"{directory}: its clock is {lag / 3600:.1f} hours behind this worker's, "
            "so files written within the 24-hour grace would look older",
        )
    return None


def clock_trips(directories: Sequence[ScanDirectory]) -> list[BreakerTrip]:
    """Why each directory's clock cannot be trusted for the grace, from a probe
    written there. Only for a run about to remove files: one that reports, or
    that another rule stops, writes nothing.

    The grace compares each file's mtime with this worker's clock, but on an NFS
    or SMB mount the server sets the mtime. A clock running ahead only keeps more
    files. A directory that is not there holds nothing to remove.
    """
    trips: list[BreakerTrip] = []
    for directory in directories:
        if not directory.path.is_dir():
            continue
        trip = _clock_trip(directory.path)
        if trip is not None:
            trips.append(trip)
    return trips


def open_redis_client() -> Any:
    import redis

    from backend.core.redis import get_redis_url

    return redis.from_url(get_redis_url(), socket_connect_timeout=5, socket_timeout=5)


def restore_lock_trip(open_client: Callable[[], Any]) -> BreakerTrip | None:
    """Why deleting must wait for the restore lock, or None when it is not set.

    Fails closed. A restore proceeds without the lock when Redis is down, so a
    lock store that cannot be read may hide a restore that is running. The lock
    expires after six hours, so a restore that outlives it is not seen.
    """
    try:
        client = open_client()
        try:
            held = bool(client.exists(RESTORE_LOCK_KEY))
        finally:
            client.close()
    except Exception as error:  # noqa: BLE001 -- boundary: an unreadable lock store must refuse deletion rather than fail the daily cleanup
        return BreakerTrip(
            RULE_RESTORE_LOCK,
            f"the restore lock could not be read: {str(error).rstrip('.')}",
        )
    if held:
        return BreakerTrip(RULE_RESTORE_LOCK, "the restore lock is set")
    return None
