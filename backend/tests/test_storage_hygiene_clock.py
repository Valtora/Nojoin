"""The clock rule: a deleting run refuses when a directory's clock trails the worker's.

On an NFS or SMB data directory the filesystem may stamp each mtime with
another clock, so the tests stand in for a lagging one by moving the probe's
mtime after it is written, just before the run reads it back.
"""

from __future__ import annotations

import errno
import logging
import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

import backend.storage_hygiene as cli
import backend.utils.storage_hygiene as hygiene
import backend.utils.storage_hygiene_breaker as breaker
from backend.tests.storage_hygiene_harness import (
    HOUR,
    FakeLockStore,
    Layout,
    add_referenced_audio,
    add_referenced_documents,
    make_layout,
    rules,
    run,
    sqlite_engine,
    uuid_name,
    write_file,
)
from backend.utils.storage_hygiene import (
    RULE_CLOCK,
    RULE_RESTORE_LOCK,
    HygieneReport,
)
from backend.utils.storage_hygiene_breaker import CLOCK_PROBE_NAME

NOT_ROOT = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root writes into a read-only directory",
)


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Layout:
    return make_layout(tmp_path, monkeypatch)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Engine]:
    engine = sqlite_engine(tmp_path / "hygiene.db")
    yield engine
    engine.dispose()


@pytest.fixture
def library(layout: Layout, db: Engine) -> None:
    """Nine named old files in each directory, so one orphan trips nothing else."""
    add_referenced_audio(db, layout, 9)
    add_referenced_documents(db, layout, 9)


@pytest.fixture
def orphans(layout: Layout, library: None) -> list[Path]:
    """One unreferenced old file in each directory."""
    return [
        write_file(layout.recordings / uuid_name(".wav")),
        write_file(layout.documents / uuid_name(".pdf")),
    ]


def _before_each_reading(
    monkeypatch: pytest.MonkeyPatch, then: Callable[[Path], None]
) -> list[Path]:
    """Every probe a run reads back, with ``then`` run on each once it is
    written and before its mtime is read."""
    probes: list[Path] = []
    read = breaker._probe_mtime_ns

    def then_read(path: Path) -> int:
        probes.append(path)
        then(path)
        return read(path)

    monkeypatch.setattr(breaker, "_probe_mtime_ns", then_read)
    return probes


def _lagging(monkeypatch: pytest.MonkeyPatch, seconds: float) -> list[Path]:
    """The filesystem's clock ``seconds`` behind the worker's (ahead if negative)."""

    def age(path: Path) -> None:
        moment = time.time() - seconds
        os.utime(path, (moment, moment))

    return _before_each_reading(monkeypatch, age)


def _probes_left(layout: Layout) -> list[Path]:
    return [
        path
        for directory in (layout.documents, layout.recordings)
        if (path := directory / CLOCK_PROBE_NAME).exists()
    ]


# --- the lag ------------------------------------------------------------------


@pytest.mark.parametrize("lag", [HOUR + 100, 3 * 24 * HOUR], ids=["hour", "days"])
def test_a_clock_over_an_hour_behind_refuses_deletion(
    layout: Layout,
    db: Engine,
    orphans: list[Path],
    monkeypatch: pytest.MonkeyPatch,
    lag: float,
) -> None:
    probes = _lagging(monkeypatch, lag)

    report = run(db, delete=True)

    assert rules(report) == [RULE_CLOCK, RULE_CLOCK]
    assert [trip.detail.split(":")[0] for trip in report.breaker] == [
        str(layout.documents.resolve()),
        str(layout.recordings.resolve()),
    ]
    assert f"{lag / HOUR:.1f} hours behind" in report.breaker[0].detail
    assert all(path.exists() for path in orphans)
    assert len(probes) == 2 and _probes_left(layout) == []


@pytest.mark.parametrize(
    "lag", [HOUR - 100, 0, -3 * 24 * HOUR], ids=["under_an_hour", "none", "ahead"]
)
def test_a_clock_under_an_hour_behind_or_ahead_lets_the_run_delete(
    layout: Layout,
    db: Engine,
    orphans: list[Path],
    monkeypatch: pytest.MonkeyPatch,
    lag: float,
) -> None:
    probes = _lagging(monkeypatch, lag)

    report = run(db, delete=True)

    assert rules(report) == []
    assert not any(path.exists() for path in orphans)
    assert report.deleted == 2
    assert len(probes) == 2 and _probes_left(layout) == []


