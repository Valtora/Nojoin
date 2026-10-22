"""The clock rule: a deleting run refuses when a directory's clock trails the worker's.

On an NFS or SMB data directory the server sets each mtime, so the tests stand
in for a lagging server by moving the probe's mtime right after it is written,
before the run reads it back.
"""

from __future__ import annotations

import errno
import logging
import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path

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
from backend.utils.storage_hygiene_breaker import CLOCK_PROBE_PREFIX


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Layout:
    return make_layout(tmp_path, monkeypatch)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Engine]:
    engine = sqlite_engine(tmp_path / "hygiene.db")
    yield engine
    engine.dispose()


@pytest.fixture
def orphans(layout: Layout, db: Engine) -> list[Path]:
    """One unreferenced old file in each directory among nine named ones, so no
    other rule trips."""
    add_referenced_audio(db, layout, 9)
    add_referenced_documents(db, layout, 9)
    return [
        write_file(layout.recordings / uuid_name(".wav")),
        write_file(layout.documents / uuid_name(".pdf")),
    ]


def _after_each_probe(
    monkeypatch: pytest.MonkeyPatch, then: Callable[[Path], None]
) -> list[Path]:
    """Every probe a run writes, with ``then`` run on each once it is written."""
    probes: list[Path] = []
    write = breaker._write_probe

    def write_then(path: Path) -> None:
        write(path)
        probes.append(path)
        then(path)

    monkeypatch.setattr(breaker, "_write_probe", write_then)
    return probes


def _lagging(monkeypatch: pytest.MonkeyPatch, seconds: float) -> list[Path]:
    """The filesystem's clock ``seconds`` behind the worker's (ahead if negative)."""

    def age(path: Path) -> None:
        moment = time.time() - seconds
        os.utime(path, (moment, moment))

    return _after_each_probe(monkeypatch, age)


def _probes_left(layout: Layout) -> list[str]:
    return [
        name
        for directory in (layout.documents, layout.recordings)
        for name in os.listdir(directory)
        if name.startswith(CLOCK_PROBE_PREFIX)
    ]


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
    assert len(probes) == 2 and not any(probe.exists() for probe in probes)


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


def test_only_the_directory_whose_clock_trails_trips(
    layout: Layout, db: Engine, orphans: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    def age_documents_only(path: Path) -> None:
        if path.parent == layout.documents.resolve():
            moment = time.time() - 2 * HOUR
            os.utime(path, (moment, moment))

    _after_each_probe(monkeypatch, age_documents_only)

    report = run(db, delete=True)

    assert rules(report) == [RULE_CLOCK]
    assert report.breaker[0].detail.startswith(f"{layout.documents.resolve()}:")
    assert all(path.exists() for path in orphans)


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root writes into a read-only directory",
)
def test_a_directory_the_probe_cannot_be_written_to_refuses_deletion(
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
    # The trip is the whole story: no probe was made, so none failed to go.
    assert "could not remove its probe" not in caplog.text
    assert _probes_left(layout) == []


def test_a_probe_that_fails_after_it_is_created_is_removed_and_refuses(
    layout: Layout, db: Engine, orphans: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(path: Path) -> None:
        raise OSError(errno.ENOSPC, "No space left on device", str(path))

    probes = _after_each_probe(monkeypatch, fail)

    report = run(db, delete=True)

    assert rules(report) == [RULE_CLOCK, RULE_CLOCK]
    assert "No space left on device" in report.breaker[0].detail
    assert all(path.exists() for path in orphans)
    assert len(probes) == 2 and _probes_left(layout) == []


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
    assert [directory.stat().st_mtime_ns for directory in directories] == [
        1_000_000_000 * 1_000_000_000
    ] * 2
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
        # The probe was old enough for the grace, a past write's mtime from a
        # clock behind by days, and an unreferenced file: a candidate if listed.
        os.utime(path, (1_000_000_000, 1_000_000_000))
        assert path.exists()
        concurrent.append(run(db))
        os.utime(path, None)

    probes = _after_each_probe(monkeypatch, scan_while_it_exists)

    report = run(db, delete=True)

    assert len(probes) == 2 and len(concurrent) == 2
    assert [_seen(scan) for scan in concurrent] == [expected, expected]
    assert rules(report) == []


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
