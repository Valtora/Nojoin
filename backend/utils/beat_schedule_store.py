"""Celery beat's schedule, kept in one JSON file that is replaced atomically.

Celery's default scheduler keeps its state in a ``shelve``, so in whichever
``dbm`` module the Python build has. The worker image's Python has only
``dbm.ndbm``, backed by Berkeley DB, which holds its changes in memory until the
database is closed and offers no way to flush them sooner. Beat closes it only
on a clean stop. When beat is killed instead (``docker stop`` escalating to
SIGKILL while worker-io is still busy, an OOM kill, a crash), the file is left
half written: the next start cannot read its entries, fails to write them with
``cannot add item to database``, and Celery deletes the file, which starts every
interval again (#917).

This scheduler keeps what beat needs across a restart, each entry's last run and
run count, in a JSON file written to a sibling, flushed to disk and renamed into
place. A kill at any moment leaves either the previous file or the next one.
"""

from __future__ import annotations

import dbm
import json
import logging
import os
import shelve
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from celery.beat import Scheduler

logger = logging.getLogger(__name__)

STORE_SUFFIX = ".json"
STORE_VERSION = 1

# The files each dbm flavour keeps for a shelve opened at ``<base>``. Only the
# set belonging to the flavour dbm.whichdb() recognises is removed, so a file
# that merely shares the name is never touched.
_LEGACY_SUFFIXES = {
    "dbm.gnu": ("",),
    "dbm.ndbm": (".db", ".pag", ".dir"),
    "dbm.dumb": (".dat", ".dir", ".bak"),
}

RunState = dict[str, tuple[datetime, int]]


def _aware(value: datetime) -> datetime:
    # Beat records aware UTC times; treat a naive one as UTC rather than local.
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _parse_store(raw: bytes) -> RunState:
    document = json.loads(raw)
    if not isinstance(document, dict) or document.get("version") != STORE_VERSION:
        raise ValueError("not a version 1 schedule")
    entries = document.get("entries")
    if not isinstance(entries, dict):
        raise ValueError("no entries")
    state: RunState = {}
    for name, saved in entries.items():
        try:
            last_run_at = _aware(datetime.fromisoformat(saved["last_run_at"]))
            total_run_count = int(saved["total_run_count"])
        except (KeyError, TypeError, ValueError):
            logger.warning(
                "Beat schedule entry %r is unreadable; it starts afresh", name
            )
            continue
        state[name] = (last_run_at, total_run_count)
    return state


def _write_atomically(path: Path, text: str) -> None:
    # A fixed sibling name, so a write cut short is overwritten by the next one
    # rather than left behind in the data directory.
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _read_legacy(base: str) -> tuple[RunState, list[Path]]:
    """Read the shelve an older Nojoin kept at ``base``, and name its files.

    Returns the run state it holds, empty when it cannot be read, and the files
    to remove once the JSON store has been written.
    """
    try:
        flavour = dbm.whichdb(base)
    except OSError:
        return {}, []
    if not flavour:
        return {}, []
    files = [
        Path(base + suffix)
        for suffix in _LEGACY_SUFFIXES.get(flavour, ())
        if Path(base + suffix).exists()
    ]
    state: RunState = {}
    try:
        with shelve.open(base, flag="r") as store:
            for name, entry in store["entries"].items():
                state[name] = (_aware(entry.last_run_at), int(entry.total_run_count))
    except Exception as exc:  # noqa: BLE001 -- boundary: a missing dbm module, damaged pages or a bad pickle all mean the old schedule is lost; beat must still start
        logger.warning(
            "Could not read the previous beat schedule %s (%s: %s); every interval starts afresh",
            base,
            type(exc).__name__,
            exc,
        )
        return {}, files
    logger.info("Carried %d beat entries over from %s", len(state), base)
    return state, files


class JsonFileScheduler(Scheduler):
    """Beat scheduler whose state survives being killed at any moment.

    Takes the same ``-s`` path as Celery's default and stores the schedule at
    that path plus ``.json``. On the first start without that file it carries
    the last runs over from the shelve Celery's default left at the path, then
    removes it.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.schedule_filename: str | None = kwargs.get("schedule_filename")
        super().__init__(*args, **kwargs)

    @property
    def _base(self) -> str:
        return self.schedule_filename or self.app.conf.beat_schedule_filename

    @property
    def store_path(self) -> Path:
        return Path(self._base + STORE_SUFFIX)

    def setup_schedule(self) -> None:
        state, legacy_files = self._load()
        # The order Celery's PersistentScheduler uses: merge_inplace drops any
        # entry the configuration does not name, default entries included.
        self.merge_inplace(self.app.conf.beat_schedule)
        self.install_default_entries(self.schedule)
        for name, entry in self.schedule.items():
            if name in state:
                entry.last_run_at, entry.total_run_count = state[name]
        if self._save():
            for path in legacy_files:
                path.unlink(missing_ok=True)

    def _load(self) -> tuple[RunState, list[Path]]:
        path = self.store_path
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return _read_legacy(self._base)
        except OSError as exc:
            logger.error(
                "Could not read beat schedule %s (%s); every interval starts afresh",
                path,
                exc,
            )
            return {}, []
        try:
            return _parse_store(raw), []
        except ValueError as exc:
            logger.error(
                "Beat schedule %s is unreadable (%s); every interval starts afresh",
                path,
                exc,
            )
            return {}, []

    def _save(self) -> bool:
        entries = {
            name: {
                "last_run_at": entry.last_run_at.isoformat(),
                "total_run_count": entry.total_run_count,
            }
            for name, entry in self.schedule.items()
        }
        text = json.dumps(
            {"version": STORE_VERSION, "entries": entries}, indent=2, sort_keys=True
        )
        try:
            _write_atomically(self.store_path, text + "\n")
        except OSError as exc:
            # Scheduling carries on from memory; only what a restart keeps is at risk.
            logger.error("Could not save beat schedule %s: %s", self.store_path, exc)
            return False
        return True

    def apply_entry(self, entry: Any, producer: Any = None) -> None:
        super().apply_entry(entry, producer=producer)
        # Record the run now rather than at the next periodic sync, up to five
        # minutes away, so a kill just after a dispatch does not send the task
        # again on the next start.
        self._save()

    def sync(self) -> None:
        self._save()

    @property
    def info(self) -> str:
        return f"    . db -> {self.store_path}"
