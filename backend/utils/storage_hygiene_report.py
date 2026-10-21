"""What a storage hygiene run found, and how it is written down.

Shared by the routine in :mod:`backend.utils.storage_hygiene`, which logs one
:func:`summary_line` per run, and the operator CLI, which prints the flagged
files and a table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DOCUMENTS = "documents"
AUDIO = "audio"
PROXIES = "proxies"
PARTIAL_UPLOADS = "partial uploads"
CATEGORIES = (DOCUMENTS, AUDIO, PROXIES, PARTIAL_UPLOADS)

RULE_EMPTY_TABLE = "empty_recordings"
RULE_DATA_ROOT = "data_root"
RULE_MISSING_FILES = "missing_files"
RULE_BULK = "bulk"
RULE_RESTORE_LOCK = "restore_lock"


@dataclass(frozen=True)
class BreakerTrip:
    """A circuit-breaker rule that stopped deletion, and why."""

    rule: str
    detail: str


@dataclass
class FlaggedFile:
    """An unreferenced file past the grace, and what a deleting run did with it.

    ``outcome`` stays None until a run tries to remove the file, then reads
    ``deleted``, ``failed``, or ``kept`` for one that changed or went first.
    """

    category: str
    path: Path
    size: int
    outcome: str | None = None


@dataclass
class CategoryReport:
    scanned: int = 0
    unreferenced: int = 0
    unreferenced_bytes: int = 0
    deleted: int = 0
    deleted_bytes: int = 0
    failed: int = 0
    missing_rows: int = 0


def _empty_categories() -> dict[str, CategoryReport]:
    return {name: CategoryReport() for name in CATEGORIES}


@dataclass
class HygieneReport:
    delete_requested: bool
    directories: list[Path] = field(default_factory=list)
    categories: dict[str, CategoryReport] = field(default_factory=_empty_categories)
    breaker: list[BreakerTrip] = field(default_factory=list)
    flagged: list[FlaggedFile] = field(default_factory=list)

    @property
    def tripped(self) -> bool:
        return bool(self.breaker)

    @property
    def reclaimable_bytes(self) -> int:
        return sum(entry.unreferenced_bytes for entry in self.categories.values())

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


def summary_line(report: HygieneReport) -> str:
    mode = "delete" if report.delete_requested else "report only"
    parts = [
        f"{name} {entry.scanned} scanned, {entry.unreferenced} unreferenced "
        f"({format_bytes(entry.unreferenced_bytes)}), {entry.deleted} deleted, "
        f"{entry.failed} failed, {entry.missing_rows} rows missing their file"
        for name, entry in report.categories.items()
    ]
    if report.tripped:
        breaker = "tripped, nothing removed: " + "; ".join(
            trip.detail for trip in report.breaker
        )
    else:
        breaker = "clear"
    return f"Storage hygiene ({mode}): {'; '.join(parts)}. Circuit breaker {breaker}."
