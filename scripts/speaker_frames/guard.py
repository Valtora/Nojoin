"""What the analysis may write and say: summary.json, numbers and ids.

``install`` adds an audit hook that refuses, for the rest of the process,
every write but the one summary file, every change to the file system,
every new process and every network call. ``check_summary`` refuses a
summary holding any string that is not a recording id, a platform, a result
word or a SHA-256, so no name, text or picture can leave in it.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

SUMMARY_NAME = "summary.json"
MAX_SUMMARY_BYTES = 1_000_000

WORDS = frozenset(
    {
        "teams",
        "zoom",
        "meet",
        "unknown",
        "calibration",
        "held_out",
        "pass",
        "fail",
        "inconclusive",
        "not_scored",
        "speaker-frames-summary-1",
    }
)
KEY = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# Audit events that change the file system, start a process or reach a
# network. os.fork stays allowed: a forked worker inherits this hook.
BLOCKED = frozenset(
    {
        "os.chmod",
        "os.chown",
        "os.exec",
        "os.link",
        "os.mkdir",
        "os.posix_spawn",
        "os.remove",
        "os.rename",
        "os.rmdir",
        "os.spawn",
        "os.symlink",
        "os.system",
        "os.truncate",
        "os.utime",
        "shutil.copyfile",
        "shutil.copymode",
        "shutil.copystat",
        "shutil.copytree",
        "shutil.make_archive",
        "shutil.move",
        "shutil.rmtree",
        "shutil.unpack_archive",
        "socket.bind",
        "socket.connect",
        "socket.getaddrinfo",
        "socket.gethostbyaddr",
        "socket.gethostbyname",
        "socket.sendto",
        "subprocess.Popen",
        "tempfile.mkdtemp",
        "tempfile.mkstemp",
        "urllib.Request",
    }
)
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC


class Refused(PermissionError):
    """The analysis tried something its guard does not allow."""


def _writes(mode: object, flags: object) -> bool:
    if isinstance(mode, str) and any(c in mode for c in "wax+"):
        return True
    return isinstance(flags, int) and bool(flags & WRITE_FLAGS)


def install(summary: Path) -> None:
    """From now on, refuse every write but ``summary`` and every way out."""
    allowed = os.path.realpath(summary)

    def hook(event: str, args: tuple[Any, ...]) -> None:
        if event == "open":
            path, mode, flags = (tuple(args) + (None, None, None))[:3]
            if _writes(mode, flags) and (
                not isinstance(path, (str, bytes, os.PathLike))
                or os.path.realpath(os.fsdecode(path)) != allowed
            ):
                raise Refused(f"write refused: {path!r}")
        elif event in BLOCKED:
            raise Refused(f"refused: {event}")

    sys.addaudithook(hook)


def check_summary(summary: object, recordings: Iterable[str]) -> None:
    """Raise ValueError unless the summary holds only what it may."""
    known = set(recordings)
    _check(summary, known, "summary")
    if len(json.dumps(summary).encode()) > MAX_SUMMARY_BYTES:
        raise ValueError("summary is too large")


def _check(value: object, known: set[str], where: str) -> None:
    if isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            _check(item, known, f"{where}[{i}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not (
                KEY.match(key) or key in known or key in WORDS
            ):
                raise ValueError(f"{where}: a key that is not allowed")
            _check(item, known, f"{where}.{key}")
    else:
        _check_scalar(value, known, where)


def _check_scalar(value: object, known: set[str], where: str) -> None:
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{where}: not a finite number")
        return
    if isinstance(value, str):
        if value in WORDS or value in known or SHA256.match(value):
            return
        raise ValueError(f"{where}: a string that is not an id, a platform or a result")
    raise ValueError(f"{where}: a {type(value).__name__} is not allowed")


def write_summary(
    out_dir: Path, summary: dict[str, Any], recordings: Iterable[str]
) -> Path:
    """Check the summary, then write it as the only file in ``out_dir``."""
    ids = list(recordings)
    if not all(UUID.match(r) for r in ids):
        raise ValueError("a recording id is not a public id")
    check_summary(summary, ids)
    path = out_dir / SUMMARY_NAME
    with open(path, "x", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=1, sort_keys=True)
        handle.write("\n")
    return path
