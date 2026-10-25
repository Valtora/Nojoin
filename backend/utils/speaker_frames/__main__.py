"""Purge and audit the screen study's frames from a shell.

    docker compose exec --user appuser api python -m backend.utils.speaker_frames audit
    docker compose exec --user appuser api python -m backend.utils.speaker_frames purge --all
    docker compose exec --user appuser api python -m backend.utils.speaker_frames purge <public_id>

``purge`` writes the SHA-256 of every frame it is about to delete to a new list
in ``speaker_frame_hashes/``, then deletes the frames and their state. ``audit``
prints the directories present with their counts and sizes, the state files and
the hash lists, and never any frame content. ``audit --scan <dir>`` also hashes
every JPEG under a directory, and every JPEG inside a ZIP there such as a
backup, and names each file that matches a saved hash.

Exit status: 0 when the command finished, 1 when ``audit --scan`` found a match,
2 on a usage error.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import zipfile
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from .settings import sampling_enabled
from .storage import (
    INDEX_FILENAME,
    delete_recording_frames,
    frame_hashes,
    frames_root,
    hashes_root,
    is_public_id,
    recording_dirs,
    recording_stopped_at,
    scan_summary,
    state_files,
)

EXIT_OK = 0
EXIT_MATCH = 1
EXIT_USAGE = 2

_JPEG_SUFFIXES = (".jpg", ".jpeg")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.utils.speaker_frames",
        description="Purge or audit the screen study's frames. Prints no frame content.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    purge = commands.add_parser(
        "purge", help="save the frames' hashes, then delete the frames"
    )
    target = purge.add_mutually_exclusive_group(required=True)
    target.add_argument("--all", action="store_true", help="every recording's frames")
    target.add_argument("public_id", nargs="?", help="one recording's public id")

    audit = commands.add_parser("audit", help="report what is left, with no content")
    audit.add_argument(
        "--scan",
        metavar="DIRECTORY",
        action="append",
        default=[],
        help="also hash every JPEG under DIRECTORY, ZIP members included, against the saved hashes",
    )
    return parser


def _write_hash_list(entries: list[str]) -> Path:
    root = hashes_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = root / f"purge-{stamp}.sha256"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        for line in entries:
            handle.write(line + "\n")
    return path


def purge(target: str | None, *, everything: bool) -> int:
    if everything:
        directories = [path for path in recording_dirs() if is_public_id(path.name)]
    else:
        if target is None or not is_public_id(target):
            print("Not a recording public id.", file=sys.stderr)
            return EXIT_USAGE
        candidate = frames_root() / target
        directories = [candidate] if candidate.is_dir() else []

    if sampling_enabled():
        print(
            "speaker_frame_sampling is still owner, so a capture with Screen study "
            "ticked keeps adding frames. Set it to off to end the study."
        )

    hashes: list[str] = []
    for directory in directories:
        hashes.extend(
            f"{digest}  {directory.name}/{name}"
            for digest, name in frame_hashes(directory)
        )
    if hashes:
        hash_list = _write_hash_list(hashes)
        print(f"Saved {len(hashes)} frame hashes to {hash_list}")

    removed = 0
    for directory in directories:
        removed += delete_recording_frames(directory.name, reason="purge")
    if not everything and target is not None:
        # A recording whose frames were already gone may still hold its state.
        delete_recording_frames(target, reason="purge")
    print(f"Deleted {removed} frames from {len(directories)} recordings.")
    return EXIT_OK


def _index_lines(directory: Path) -> int:
    try:
        with open(directory / INDEX_FILENAME, encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except FileNotFoundError:
        return 0


def _saved_hashes() -> tuple[set[str], list[tuple[str, int]]]:
    known: set[str] = set()
    lists: list[tuple[str, int]] = []
    try:
        paths = sorted(hashes_root().glob("*.sha256"))
    except OSError:
        paths = []
    for path in paths:
        count = 0
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                digest = line.split(" ", 1)[0].strip()
                if len(digest) == 64:
                    known.add(digest)
                    count += 1
        lists.append((path.name, count))
    return known, lists


def _sha256_stream(handle: IO[bytes]) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: handle.read(1 << 16), b""):
        digest.update(block)
    return digest.hexdigest()


def _scan(directory: Path, known: set[str]) -> Iterator[tuple[str, bool]]:
    """``(location, matches)`` for each JPEG under ``directory``, ZIP members included."""
    for root, _dirs, files in os.walk(directory):
        for name in files:
            path = Path(root) / name
            lower = name.lower()
            try:
                if lower.endswith(_JPEG_SUFFIXES):
                    with open(path, "rb") as handle:
                        yield str(path), _sha256_stream(handle) in known
                elif lower.endswith(".zip"):
                    with zipfile.ZipFile(path) as archive:
                        for member in archive.namelist():
                            if member.lower().endswith(_JPEG_SUFFIXES):
                                with archive.open(member) as handle:
                                    yield (
                                        f"{path}!{member}",
                                        _sha256_stream(handle) in known,
                                    )
            except (OSError, zipfile.BadZipFile):
                continue


def audit(scan_dirs: Sequence[str]) -> int:
    root = frames_root()
    print(f"Frames directory: {root} ({'present' if root.is_dir() else 'absent'})")
    totals = [0, 0]
    directories = recording_dirs()
    for directory in directories:
        summary = scan_summary(directory)
        totals[0] += summary.frames
        totals[1] += summary.bytes
        if not is_public_id(directory.name):
            print(f"  {directory.name}  not a recording id, left alone")
            continue
        stopped = recording_stopped_at(directory.name)
        print(
            f"  {directory.name}  frames={summary.frames}  bytes={summary.bytes}  "
            f"indexed={_index_lines(directory)}  "
            f"stopped={stopped.isoformat() if stopped else 'not recorded'}"
        )
    print(
        f"Recordings with frames: {len(directories)}, frames: {totals[0]}, "
        f"bytes: {totals[1]}"
    )
    print(f"State files: {len(state_files())}")
    known, lists = _saved_hashes()
    print(f"Hash lists: {len(lists)}")
    for name, count in lists:
        print(f"  {name}  hashes={count}")

    status = EXIT_OK
    for scan_dir in scan_dirs:
        checked = 0
        matches: list[str] = []
        for location, matched in _scan(Path(scan_dir), known):
            checked += 1
            if matched:
                matches.append(location)
        print(
            f"Scanned {scan_dir}: {checked} JPEG files checked, "
            f"{len(matches)} match a saved hash"
        )
        for location in matches:
            print(f"  match: {location}")
        if matches:
            status = EXIT_MATCH
    if scan_dirs and not known:
        print("No saved hashes to compare against.")
    return status


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exit_:
        return EXIT_USAGE if exit_.code else EXIT_OK
    if args.command == "purge":
        return purge(args.public_id, everything=args.all)
    return audit(args.scan)


if __name__ == "__main__":
    sys.exit(main())
