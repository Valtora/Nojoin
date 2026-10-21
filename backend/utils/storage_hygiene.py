"""Find, and optionally remove, stored files that no database row names.

One routine behind two entrypoints: the daily ``cleanup_temp_recordings`` task,
whose mode ``NOJOIN_STORAGE_HYGIENE`` sets, and the operator CLI
``python -m backend.storage_hygiene``.

A run lists the documents directory and both recordings roots first, then reads
every stored path in one statement, soft-deleted rows included, then subtracts
(:mod:`backend.utils.storage_hygiene_scan` says what keeps a file). A row that
commits after the listing can only add references, and a file written after it
is not in the listing at all. Only unreferenced files older than the grace,
under names Nojoin writes, are candidates. Before unlinking, the rows are read
again, the restore lock is read every few files, and each file's inode and
mtime are checked once more. The circuit breaker
(:mod:`backend.utils.storage_hygiene_breaker`) turns a run that looks wrong into
a report. Rows are never changed, and every removed file is logged.
"""

from __future__ import annotations

import logging
import os
import stat
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlmodel import Session

from backend.utils.storage_hygiene_breaker import (
    breaker_trips,
    open_redis_client,
    restore_lock_trip,
)
from backend.utils.storage_hygiene_report import (
    AUDIO,
    DELETED,
    DOCUMENTS,
    FAILED,
    KEPT,
    PARTIAL_UPLOADS,
    PROXIES,
    RULE_BULK,
    RULE_DATA_ROOT,
    RULE_EMPTY_TABLE,
    RULE_MISSING_FILES,
    RULE_RESTORE_LOCK,
    RULE_SPLIT_BRAIN,
    RULE_WORKING_DIRECTORY,
    UNRECOGNISED,
    BreakerTrip,
    DirectoryReport,
    FlaggedFile,
    HygieneReport,
    describe,
    summary_line,
)
from backend.utils.storage_hygiene_scan import (
    References,
    ScanDirectory,
    StoredFile,
    hygiene_directories,
    list_directories,
    non_mp3_bases,
    read_references,
    unreferenced_category,
)

__all__ = [
    "AUDIO",
    "DOCUMENTS",
    "PARTIAL_UPLOADS",
    "PROXIES",
    "RULE_BULK",
    "RULE_DATA_ROOT",
    "RULE_EMPTY_TABLE",
    "RULE_MISSING_FILES",
    "RULE_RESTORE_LOCK",
    "RULE_SPLIT_BRAIN",
    "RULE_WORKING_DIRECTORY",
    "UNRECOGNISED",
    "BreakerTrip",
    "HygieneOptions",
    "HygieneReport",
    "run_storage_hygiene",
    "storage_hygiene_mode",
]

logger = logging.getLogger(__name__)

# The same floor as the other 24 hour sweeps and RECORDING_UPLOAD_RETENTION_HOURS.
GRACE_SECONDS = 24 * 60 * 60
# The restore lock is read again after this many unlinks.
LOCK_RECHECK_EVERY = 25
# Flagged files a run that removes nothing lists in the log.
LOGGED_FLAGGED = 50

STORAGE_HYGIENE_ENV_KEY = "NOJOIN_STORAGE_HYGIENE"
STORAGE_HYGIENE_FLAG = "flag"
STORAGE_HYGIENE_DELETE = "delete"


@dataclass(frozen=True)
class HygieneOptions:
    """``delete`` unlinks; otherwise the run only reports.

    ``bulk_allowances`` maps a scanned directory to the number of files a run
    may remove from it although that is over a tenth of it; any other count
    still trips. ``now`` and ``redis_client`` are seams for tests: the time the
    grace is measured from, and what opens the client the restore lock is read
    through.
    """

    delete: bool = False
    bulk_allowances: Mapping[Path, int] = field(default_factory=dict)
    now: float | None = None
    redis_client: Callable[[], Any] | None = None


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


# Seams, looked up when called so tests can step between a run's stages.
def _open_redis_client() -> Any:
    return open_redis_client()


def _snapshot(directories: Sequence[ScanDirectory]) -> list[StoredFile]:
    return list_directories(directories)


def _read_references(session_factory: Callable[[], Session]) -> References:
    return read_references(session_factory)


def _lock_trip(options: HygieneOptions) -> BreakerTrip | None:
    return restore_lock_trip(options.redis_client or _open_redis_client)


