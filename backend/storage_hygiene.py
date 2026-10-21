"""Report, or remove, document, audio and proxy files that no row names.

    docker compose exec --user appuser worker-io python -m backend.storage_hygiene

Lists each unreferenced file past the grace, then a table per category. A dry
run by default. ``--delete`` removes the files now instead of at the next
daily run, still only those unmodified for 24 hours and still under every rule of
the circuit breaker. ``--allow-bulk`` lifts only the rule against removing over
10% of a directory's files or bytes, for a backlog. Run it from the app
directory, as ``exec`` does: stored paths are relative to it.

Exits 0 after a clean run, 1 when the circuit breaker tripped or a file could
not be removed, and 2 for a usage error.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from backend.core.db import get_sync_session
from backend.utils.storage_hygiene import HygieneOptions, run_storage_hygiene
from backend.utils.storage_hygiene_report import (
    PARTIAL_UPLOADS,
    HygieneReport,
    format_bytes,
)

_COLUMNS = (
    ("Category", 16),
    ("Scanned", 9),
    ("Unreferenced", 14),
    ("Reclaimable", 13),
    ("Deleted", 9),
    ("Failed", 8),
    ("Missing rows", 14),
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.storage_hygiene",
        description=(
            "Report the document, audio and proxy files that no database row "
            "names. Nothing is removed without --delete."
        ),
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="remove the unreferenced files now; files modified in the last 24 hours are kept",
    )
    parser.add_argument(
        "--allow-bulk",
        action="store_true",
        help="lift the rule against removing over 10%% of a directory; every other rule still applies",
    )
    return parser


def _row(cells: Sequence[object]) -> str:
    first, *rest = cells
    line = f"{first!s:<{_COLUMNS[0][1]}}"
    for cell, (_, width) in zip(rest, _COLUMNS[1:], strict=True):
        line += f"{cell!s:>{width}}"
    return line.rstrip()


_OUTCOMES = {
    "deleted": "removed",
    "failed": "could not be removed",
    "kept": "kept, changed since the listing",
}


def _flagged_lines(report: HygieneReport) -> list[str]:
    """Each unreferenced file past the grace, with what a deleting run did to it."""
    if not report.flagged:
        return []
    lines = ["Unreferenced files past the 24-hour grace:"]
    order = {name: index for index, name in enumerate(report.categories)}
    for flagged in sorted(
        report.flagged, key=lambda item: (order[item.category], str(item.path))
    ):
        line = (
            f"  {flagged.category:<{_COLUMNS[0][1]}}"
            f"{format_bytes(flagged.size):>10}  {flagged.path}"
        )
        if flagged.outcome is not None:
            line += f"  ({_OUTCOMES[flagged.outcome]})"
        lines.append(line)
    return [*lines, ""]


def format_report(report: HygieneReport) -> str:
    if report.delete_requested:
        title = "Storage hygiene: delete"
    else:
        title = "Storage hygiene: dry run, nothing removed"
    lines = [title, "", *_flagged_lines(report), _row([name for name, _ in _COLUMNS])]
    for name, entry in report.categories.items():
        lines.append(
            _row(
                [
                    name,
                    entry.scanned,
                    entry.unreferenced,
                    format_bytes(entry.unreferenced_bytes),
                    entry.deleted,
                    entry.failed,
                    "-" if name == PARTIAL_UPLOADS else entry.missing_rows,
                ]
            )
        )
    lines += [
        "",
        f"Total reclaimable: {format_bytes(report.reclaimable_bytes)} "
        f"({report.reclaimable_bytes} bytes)",
        "Directories: " + ", ".join(str(path) for path in report.directories),
    ]
    if report.tripped:
        lines.append("Circuit breaker tripped, nothing removed:")
        lines += [f"  - {trip.rule}: {trip.detail}" for trip in report.breaker]
    else:
        lines.append("Circuit breaker: clear")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run_storage_hygiene(
        get_sync_session,
        HygieneOptions(delete=args.delete, allow_bulk=args.allow_bulk),
    )
    print(format_report(report))
    return 1 if report.tripped or report.failed else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
