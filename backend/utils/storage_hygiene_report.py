"""What a storage hygiene run found, did and refused, and how it is written down.

Shared by the routine in :mod:`backend.utils.storage_hygiene`, which logs a
:func:`summary_line` per run and a line per flagged or removed file, and the
operator CLI, which prints the flagged files and a table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

DOCUMENTS = "documents"
AUDIO = "audio"
PROXIES = "proxies"
PARTIAL_UPLOADS = "partial uploads"
# Files under names Nojoin never writes: reported, never removed.
UNRECOGNISED = "unrecognised"
CATEGORIES = (DOCUMENTS, AUDIO, PROXIES, PARTIAL_UPLOADS, UNRECOGNISED)
# Categories a stored path can name, so a row can be missing its file.
STORED_CATEGORIES = (DOCUMENTS, AUDIO, PROXIES)

RULE_EMPTY_TABLE = "empty_recordings"
RULE_DATA_ROOT = "data_root"
RULE_MISSING_FILES = "missing_files"
RULE_WORKING_DIRECTORY = "working_directory"
RULE_SPLIT_BRAIN = "split_brain"
RULE_BULK = "bulk"
RULE_RESTORE_LOCK = "restore_lock"

# What a deleting run did with a flagged file.
DELETED = "deleted"
FAILED = "failed"
KEPT = "kept"


@dataclass(frozen=True)
class BreakerTrip:
    """A circuit-breaker rule that stopped deletion, and why."""

    rule: str
    detail: str


@dataclass
class CategoryReport:
    scanned: int = 0
    unreferenced: int = 0
    unreferenced_bytes: int = 0
    deleted: int = 0
    deleted_bytes: int = 0
    failed: int = 0
    missing_rows: int = 0


@dataclass
class DirectoryReport:
    """One scanned directory. ``old`` counts the files past the grace that a row
    names or Nojoin could have written; ``candidates`` are the unreferenced ones."""

    path: Path
    files: int = 0
    file_bytes: int = 0
    old: int = 0
    candidates: int = 0
    candidate_bytes: int = 0


@dataclass
class FlaggedFile:
    """An unreferenced file past the grace, and what a deleting run did with it.

    ``outcome`` stays None until a run tries to remove the file, then reads
    ``deleted``, ``failed``, or ``kept`` for one that changed or went first, or
    that a row named when the rows were read again.
    """

    category: str
    path: Path
    size: int
    mtime_ns: int
    outcome: str | None = None


def _empty_categories() -> dict[str, CategoryReport]:
    return {name: CategoryReport() for name in CATEGORIES}


@dataclass
class HygieneReport:
    delete_requested: bool
    directories: list[DirectoryReport] = field(default_factory=list)
    categories: dict[str, CategoryReport] = field(default_factory=_empty_categories)
    breaker: list[BreakerTrip] = field(default_factory=list)
    flagged: list[FlaggedFile] = field(default_factory=list)

    @property
    def tripped(self) -> bool:
        return bool(self.breaker)

    @property
    def rules(self) -> list[str]:
        return list(dict.fromkeys(trip.rule for trip in self.breaker))

    @property
    def reclaimable_bytes(self) -> int:
        return sum(
            entry.unreferenced_bytes
            for name, entry in self.categories.items()
            if name != UNRECOGNISED
        )

    @property
    def deleted(self) -> int:
        return sum(entry.deleted for entry in self.categories.values())

    @property
    def failed(self) -> int:
        return sum(entry.failed for entry in self.categories.values())


def format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    value = size / 1024
    for unit in ("KiB", "MiB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GiB"


def format_mtime(mtime_ns: int) -> str:
    moment = datetime.fromtimestamp(mtime_ns // 1_000_000_000, tz=UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def describe(flagged: FlaggedFile) -> str:
    """One file, as the log lines name it."""
    return (
        f"{flagged.path} ({flagged.category}, {flagged.size} bytes, "
        f"modified {format_mtime(flagged.mtime_ns)})"
    )


def summary_line(report: HygieneReport) -> str:
    mode = "delete" if report.delete_requested else "report only"
    parts = [
        f"{name} {entry.scanned} scanned, {entry.unreferenced} unreferenced "
        f"({format_bytes(entry.unreferenced_bytes)}), {entry.deleted} deleted, "
        f"{entry.failed} failed, {entry.missing_rows} rows missing their file"
        for name, entry in report.categories.items()
    ]
    if not report.tripped:
        breaker = "clear"
    elif report.deleted or report.failed:
        breaker = f"tripped ({', '.join(report.rules)}) partway, deletion stopped"
    else:
        breaker = f"tripped ({', '.join(report.rules)}), nothing removed"
    return f"Storage hygiene ({mode}): {'; '.join(parts)}. Circuit breaker {breaker}."
