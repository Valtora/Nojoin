"""The two ways storage hygiene runs: the daily cleanup task and the operator CLI.

Both run the real routine against real directories and a SQLite schema; only the
restore lock is faked, since the suite allows no Redis.
"""

from __future__ import annotations

import logging
import os
import tempfile
import uuid
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
    add_recording,
    add_referenced_audio,
    add_referenced_documents,
    make_layout,
    sqlite_engine,
    uuid_name,
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
    return write_file(layout.recordings / uuid_name(".wav"))


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
def test_the_cli_is_a_dry_run_by_default_and_lists_the_files(
    db: Engine, orphan: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    code, out = _cli(db, monkeypatch, capsys)

    assert code == 0
    assert orphan.exists()
    assert "dry run, nothing removed" in out
    listed = [line for line in out.splitlines() if str(orphan.resolve()) in line]
    assert [line.split() for line in listed] == [
        ["audio", "10", "B", str(orphan.resolve())]
    ]
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
    listed = [line for line in out.splitlines() if str(orphan.resolve()) in line]
    assert len(listed) == 1
    assert listed[0].endswith(f"{orphan.resolve()}  (removed)")


@pytest.mark.usefixtures("lock")
def test_the_cli_lists_as_many_files_as_shown(
    layout: Layout, db: Engine, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    add_referenced_audio(db, layout, 30)
    for _ in range(3):
        write_file(layout.recordings / uuid_name(".wav"))

    _, capped = _cli(db, monkeypatch, capsys, "--show", "1")
    _, every = _cli(db, monkeypatch, capsys, "--show", "0")

    def listed(out: str) -> int:
        return sum(line.startswith("  audio ") for line in out.splitlines())

    assert listed(capped) == 1
    assert "... and 2 more (--show 0 lists them all)" in capped
    assert listed(every) == 3
    assert "more (--show 0" not in every


@pytest.mark.usefixtures("lock")
def test_the_cli_needs_the_directory_and_the_count_to_allow_a_bulk_removal(
    layout: Layout, db: Engine, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    add_referenced_audio(db, layout, 3)
    orphans = [write_file(layout.recordings / uuid_name(".wav")) for _ in range(3)]

    for argv in (
        [],
        ["--delete"],
        ["--delete", "--allow-bulk", "data/recordings", "--expect", "2"],
    ):
        code, out = _cli(db, monkeypatch, capsys, *argv)
        assert code == 1, argv
        assert "bulk:" in out
        assert all(path.exists() for path in orphans)

    code, _ = _cli(
        db,
        monkeypatch,
        capsys,
        "--delete",
        "--allow-bulk",
        "data/recordings",
        "--expect",
        "3",
    )

    assert code == 0
    assert not any(path.exists() for path in orphans)


@pytest.mark.usefixtures("lock")
def test_the_cli_takes_a_pair_for_each_directory_over_the_limit(
    layout: Layout, db: Engine, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """One directory over the limit stops the whole run, so a backlog in both
    directories needs both allowances in the same run."""
    add_referenced_documents(db, layout, 4)
    add_referenced_audio(db, layout, 9)
    orphans = [
        *(write_file(layout.recordings / uuid_name(".wav")) for _ in range(2)),
        write_file(layout.documents / uuid_name(".pdf")),
    ]
    recordings = ["--allow-bulk", "data/recordings", "--expect", "2"]
    documents = ["--allow-bulk", "data/documents", "--expect", "1"]

    for pair in (recordings, documents):
        code, out = _cli(db, monkeypatch, capsys, "--delete", *pair)
        assert code == 1, pair
        assert "bulk:" in out
        assert all(path.exists() for path in orphans)

    code, _ = _cli(db, monkeypatch, capsys, "--delete", *recordings, *documents)

    assert code == 0
    assert not any(path.exists() for path in orphans)


_PAIR = ["--allow-bulk", "data/recordings", "--expect", "3"]


@pytest.mark.usefixtures("layout")
@pytest.mark.parametrize(
    "argv",
    [
        ["--allow-bulk", "data/recordings"],
        ["--expect", "3"],
        ["--show", "many"],
        [*_PAIR, "--allow-bulk", "data/documents"],
        [*_PAIR, *_PAIR],
    ],
    ids=[
        "allow_bulk_alone",
        "expect_alone",
        "not_a_count",
        "a_pair_and_an_unpaired_directory",
        "a_directory_named_twice",
    ],
)
def test_the_cli_exits_2_on_a_usage_error(
    db: Engine, monkeypatch: pytest.MonkeyPatch, capsys, argv: list[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        _cli(db, monkeypatch, capsys, "--delete", *argv)

    assert exit_info.value.code == 2


@pytest.mark.usefixtures("lock")
def test_the_cli_refuses_an_allowance_for_a_directory_it_does_not_scan(
    layout: Layout, db: Engine, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    add_referenced_audio(db, layout, 6)
    orphans = [write_file(layout.recordings / uuid_name(".wav")) for _ in range(2)]

    with pytest.raises(SystemExit) as exit_info:
        _cli(
            db,
            monkeypatch,
            capsys,
            "--delete",
            "--allow-bulk",
            "data/recording",
            "--expect",
            "2",
        )

    assert exit_info.value.code == 2
    error = capsys.readouterr().err
    assert f"{layout.data / 'recording'} is not a directory this run scans" in error
    assert str(layout.recordings.resolve()) in error
    assert all(path.exists() for path in orphans)


@pytest.mark.usefixtures("lock")
def test_allow_bulk_lifts_no_other_rule(
    layout: Layout, db: Engine, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    add_referenced_audio(db, layout, 1)
    monkeypatch.setenv("DOCUMENTS_DIR", str(layout.data))

    code, out = _cli(
        db,
        monkeypatch,
        capsys,
        "--delete",
        "--allow-bulk",
        str(layout.data),
        "--expect",
        "0",
    )

    assert code == 1
    assert "data_root:" in out
    assert (layout.data / "config.json").exists()
    assert (layout.data / "celerybeat-schedule").exists()


@pytest.mark.usefixtures("lock")
def test_no_flag_removes_most_of_a_directory(
    layout: Layout, db: Engine, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """The library of an install whose database was replaced, with the count
    the dry run printed: still refused."""
    library = []
    for _ in range(40):
        base = uuid.uuid4()
        library.append(write_file(layout.recordings / f"{base}.flac"))
        library.append(write_file(layout.recordings / f"{base}.mp3"))
    add_recording(db, write_file(layout.recordings / "demo_welcome_1.wav"))

    code, out = _cli(
        db,
        monkeypatch,
        capsys,
        "--delete",
        "--allow-bulk",
        "data/recordings",
        "--expect",
        "80",
    )

    assert code == 1
    assert "split_brain:" in out
    assert "may not belong to this data directory" in out
    assert all(path.exists() for path in library)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
@pytest.mark.usefixtures("lock")
def test_the_cli_exits_3_when_a_file_cannot_be_removed(
    layout: Layout, db: Engine, orphan: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    layout.recordings.chmod(0o500)
    try:
        code, out = _cli(db, monkeypatch, capsys, "--delete")
    finally:
        layout.recordings.chmod(0o700)

    assert code == 3
    assert f"{orphan.resolve()}  (could not be removed)" in out
    assert orphan.exists()


@pytest.mark.usefixtures("lock", "layout")
def test_the_cli_exits_4_when_the_run_fails(
    monkeypatch: pytest.MonkeyPatch, capsys, caplog: pytest.LogCaptureFixture
) -> None:
    def no_database():
        raise ConnectionError("database is down")

    monkeypatch.setattr(cli, "get_sync_session", no_database)

    with caplog.at_level(logging.ERROR, logger=cli.__name__):
        code = cli.main(["--delete"])

    assert code == 4
    assert "Storage hygiene failed: database is down" in capsys.readouterr().err
    assert caplog.records[-1].exc_info is not None


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


def _summaries(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == hygiene.__name__
        and record.getMessage().startswith("Storage hygiene (")
    ]


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
    summary = _summaries(caplog)
    assert len(summary) == 1
    assert ("(delete)" if removed else "(report only)") in summary[0]
    messages = [r.getMessage() for r in caplog.records if r.name == hygiene.__name__]
    assert len(messages) == 2  # the summary, and a line naming the file
    verb = "removed" if removed else "would remove"
    assert f"Storage hygiene {verb} {orphan} (audio, 10 bytes" in caplog.text


@pytest.mark.usefixtures("lock")
def test_the_daily_flag_run_lists_at_most_50_files(
    layout: Layout, db: Engine, daily_mode: str, caplog: pytest.LogCaptureFixture
) -> None:
    add_referenced_audio(db, layout, 60)
    for _ in range(60):
        write_file(layout.recordings / uuid_name(".wav"))

    with caplog.at_level(logging.INFO, logger=hygiene.__name__):
        system_tasks.cleanup_temp_recordings.run()

    listed = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("Storage hygiene would remove ")
    ]
    if daily_mode == "flag":
        assert len(listed) == 51
        assert listed[-1] == "Storage hygiene would remove 10 more files"
    else:
        # Sixty of 120 is a bulk removal: refused, and the refusal lists them.
        assert "bulk" in _summaries(caplog)[0]
        assert len(listed) == 51


@pytest.mark.usefixtures("lock")
def test_a_failing_hygiene_run_does_not_fail_the_daily_cleanup(
    layout: Layout,
    daily_mode: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def no_database():
        raise ConnectionError("database went away")

    monkeypatch.setattr(system_tasks, "get_sync_session", no_database)
    stale = write_file(layout.recordings / "failed" / "1_failed_x.wav", age_hours=72)

    with caplog.at_level(logging.ERROR):
        system_tasks.cleanup_temp_recordings.run()

    assert not stale.exists(), daily_mode
    failure = [
        r for r in caplog.records if r.getMessage().startswith("Storage hygiene failed")
    ]
    assert len(failure) == 1
    assert failure[0].exc_info is not None
