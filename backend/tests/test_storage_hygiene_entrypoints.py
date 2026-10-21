"""The two ways storage hygiene runs: the daily cleanup task and the operator CLI.

Both run the real routine against real directories and a SQLite schema; only the
restore lock is faked, since the suite allows no Redis.
"""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

import backend.storage_hygiene as cli
import backend.utils.storage_hygiene as hygiene
import backend.worker.tasks.constants as task_constants
import backend.worker.tasks.system as system_tasks
from backend.tests.storage_hygiene_harness import (
    FakeLockStore,
    Layout,
    add_referenced_audio,
    make_layout,
    sqlite_engine,
    write_file,
)
from backend.utils.storage_hygiene import storage_hygiene_mode


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Layout:
    return make_layout(tmp_path, monkeypatch)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Engine]:
    engine = sqlite_engine(tmp_path / "hygiene.db")
    yield engine
    engine.dispose()


@pytest.fixture
def lock(monkeypatch: pytest.MonkeyPatch) -> FakeLockStore:
    """A free restore lock behind the client the entrypoints open themselves."""
    store = FakeLockStore()
    monkeypatch.setattr(hygiene, "_open_redis_client", store.open)
    return store


@pytest.fixture
def orphan(layout: Layout, db: Engine) -> Path:
    """One unreferenced file among ten, so the bulk rule stays clear."""
    add_referenced_audio(db, layout, 9)
    return write_file(layout.recordings / "orphan.wav")


@pytest.mark.parametrize(
    "value,mode",
    [
        (None, "flag"),
        ("", "flag"),
        ("  ", "flag"),
        ("flag", "flag"),
        ("delete", "delete"),
        (" DELETE ", "delete"),
        ("remove", "flag"),
    ],
)
def test_the_setting_turns_deletion_on_only_when_it_says_delete(
    monkeypatch: pytest.MonkeyPatch, value: str | None, mode: str
) -> None:
    if value is None:
        monkeypatch.delenv("NOJOIN_STORAGE_HYGIENE", raising=False)
    else:
        monkeypatch.setenv("NOJOIN_STORAGE_HYGIENE", value)

    assert storage_hygiene_mode() == mode


def _cli(
    db: Engine, monkeypatch: pytest.MonkeyPatch, capsys, *argv: str
) -> tuple[int, str]:
    monkeypatch.setattr(cli, "get_sync_session", lambda: Session(db))
    code = cli.main(list(argv))
    return code, capsys.readouterr().out


@pytest.mark.usefixtures("lock")
def test_the_cli_is_a_dry_run_by_default(
    db: Engine, orphan: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    code, out = _cli(db, monkeypatch, capsys)

    assert code == 0
    assert orphan.exists()
    assert "dry run, nothing removed" in out
    assert "Total reclaimable: 10 B (10 bytes)" in out
    assert "Circuit breaker: clear" in out


@pytest.mark.usefixtures("lock")
def test_the_cli_deletes_when_asked(
    db: Engine, orphan: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    code, out = _cli(db, monkeypatch, capsys, "--delete")

    assert code == 0
    assert not orphan.exists()
    audio_row = next(line for line in out.splitlines() if line.startswith("audio"))
    assert audio_row.split() == ["audio", "10", "1", "10", "B", "1", "0", "0"]


@pytest.mark.usefixtures("lock")
def test_the_cli_exits_nonzero_when_the_bulk_rule_trips_until_allowed(
    layout: Layout, db: Engine, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    add_referenced_audio(db, layout, 1)
    orphan = write_file(layout.recordings / "orphan.wav")

    for argv in ([], ["--delete"]):
        code, out = _cli(db, monkeypatch, capsys, *argv)
        assert code == 1, argv
        assert "bulk:" in out
        assert orphan.exists()

    code, _ = _cli(db, monkeypatch, capsys, "--delete", "--allow-bulk")

    assert code == 0
    assert not orphan.exists()


@pytest.mark.usefixtures("lock")
def test_allow_bulk_lifts_no_other_rule(
    layout: Layout, db: Engine, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    add_referenced_audio(db, layout, 1)
    monkeypatch.setenv("DOCUMENTS_DIR", str(layout.data))

    code, out = _cli(db, monkeypatch, capsys, "--delete", "--allow-bulk")

    assert code == 1
    assert "data_root:" in out
    assert (layout.data / "config.json").exists()
    assert (layout.data / "celerybeat-schedule").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
@pytest.mark.usefixtures("lock")
def test_the_cli_exits_nonzero_when_a_file_cannot_be_removed(
    layout: Layout, db: Engine, orphan: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    layout.recordings.chmod(0o500)
    try:
        code, _ = _cli(db, monkeypatch, capsys, "--delete")
    finally:
        layout.recordings.chmod(0o700)

    assert code == 1
    assert orphan.exists()


@pytest.fixture(params=["flag", "delete"])
def daily_mode(
    request: pytest.FixtureRequest,
    db: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> str:
    """The daily cleanup task pointed at ``db``, with the setting at each value."""
    monkeypatch.setenv("NOJOIN_STORAGE_HYGIENE", request.param)
    monkeypatch.setattr(task_constants, "get_sync_session", lambda: Session(db))
    monkeypatch.setattr(system_tasks, "get_sync_session", lambda: Session(db))
    # The task's other sweeps: keep them off the session of any earlier test
    # and out of the real temp directory.
    monkeypatch.setattr(system_tasks.cleanup_temp_recordings, "_session", None)
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp"))
    return request.param


def test_the_daily_cleanup_runs_storage_hygiene_in_the_configured_mode(
    orphan: Path,
    lock: FakeLockStore,
    daily_mode: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    removed = daily_mode == "delete"

    with caplog.at_level(logging.INFO, logger=hygiene.__name__):
        system_tasks.cleanup_temp_recordings.run()

    assert orphan.exists() is not removed
    assert lock.reads == (2 if removed else 1)
    summary = [r.getMessage() for r in caplog.records if r.name == hygiene.__name__]
    assert len(summary) == 1
    assert ("(delete)" if removed else "(report only)") in summary[0]