def _tally(
    files: Sequence[StoredFile],
    references: References,
    cutoff_ns: int,
    report: HygieneReport,
) -> list[tuple[StoredFile, FlaggedFile]]:
    """Count every file, and flag the candidates: unreferenced, past the grace,
    under a name Nojoin writes."""
    siblings = non_mp3_bases(files)
    directories = {directory.path: directory for directory in report.directories}
    candidates: list[tuple[StoredFile, FlaggedFile]] = []
    for stored in files:
        directory = directories[stored.directory]
        directory.files += 1
        directory.file_bytes += stored.size
        named = references.category_of(stored)
        category = named or unreferenced_category(stored, siblings)
        entry = report.categories[category]
        entry.scanned += 1
        if stored.mtime_ns >= cutoff_ns:
            continue
        if named is None:
            entry.unreferenced += 1
            entry.unreferenced_bytes += stored.size
        if category == UNRECOGNISED:
            continue
        directory.old += 1
        if named is not None:
            continue
        directory.candidates += 1
        directory.candidate_bytes += stored.size
        flagged = FlaggedFile(category, stored.path, stored.size, stored.mtime_ns)
        candidates.append((stored, flagged))
        report.flagged.append(flagged)
    for category, count in references.missing.items():
        report.categories[category].missing_rows = count
    return candidates


def _unchanged(stored: StoredFile, status: os.stat_result) -> bool:
    return stat.S_ISREG(status.st_mode) and (
        status.st_dev,
        status.st_ino,
        status.st_mtime_ns,
    ) == (stored.device, stored.inode, stored.mtime_ns)


def _unlink_if_unchanged(stored: StoredFile) -> str:
    """Unlink a candidate unless it changed or went since the listing."""
    try:
        status = os.lstat(stored.path)
    except FileNotFoundError:
        return KEPT
    except OSError as error:
        logger.warning("Storage hygiene could not check %s: %s", stored.path, error)
        return FAILED
    if not _unchanged(stored, status):
        return KEPT
    try:
        os.unlink(stored.path)
    except FileNotFoundError:
        return KEPT
    except OSError as error:
        logger.warning("Storage hygiene could not remove %s: %s", stored.path, error)
        return FAILED
    return DELETED


def _delete(
    candidates: Sequence[tuple[StoredFile, FlaggedFile]],
    report: HygieneReport,
    session_factory: Callable[[], Session],
    options: HygieneOptions,
) -> None:
    # Read again: a row committed since the first read keeps its file.
    references = _read_references(session_factory)
    for index, (stored, flagged) in enumerate(candidates):
        if index % LOCK_RECHECK_EVERY == 0:
            lock_trip = _lock_trip(options)
            if lock_trip is not None:
                report.breaker.append(lock_trip)
                return
        if references.category_of(stored) is not None:
            flagged.outcome = KEPT
            continue
        flagged.outcome = _unlink_if_unchanged(stored)
        entry = report.categories[flagged.category]
        if flagged.outcome == DELETED:
            # One line per file, so the log says what went as well as how much.
            logger.info("Storage hygiene removed %s", describe(flagged))
            entry.deleted += 1
            entry.deleted_bytes += stored.size
        elif flagged.outcome == FAILED:
            entry.failed += 1


def _log_run(report: HygieneReport) -> None:
    if report.tripped:
        split_brain = RULE_SPLIT_BRAIN in report.rules
        logger.log(
            logging.ERROR if split_brain else logging.WARNING,
            "Storage hygiene circuit breaker tripped (%s): %s",
            ", ".join(report.rules),
            "; ".join(trip.detail for trip in report.breaker),
        )
    untried = [flagged for flagged in report.flagged if flagged.outcome is None]
    if not (report.deleted or report.failed):
        for flagged in untried[:LOGGED_FLAGGED]:
            logger.info("Storage hygiene would remove %s", describe(flagged))
        if len(untried) > LOGGED_FLAGGED:
            logger.info(
                "Storage hygiene would remove %d more files",
                len(untried) - LOGGED_FLAGGED,
            )
    logger.info("%s", summary_line(report))


def run_storage_hygiene(
    session_factory: Callable[[], Session],
    options: HygieneOptions | None = None,
) -> HygieneReport:
    """Report the files no row names and, when asked and nothing trips, remove them.

    ``session_factory`` opens each session the stored paths are read in. A new
    one, so its first read comes after the directory listing.
    """
    options = options or HygieneOptions()
    now = time.time() if options.now is None else options.now
    cutoff_ns = int(now * 1_000_000_000) - GRACE_SECONDS * 1_000_000_000

    lock_trip = _lock_trip(options)
    scanned = hygiene_directories()
    files = _snapshot(scanned)
    references = _read_references(session_factory)

    report = HygieneReport(
        delete_requested=options.delete,
        directories=[DirectoryReport(path=directory.path) for directory in scanned],
    )
    candidates = _tally(files, references, cutoff_ns, report)
    allowances = {
        Path(path).expanduser().resolve(): count
        for path, count in options.bulk_allowances.items()
    }
    report.breaker = breaker_trips(scanned, report.directories, references, allowances)
    if lock_trip is not None:
        report.breaker.append(lock_trip)

    if options.delete and not report.breaker:
        _delete(candidates, report, session_factory, options)

    _log_run(report)
    return report