@pytest.mark.usefixtures("library")
@pytest.mark.parametrize(("lag", "trips"), [(3600, False), (3601, True)])
def test_the_limit_is_an_hour_exactly(
    layout: Layout,
    db: Engine,
    monkeypatch: pytest.MonkeyPatch,
    lag: int,
    trips: bool,
) -> None:
    orphan = write_file(layout.recordings / uuid_name(".wav"))
    fixed = int(time.time())
    monkeypatch.setattr(breaker, "time", SimpleNamespace(time=lambda: float(fixed)))

    def stamp(path: Path) -> None:
        moment_ns = (fixed - lag) * 1_000_000_000
        os.utime(path, ns=(moment_ns, moment_ns))

    _before_each_reading(monkeypatch, stamp)

    report = run(db, delete=True, now=float(fixed))

    assert rules(report) == ([RULE_CLOCK] if trips else [])
    assert orphan.exists() is trips


def test_a_worker_clock_stepped_back_since_the_cutoff_still_trips(
    layout: Layout, db: Engine, library: None
) -> None:
    """The grace was measured from a worker clock 25 hours fast, and NTP put it
    right before the probe: the probe reads true, the cutoff does not."""
    fresh = write_file(layout.recordings / uuid_name(".wav"), age_hours=0)

    report = run(db, delete=True, now=time.time() + 25 * HOUR)

    assert report.directories[1].candidates == 1
    assert rules(report) == [RULE_CLOCK]
    assert "25.0 hours behind" in report.breaker[0].detail
    assert fresh.exists()


