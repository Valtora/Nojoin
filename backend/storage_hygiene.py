"""Report, or remove, the document, audio and proxy files that no row names.

    docker compose exec --user appuser worker-io python -m backend.storage_hygiene

Lists each unreferenced file past the grace, then a table per category. A dry
run by default. ``--delete`` removes the files now instead of at the next daily
run, still only those unmodified for 24 hours, under names Nojoin writes, and
under every rule of the circuit breaker. Where a run would remove over 10% of a
directory's files or bytes, ``--allow-bulk <directory> --expect <count>`` lets
it, with the count the dry run printed for that directory. Nothing lifts the
rule against removing over half of a directory's old files. Run it from the app
directory, as ``exec`` does: stored paths are relative to it, and the breaker
refuses a run from which most of them are missing.

Exit status:

- 0: the run finished and nothing was refused;
- 1: the circuit breaker refused, so nothing, or nothing more, was removed;
- 2: a usage error;
- 3: a file could not be removed;
- 4: the run failed with an error.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from backend.core.db import get_sync_session
from backend.utils.storage_hygiene import HygieneOptions, run_storage_hygiene
from backend.utils.storage_hygiene_report import (
    PARTIAL_UPLOADS,
    UNRECOGNISED,
    HygieneReport,
    format_bytes,
)
from backend.utils.storage_hygiene_scan import hygiene_directories

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2
EXIT_UNLINK_FAILED = 3
EXIT_ERROR = 4

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
        metavar="DIRECTORY",
        help="let the run remove over 10%% of this one directory; needs --expect",
    )
    parser.add_argument(
        "--expect",
        metavar="COUNT",
        type=int,
        help="the number of files the dry run would remove from that directory",
    )
    parser.add_argument(
        "--show",
        metavar="COUNT",
        type=int,
        default=100,
        help="list at most this many files (default 100; 0 lists them all)",
    )
    return parser


def _row(cells: Sequence[object]) -> str:
    first, *rest = cells
    line = f"{first!s:<{_COLUMNS[0][1]}}"
    for cell, (_, width) in zip(rest, _COLUMNS[1:], strict=True):
        line += f"{cell!s:>{width}}"
    return line.rstrip()


def _category_lines(report: HygieneReport) -> list[str]:
    lines = [_row([name for name, _ in _COLUMNS])]
    for name, entry in report.categories.items():
        never_stored = name in (PARTIAL_UPLOADS, UNRECOGNISED)
        lines.append(
            _row(
                [
                    name,
                    entry.scanned,
                    entry.unreferenced,
                    "-"
                    if name == UNRECOGNISED
                    else format_bytes(entry.unreferenced_bytes),
                    entry.deleted,
                    entry.failed,
                    "-" if never_stored else entry.missing_rows,
                ]
            )
        )
    return lines


def _directory_lines(report: HygieneReport) -> list[str]:
    lines = ["Directory: files, files older than 24 hours, files to remove"]
    for directory in report.directories:
        lines.append(
            f"  {directory.path}: {directory.files}, {directory.old}, "
            f"{directory.candidates} ({format_bytes(directory.candidate_bytes)})"
        )
    return lines


_OUTCOMES = {
    "deleted": "removed",
    "failed": "could not be removed",
    "kept": "kept, changed or named since the listing",
}


def _flagged_lines(report: HygieneReport, show: int) -> list[str]:
    """Each unreferenced file past the grace, with what a deleting run did to it."""
    if not report.flagged:
        return []
    lines = ["Unreferenced files past the 24-hour grace:"]
    order = {name: index for index, name in enumerate(report.categories)}
    flagged = sorted(
        report.flagged, key=lambda item: (order[item.category], str(item.path))
    )
    listed = flagged if show <= 0 else flagged[:show]
    for item in listed:
        line = (
            f"  {item.category:<{_COLUMNS[0][1]}}"
            f"{format_bytes(item.size):>10}  {item.path}"
        )
        if item.outcome is not None:
            line += f"  ({_OUTCOMES[item.outcome]})"
        lines.append(line)
    if len(flagged) > len(listed):
        lines.append(
            f"  ... and {len(flagged) - len(listed)} more (--show 0 lists them all)"
        )
    return [*lines, ""]


def format_report(report: HygieneReport, show: int = 100) -> str:
    if report.delete_requested:
        title = "Storage hygiene: delete"
    else:
        title = "Storage hygiene: dry run, nothing removed"
    lines = [title, "", *_flagged_lines(report, show), *_category_lines(report)]
    lines += ["", *_directory_lines(report)]
    lines += [
        "",
        f"Total reclaimable: {format_bytes(report.reclaimable_bytes)} "
        f"({report.reclaimable_bytes} bytes)",
    ]
    if report.tripped:
        if report.deleted or report.failed:
            lines.append("Circuit breaker tripped partway, nothing more removed:")
        else:
            lines.append("Circuit breaker tripped, nothing removed:")
        lines += [f"  - {trip.rule}: {trip.detail}" for trip in report.breaker]
    else:
        lines.append("Circuit breaker: clear")
    return "\n".join(lines)


def _allowances(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> dict[Path, int]:
    """The bulk allowance the arguments name, refused as a usage error unless it
    is for a directory this run scans and comes with its count."""
    if (args.allow_bulk is None) != (args.expect is None):
        parser.error("--allow-bulk and --expect go together")
    if args.allow_bulk is None:
        return {}
    directory = Path(args.allow_bulk).expanduser().resolve()
    scanned = [scan.path for scan in hygiene_directories()]
    if directory not in scanned:
        parser.error(
            f"--allow-bulk {args.allow_bulk}: {directory} is not a directory this "
            f"run scans ({', '.join(str(path) for path in scanned)})"
        )
    return {directory: args.expect}


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        # A usage error raises SystemExit, which this does not catch.
        allowances = _allowances(parser, args)
        report = run_storage_hygiene(
            get_sync_session,
            HygieneOptions(delete=args.delete, bulk_allowances=allowances),
        )
    except Exception as error:  # noqa: BLE001 -- boundary: the operator gets a distinct exit status and the traceback in the log
        logger.exception("Storage hygiene failed")
        print(f"Storage hygiene failed: {error}", file=sys.stderr)
        return EXIT_ERROR
    print(format_report(report, args.show))
    if report.tripped:
        return EXIT_REFUSED
    if report.failed:
        return EXIT_UNLINK_FAILED
    return EXIT_OK


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