def test_only_the_directory_whose_clock_trails_trips(
    layout: Layout, db: Engine, orphans: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    def age_documents_only(path: Path) -> None:
        if path.parent == layout.documents.resolve():
            moment = time.time() - 2 * HOUR
            os.utime(path, (moment, moment))

    _before_each_reading(monkeypatch, age_documents_only)

    report = run(db, delete=True)

    assert rules(report) == [RULE_CLOCK]
    assert report.breaker[0].detail.startswith(f"{layout.documents.resolve()}:")
    assert all(path.exists() for path in orphans)


# --- which directories are probed -----------------------------------------------


@NOT_ROOT
def test_a_directory_with_files_to_remove_and_no_room_for_the_probe_refuses(
    layout: Layout,
    db: Engine,
    orphans: list[Path],
    caplog: pytest.LogCaptureFixture,
) -> None:
    layout.documents.chmod(0o500)
    try:
        with caplog.at_level(logging.INFO, logger=hygiene.__name__):
            report = run(db, delete=True)
    finally:
        layout.documents.chmod(0o700)

    assert rules(report) == [RULE_CLOCK]
    detail = report.breaker[0].detail
    assert detail.startswith(f"{layout.documents.resolve()}: could not write")
    assert "Permission denied" in detail
    assert all(path.exists() for path in orphans)
    # No probe was made there, so none failed to go.
    assert "could not remove its probe" not in caplog.text
    assert _probes_left(layout) == []


@NOT_ROOT
def test_a_read_only_directory_with_nothing_to_remove_blocks_nothing(
    layout: Layout, db: Engine, library: None
) -> None:
    orphan = write_file(layout.recordings / uuid_name(".wav"))
    layout.documents.chmod(0o500)
    try:
        report = run(db, delete=True)
    finally:
        layout.documents.chmod(0o700)

    assert rules(report) == []
    assert not orphan.exists()


def test_a_deleting_run_with_nothing_to_remove_writes_nothing(
    layout: Layout, db: Engine, library: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    probes = _lagging(monkeypatch, 3 * 24 * HOUR)
    directories = (layout.documents, layout.recordings)
    for directory in directories:
        os.utime(directory, (1_000_000_000, 1_000_000_000))

    report = run(db, delete=True)

    assert probes == [] and rules(report) == []
    assert [d.stat().st_mtime_ns for d in directories] == [10**18, 10**18]


class _MovingLockStore(FakeLockStore):
    """A free lock whose second read, the one just before the clock probe,
    first moves ``directory`` away."""

    def __init__(self, directory: Path, to: Path) -> None:
        super().__init__(False)
        self.directory, self.to = directory, to

    def exists(self, key: str) -> int:
        if self.reads == 1:
            self.directory.rename(self.to)
        return super().exists(key)


def test_a_directory_that_goes_after_the_listing_trips(
    layout: Layout, db: Engine, orphans: list[Path]
) -> None:
    moved = layout.app / "documents-moved"

    report = run(db, delete=True, lock=_MovingLockStore(layout.documents, moved))

    assert report.directories[0].candidates == 1
    assert rules(report) == [RULE_CLOCK]
    assert report.breaker[0].detail.startswith(f"{layout.documents.resolve()}:")
    assert "No such file or directory" in report.breaker[0].detail
    assert orphans[0].exists()
    assert not (moved / CLOCK_PROBE_NAME).exists()


def test_a_directory_that_is_not_there_is_not_probed(
    layout: Layout, db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_referenced_audio(db, layout, 9)
    orphan = write_file(layout.recordings / uuid_name(".wav"))
    layout.documents.rmdir()
    probes = _lagging(monkeypatch, 0)

    report = run(db, delete=True)

    assert rules(report) == []
    assert not orphan.exists()
    assert [probe.parent for probe in probes] == [layout.recordings.resolve()]


# --- writing and removing the probe -----------------------------------------------


def test_a_probe_that_fails_after_it_is_created_is_removed_and_refuses(
    layout: Layout, db: Engine, orphans: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(descriptor: int) -> None:
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(breaker, "_write_probe", fail)

    report = run(db, delete=True)

    assert rules(report) == [RULE_CLOCK, RULE_CLOCK]
    assert "Input/output error" in report.breaker[0].detail
    assert all(path.exists() for path in orphans)
    assert _probes_left(layout) == []


@pytest.mark.parametrize(
    ("code", "lag"),
    [
        (errno.ENOSPC, 0),
        (errno.EDQUOT, 0),
        (errno.ENOSPC, 2 * HOUR),
        (errno.EDQUOT, 2 * HOUR),
    ],
    ids=["full", "quota", "full_behind", "quota_behind"],
)
def test_a_full_volume_measures_the_empty_probe(
    db: Engine,
    orphans: list[Path],
    monkeypatch: pytest.MonkeyPatch,
    code: int,
    lag: float,
) -> None:
    trips = lag > HOUR

    def full(descriptor: int) -> None:
        raise OSError(code, os.strerror(code))

    monkeypatch.setattr(breaker, "_write_probe", full)
    probes = _lagging(monkeypatch, lag)

    report = run(db, delete=True)

    assert len(probes) == 2
    assert rules(report) == ([RULE_CLOCK, RULE_CLOCK] if trips else [])
    assert all(path.exists() is trips for path in orphans)
    assert not any(probe.exists() for probe in probes)


def test_a_file_already_at_the_probe_name_is_left_alone_and_refuses(
    layout: Layout, db: Engine, orphans: list[Path]
) -> None:
    planted = layout.recordings / CLOCK_PROBE_NAME
    planted.write_bytes(b"not this run's\n")
    os.utime(planted, (1_000_000_000, 1_000_000_000))

    report = run(db, delete=True)

    assert rules(report) == [RULE_CLOCK]
    assert report.breaker[0].detail.startswith(
        f"{layout.recordings.resolve()}: {CLOCK_PROBE_NAME} is already there"
    )
    assert planted.read_bytes() == b"not this run's\n"
    assert planted.stat().st_mtime_ns == 10**18
    assert all(path.exists() for path in orphans)


def test_a_probe_that_cannot_be_removed_stops_the_next_run(
    layout: Layout,
    db: Engine,
    orphans: list[Path],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    unlink = os.unlink

    def unlink_all_but_probes(path: str | os.PathLike[str]) -> None:
        if Path(path).name == CLOCK_PROBE_NAME:
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
        unlink(path)

    with monkeypatch.context() as patched:
        patched.setattr(os, "unlink", unlink_all_but_probes)
        with caplog.at_level(logging.WARNING, logger=hygiene.__name__):
            first = run(db, delete=True)
    later = write_file(layout.recordings / uuid_name(".wav"))
    second = run(db, delete=True)

    assert rules(first) == [] and first.deleted == 2
    assert "could not remove its probe" in caplog.text
    assert len(_probes_left(layout)) == 2
    assert rules(second) == [RULE_CLOCK]
    assert second.breaker[0].detail.startswith(
        f"{layout.recordings.resolve()}: {CLOCK_PROBE_NAME} is already there"
    )
    assert later.exists()


# --- what writes nothing ------------------------------------------------------


@pytest.mark.parametrize(
    "deleting", [False, True], ids=["report_only", "deleting_during_a_restore"]
)
def test_a_run_that_removes_nothing_writes_nothing(
    layout: Layout,
    db: Engine,
    orphans: list[Path],
    monkeypatch: pytest.MonkeyPatch,
    deleting: bool,
) -> None:
    probes = _lagging(monkeypatch, 3 * 24 * HOUR)
    directories = (layout.documents, layout.recordings)
    for directory in directories:
        os.utime(directory, (1_000_000_000, 1_000_000_000))

    # A deleting run that the restore lock stops writes no probe either.
    report = run(db, delete=deleting, lock=FakeLockStore(deleting))

    assert probes == []
    assert rules(report) == ([RULE_RESTORE_LOCK] if deleting else [])
    assert [d.stat().st_mtime_ns for d in directories] == [10**18, 10**18]
    assert all(path.exists() for path in orphans)


def test_a_restore_that_begins_during_the_listing_meets_no_probe(
    layout: Layout, db: Engine, orphans: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    probes = _lagging(monkeypatch, 0)
    lock = FakeLockStore(False, True)

    report = run(db, delete=True, lock=lock)

    assert lock.reads == 2
    assert probes == []
    assert rules(report) == [RULE_RESTORE_LOCK]
    assert all(path.exists() for path in orphans)


def _seen(report: HygieneReport) -> tuple[object, ...]:
    return (
        [(d.path, d.files, d.old, d.candidates) for d in report.directories],
        sorted(str(flagged.path) for flagged in report.flagged),
        {name: entry.scanned for name, entry in report.categories.items()},
    )


def test_a_concurrent_scan_never_lists_the_probe(
    layout: Layout, db: Engine, orphans: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = _seen(run(db))
    concurrent: list[HygieneReport] = []

    def scan_while_it_exists(path: Path) -> None:
        # Old enough for the grace, as from a clock days behind: a candidate
        # if a listing included it.
        os.utime(path, (1_000_000_000, 1_000_000_000))
        assert path.exists()
        concurrent.append(run(db))
        os.utime(path, None)

    probes = _before_each_reading(monkeypatch, scan_while_it_exists)

    report = run(db, delete=True)

    assert len(probes) == 2 and len(concurrent) == 2
    assert [_seen(scan) for scan in concurrent] == [expected, expected]
    assert rules(report) == []


# --- how a trip is reported -----------------------------------------------------


def test_a_lagging_clock_is_an_error_naming_the_rule(
    layout: Layout,
    db: Engine,
    orphans: list[Path],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _lagging(monkeypatch, 2 * HOUR)

    with caplog.at_level(logging.INFO, logger=hygiene.__name__):
        run(db, delete=True)

    trips = [
        r
        for r in caplog.records
        if r.name == hygiene.__name__ and "circuit breaker tripped" in r.getMessage()
    ]
    assert [r.levelno for r in trips] == [logging.ERROR]
    message = trips[0].getMessage()
    assert message.startswith("Storage hygiene circuit breaker tripped (clock): ")


def test_the_cli_exits_1_when_the_clock_trails(
    layout: Layout,
    db: Engine,
    orphans: list[Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(hygiene, "_open_redis_client", FakeLockStore().open)
    monkeypatch.setattr(cli, "get_sync_session", lambda: Session(db))
    _lagging(monkeypatch, 2 * HOUR)

    code = cli.main(["--delete"])

    out = capsys.readouterr().out
    assert code == cli.EXIT_REFUSED
    assert "Circuit breaker tripped, nothing removed:" in out
    assert f"  - clock: {layout.documents.resolve()}: its clock is 2.0 hours" in out
    assert all(path.exists() for path in orphans)
