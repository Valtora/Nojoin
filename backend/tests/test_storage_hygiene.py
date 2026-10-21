"""Storage hygiene against real directories and a real schema.

Each test lays out ``data/`` under tmp_path as a container has it, points the
data directory and the working directory there, and runs the routine as the
daily task does. The tests that depend on how stored paths are read also run on
PostgreSQL when NOJOIN_TEST_POSTGRES_URL is set.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

import backend.utils.storage_hygiene as hygiene
from backend.models.recording import RecordingStatus
from backend.tests.storage_hygiene_harness import (
    HOUR,
    FakeLockStore,
    Layout,
    add_document,
    add_recording,
    add_referenced_audio,
    make_layout,
    postgres_engine,
    rules,
    run,
    set_age,
    sqlite_engine,
    write_file,
)
from backend.utils.storage_hygiene import (
    AUDIO,
    DOCUMENTS,
    PARTIAL_UPLOADS,
    PROXIES,
    RULE_BULK,
    RULE_DATA_ROOT,
    RULE_EMPTY_TABLE,
    RULE_MISSING_FILES,
    RULE_RESTORE_LOCK,
    HygieneOptions,
    run_storage_hygiene,
)


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Layout:
    return make_layout(tmp_path, monkeypatch)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Engine]:
    engine = sqlite_engine(tmp_path / "hygiene.db")
    yield engine
    engine.dispose()


@pytest.fixture(params=["sqlite", "postgres"])
def any_db(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Engine]:
    """The tests where the read of stored paths matters, on both databases."""
    if request.param == "postgres":
        with postgres_engine(request.getfixturevalue("postgres_test_url")) as engine:
            yield engine
        return
    engine = sqlite_engine(tmp_path / "hygiene.db")
    yield engine
    engine.dispose()


def _mixed_library(layout: Layout, engine: Engine) -> dict[str, list[Path]]:
    """A referenced recording and document, and one orphan of every kind."""
    audio = write_file(layout.recordings / "kept.wav")
    proxy = write_file(layout.recordings / "kept.mp3")
    document = write_file(layout.documents / "kept.pdf")
    # Spelled cwd-relative, as uploads store them.
    recording_id = add_recording(
        engine, "data/recordings/kept.wav", "data/recordings/kept.mp3"
    )
    add_document(engine, recording_id, document)
    return {
        "kept": [audio, proxy, document],
        DOCUMENTS: [write_file(layout.documents / "lost.pdf", size=100)],
        AUDIO: [write_file(layout.recordings / "lost.flac", size=200)],
        PROXIES: [write_file(layout.recordings / "lost.mp3", size=300)],
        PARTIAL_UPLOADS: [
            write_file(layout.recordings / "cut.flac.part", size=40),
            write_file(layout.documents / "cut.pdf.0123abcd.upload-tmp", size=50),
        ],
    }


def test_flag_mode_reports_every_kind_of_orphan_and_removes_nothing(
    layout: Layout, db: Engine
) -> None:
    library = _mixed_library(layout, db)

    report = run(db, allow_bulk=True)

    found = {
        name: (entry.scanned, entry.unreferenced, entry.unreferenced_bytes)
        for name, entry in report.categories.items()
    }
    assert found == {
        DOCUMENTS: (2, 1, 100),
        AUDIO: (2, 1, 200),
        PROXIES: (2, 1, 300),
        PARTIAL_UPLOADS: (2, 2, 90),
    }
    assert report.reclaimable_bytes == 690
    assert not report.tripped
    assert all(entry.deleted == 0 for entry in report.categories.values())
    assert all(path.exists() for paths in library.values() for path in paths)


def test_delete_mode_removes_exactly_the_orphans(layout: Layout, db: Engine) -> None:
    library = _mixed_library(layout, db)

    report = run(db, delete=True, allow_bulk=True)

    for category in (DOCUMENTS, AUDIO, PROXIES, PARTIAL_UPLOADS):
        assert not any(path.exists() for path in library[category]), category
        entry = report.categories[category]
        assert (entry.deleted, entry.deleted_bytes, entry.failed) == (
            entry.unreferenced,
            entry.unreferenced_bytes,
            0,
        )
    assert all(path.exists() for path in library["kept"])
    assert (layout.data / "config.json").exists()
    assert (layout.data / "celerybeat-schedule").exists()


def test_the_report_names_each_flagged_file_and_each_removal_is_logged(
    layout: Layout, db: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    library = _mixed_library(layout, db)
    orphans = {
        path.resolve()
        for category, paths in library.items()
        if category != "kept"
        for path in paths
    }

    dry_run = run(db, allow_bulk=True)

    assert {flagged.path for flagged in dry_run.flagged} == orphans
    assert all(flagged.outcome is None for flagged in dry_run.flagged)

    with caplog.at_level(logging.INFO, logger=hygiene.__name__):
        report = run(db, delete=True, allow_bulk=True)

    assert {flagged.outcome for flagged in report.flagged} == {"deleted"}
    removed = [
        record.getMessage().split()[3]
        for record in caplog.records
        if record.getMessage().startswith("Storage hygiene removed ")
    ]
    assert sorted(removed) == sorted(str(path) for path in orphans)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_a_failed_unlink_is_counted_and_the_rest_still_go(
    layout: Layout, db: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    library = _mixed_library(layout, db)
    layout.documents.chmod(0o500)
    try:
        with caplog.at_level(logging.WARNING, logger=hygiene.__name__):
            report = run(db, delete=True, allow_bulk=True)
    finally:
        layout.documents.chmod(0o700)

    assert report.categories[DOCUMENTS].failed == 1
    assert report.categories[DOCUMENTS].deleted == 0
    assert [f.outcome for f in report.flagged if f.category == DOCUMENTS] == ["failed"]
    assert library[DOCUMENTS][0].exists()
    assert not library[AUDIO][0].exists()
    assert report.failed == 2  # the document and the .upload-tmp beside it
    assert "could not remove" in caplog.text


def test_a_file_named_under_any_spelling_is_kept(
    layout: Layout, any_db: Engine, tmp_path: Path
) -> None:
    absolute = write_file(layout.recordings / "absolute.wav")
    add_recording(any_db, absolute)
    write_file(layout.recordings / "relative.wav")
    add_recording(any_db, "data/recordings/relative.wav")
    write_file(layout.recordings / "home.wav")
    add_recording(any_db, "~/data/recordings/home.wav")
    write_file(layout.recordings / "dotted.wav")
    add_recording(any_db, "./data/documents/../recordings/dotted.wav")
    linked_dir = tmp_path / "linked"
    linked_dir.symlink_to(layout.recordings)
    write_file(layout.recordings / "symlinked.wav")
    recording_id = add_recording(any_db, linked_dir / "symlinked.wav")
    # A row naming a hard link elsewhere keeps the directory's own name for it.
    hard = write_file(layout.documents / "hard.pdf")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.link(hard, elsewhere / "hard.pdf")
    add_document(any_db, recording_id, elsewhere / "hard.pdf")
    orphan = write_file(layout.recordings / "orphan.wav")

    report = run(any_db, delete=True, allow_bulk=True)

    assert not orphan.exists()
    assert report.categories[AUDIO].unreferenced == 1
    assert report.categories[DOCUMENTS].unreferenced == 0
    for name in ("absolute", "relative", "home", "dotted", "symlinked"):
        assert (layout.recordings / f"{name}.wav").exists(), name
    assert hard.exists()


def test_a_soft_deleted_recording_keeps_its_files(
    layout: Layout, any_db: Engine
) -> None:
    audio = write_file(layout.recordings / "binned.wav")
    proxy = write_file(layout.recordings / "binned.mp3")
    document = write_file(layout.documents / "binned.pdf")
    recording_id = add_recording(any_db, audio, proxy, is_deleted=True)
    add_document(any_db, recording_id, document)
    orphan = write_file(layout.recordings / "orphan.wav")

    report = run(any_db, delete=True, allow_bulk=True)

    assert audio.exists() and proxy.exists() and document.exists()
    assert not orphan.exists()
    assert not report.tripped


def test_an_mp3_recording_that_is_its_own_proxy_is_kept(
    layout: Layout, any_db: Engine
) -> None:
    audio = write_file(layout.recordings / "imported.mp3")
    add_recording(any_db, audio, audio)
    orphan = write_file(layout.recordings / "orphan.mp3")

    report = run(any_db, delete=True, allow_bulk=True)

    assert audio.exists()
    assert not orphan.exists()
    proxies = report.categories[PROXIES]
    assert (proxies.scanned, proxies.unreferenced, proxies.missing_rows) == (2, 1, 0)


def test_nothing_modified_within_the_grace_is_touched(
    layout: Layout, db: Engine
) -> None:
    now = float(int(time.time()))
    add_referenced_audio(db, layout, 1)
    at_the_limit = write_file(layout.recordings / "at-the-limit.wav")
    just_past = write_file(layout.recordings / "just-past.wav")
    recent = write_file(layout.recordings / "recent.wav")
    # Spelled out rather than read from the module, so a shorter grace fails here.
    cutoff = now - 24 * HOUR
    os.utime(at_the_limit, (cutoff, cutoff))
    os.utime(just_past, (cutoff - 1, cutoff - 1))
    set_age(recent, 1, now=now)

    report = run(db, delete=True, allow_bulk=True, now=now)

    assert at_the_limit.exists()
    assert recent.exists()
    assert not just_past.exists()
    assert report.categories[AUDIO].unreferenced == 1


def test_temp_failed_dotfiles_and_symlinks_are_left_alone(
    layout: Layout, db: Engine, tmp_path: Path
) -> None:
    add_referenced_audio(db, layout, 1)
    target = write_file(tmp_path / "outside" / "target.pdf")
    link = layout.documents / "link.pdf"
    link.symlink_to(target)
    set_age(link, 48)
    untouchable = [
        write_file(layout.recordings / "temp" / "7" / "000001.wav"),
        write_file(layout.recordings / "failed" / "7_failed_1.wav"),
        write_file(layout.recordings / ".hidden.wav"),
        write_file(layout.documents / ".DS_Store"),
        target,
    ]
    orphan = write_file(layout.recordings / "orphan.wav")

    report = run(db, delete=True, allow_bulk=True)

    assert not orphan.exists()
    assert link.is_symlink()
    assert all(path.exists() for path in untouchable)
    assert report.categories[DOCUMENTS].scanned == 0
    assert report.categories[AUDIO].scanned == 2


def test_both_recordings_roots_are_scanned(
    layout: Layout, db: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    moved = tmp_path / "moved-recordings"
    monkeypatch.setenv("RECORDINGS_DIR", str(moved))
    add_recording(db, write_file(moved / "kept.wav"))
    add_recording(db, write_file(layout.recordings / "restored.wav"))
    in_moved = write_file(moved / "orphan.wav")
    in_data = write_file(layout.recordings / "orphan.wav")

    report = run(db, delete=True, allow_bulk=True)

    assert not in_moved.exists() and not in_data.exists()
    assert (moved / "kept.wav").exists()
    assert (layout.recordings / "restored.wav").exists()
    assert moved.resolve() in report.directories
    assert layout.recordings.resolve() in report.directories


def test_a_row_committed_after_the_listing_keeps_its_file(
    layout: Layout, any_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    recording_id = add_recording(any_db, write_file(layout.recordings / "kept.wav"))
    late = write_file(layout.documents / "late.pdf")
    orphan = write_file(layout.documents / "orphan.pdf")
    read = hygiene._read_references

    def row_commits_first(session_factory):
        add_document(any_db, recording_id, late)
        return read(session_factory)

    monkeypatch.setattr(hygiene, "_read_references", row_commits_first)

    run(any_db, delete=True, allow_bulk=True)

    assert late.exists()
    assert not orphan.exists()


def test_a_file_that_appears_after_the_listing_is_never_deleted(
    layout: Layout, any_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_referenced_audio(any_db, layout, 1)
    orphan = write_file(layout.recordings / "orphan.wav")
    read = hygiene._read_references
    appeared: list[Path] = []

    def file_lands_first(session_factory):
        # Old by its mtime and named by no row: only the order protects it.
        appeared.append(write_file(layout.recordings / "moved-in.wav"))
        return read(session_factory)

    monkeypatch.setattr(hygiene, "_read_references", file_lands_first)

    run(any_db, delete=True, allow_bulk=True)

    assert appeared[0].exists()
    assert not orphan.exists()


@pytest.mark.parametrize("change", ["new_inode", "new_mtime"])
def test_a_file_changed_after_the_listing_is_kept(
    layout: Layout, db: Engine, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    add_referenced_audio(db, layout, 1)
    orphan = write_file(layout.recordings / "orphan.wav")
    unlink = hygiene._unlink_if_unchanged

    def changed_first(stored):
        if change == "new_inode":
            # Same name and mtime, another file: only the inode tells them apart.
            replacement = write_file(layout.recordings / "replacement.tmp")
            os.replace(replacement, stored.path)
            os.utime(stored.path, ns=(stored.mtime_ns, stored.mtime_ns))
        else:
            older = stored.mtime_ns - HOUR * 1_000_000_000
            os.utime(stored.path, ns=(older, older))
        return unlink(stored)

    monkeypatch.setattr(hygiene, "_unlink_if_unchanged", changed_first)

    report = run(db, delete=True, allow_bulk=True)

    assert orphan.exists()
    assert report.categories[AUDIO].deleted == 0
    assert report.categories[AUDIO].failed == 0
    assert [flagged.outcome for flagged in report.flagged] == ["kept"]


@pytest.mark.parametrize("files_on_disk", [True, False], ids=["files", "empty"])
def test_breaker_an_empty_recordings_table(
    layout: Layout, any_db: Engine, files_on_disk: bool
) -> None:
    on_disk = []
    if files_on_disk:
        on_disk = [
            write_file(layout.documents / "a.pdf"),
            write_file(layout.recordings / "b.wav"),
        ]

    report = run(any_db, delete=True, allow_bulk=True)

    if files_on_disk:
        assert rules(report) == [RULE_EMPTY_TABLE]
        assert all(path.exists() for path in on_disk)
    else:
        assert rules(report) == []


@pytest.mark.parametrize(
    "case",
    [
        ("DOCUMENTS_DIR", "data"),
        ("DOCUMENTS_DIR", "bare_data"),
        ("RECORDINGS_DIR", "data"),
        ("RECORDINGS_DIR", "app"),
        ("DOCUMENTS_DIR", "beside_config"),
    ],
    ids=[
        "documents_at_data_root",
        "documents_at_data_root_before_config_json",
        "recordings_at_data_root",
        "recordings_above_data_root",
        "documents_holding_config_json",
    ],
)
def test_breaker_a_directory_at_the_data_root(
    layout: Layout,
    db: Engine,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, str],
) -> None:
    variable, points_at = case
    protected = [layout.data / "config.json", layout.data / "celerybeat-schedule"]
    target = {"data": layout.data, "app": layout.app}.get(points_at)
    if points_at == "bare_data":
        # The rule stands on the path alone, before config.json is first written.
        (layout.data / "config.json").unlink()
        protected.remove(layout.data / "config.json")
        target = layout.data
    elif target is None:
        target = layout.app.parent / "beside-config"
        protected.append(write_file(target / "config.json"))
    monkeypatch.setenv(variable, str(target))
    add_recording(db, write_file(layout.recordings / "kept.wav"))
    orphan = write_file(layout.documents / "orphan.pdf")

    report = run(db, delete=True, allow_bulk=True)

    assert rules(report) == [RULE_DATA_ROOT]
    assert orphan.exists()
    assert all(path.exists() for path in protected)


@pytest.mark.parametrize("proxy_missing", [False, True], ids=["half", "most"])
def test_breaker_most_stored_paths_name_missing_files(
    layout: Layout, any_db: Engine, proxy_missing: bool
) -> None:
    audio = write_file(layout.recordings / "kept.wav")
    proxy = layout.recordings / "kept.mp3"
    if not proxy_missing:
        write_file(proxy)
    recording_id = add_recording(any_db, audio, proxy)
    add_recording(any_db, layout.recordings / "gone.wav")
    add_document(any_db, recording_id, layout.documents / "gone.pdf")
    orphan = write_file(layout.recordings / "orphan.wav")

    report = run(any_db, delete=True, allow_bulk=True)

    missing = {name: entry.missing_rows for name, entry in report.categories.items()}
    assert missing == {
        DOCUMENTS: 1,
        AUDIO: 1,
        PROXIES: int(proxy_missing),
        PARTIAL_UPLOADS: 0,
    }
    if proxy_missing:  # three of four stored paths
        assert rules(report) == [RULE_MISSING_FILES]
        assert orphan.exists()
    else:  # two of four: exactly half
        assert rules(report) == []
        assert not orphan.exists()


def test_recordings_still_uploading_do_not_count_as_missing(
    layout: Layout, any_db: Engine
) -> None:
    add_referenced_audio(any_db, layout, 1)
    # Capture init names a .wav that finalize replaces with the .flac master.
    add_recording(
        any_db, layout.recordings / "live.wav", status=RecordingStatus.UPLOADING
    )
    add_recording(any_db, layout.recordings / "held.wav", status=RecordingStatus.PAUSED)
    add_recording(
        any_db,
        layout.recordings / "reaped.wav",
        status=RecordingStatus.UPLOADING,
        is_deleted=True,
    )
    # Still a reference when its file does exist.
    uploading = write_file(layout.recordings / "arriving.wav")
    add_recording(any_db, uploading, status=RecordingStatus.UPLOADING)
    orphan = write_file(layout.recordings / "orphan.wav")

    report = run(any_db, delete=True, allow_bulk=True)

    assert rules(report) == []
    assert report.categories[AUDIO].missing_rows == 0
    assert uploading.exists()
    assert not orphan.exists()


@pytest.mark.parametrize(
    "case",
    [(9, 10, 10, False), (8, 10, 10, True), (18, 10, 20, False), (18, 10, 21, True)]
    + [(8, 100, 10, True)],
    ids=[
        "a_tenth",
        "over_a_tenth",
        "a_tenth_of_bytes",
        "over_a_tenth_of_bytes",
        "over_a_tenth_of_files_only",
    ],
)
def test_breaker_a_bulk_removal(
    layout: Layout, db: Engine, case: tuple[int, int, int, bool]
) -> None:
    kept, kept_size, orphan_size, tripped = case
    add_referenced_audio(db, layout, kept, size=kept_size)
    orphan = write_file(layout.recordings / "orphan.wav", size=orphan_size)

    report = run(db, delete=True)

    assert rules(report) == ([RULE_BULK] if tripped else [])
    assert orphan.exists() is tripped


def test_the_bulk_rule_counts_each_directory_on_its_own(
    layout: Layout, db: Engine
) -> None:
    # One file in twenty-one overall, but every file the documents directory holds.
    add_referenced_audio(db, layout, 20)
    orphan = write_file(layout.documents / "orphan.pdf")

    report = run(db, delete=True)

    assert rules(report) == [RULE_BULK]
    assert str(layout.documents.resolve()) in report.breaker[0].detail
    assert orphan.exists()


def test_allow_bulk_lifts_the_bulk_rule(layout: Layout, db: Engine) -> None:
    add_referenced_audio(db, layout, 1)
    orphans = [write_file(layout.recordings / f"orphan-{n}.wav") for n in range(3)]

    report = run(db, delete=True, allow_bulk=True)

    assert rules(report) == []
    assert not any(path.exists() for path in orphans)


def test_breaker_a_restore_holding_the_lock(layout: Layout, db: Engine) -> None:
    add_referenced_audio(db, layout, 1)
    orphan = write_file(layout.recordings / "orphan.wav")

    report = run(db, delete=True, allow_bulk=True, lock=FakeLockStore(True))

    assert rules(report) == [RULE_RESTORE_LOCK]
    assert report.breaker[0].detail == "a restore is running"
    assert orphan.exists()


def test_a_restore_running_during_the_listing_stops_the_deletion(
    layout: Layout, db: Engine
) -> None:
    # Held when the run starts and gone by the unlink: the restore may have
    # moved rows while the directories were listed and read.
    add_referenced_audio(db, layout, 1)
    orphan = write_file(layout.recordings / "orphan.wav")

    report = run(db, delete=True, allow_bulk=True, lock=FakeLockStore(True, False))

    assert rules(report) == [RULE_RESTORE_LOCK]
    assert orphan.exists()


def test_a_restore_that_starts_mid_run_stops_the_deletion(
    layout: Layout, db: Engine
) -> None:
    add_referenced_audio(db, layout, 1)
    orphan = write_file(layout.recordings / "orphan.wav")
    lock = FakeLockStore(False, True)

    report = run(db, delete=True, allow_bulk=True, lock=lock)

    assert lock.reads == 2
    assert rules(report) == [RULE_RESTORE_LOCK]
    assert orphan.exists()


def test_breaker_an_unreachable_lock_store(
    layout: Layout, db: Engine, redis_contact_guard: list[str]
) -> None:
    add_referenced_audio(db, layout, 1)
    orphan = write_file(layout.recordings / "orphan.wav")

    # The real client, which the suite's guard refuses as a Redis outage would.
    report = run_storage_hygiene(
        lambda: Session(db), HygieneOptions(delete=True, allow_bulk=True)
    )

    assert redis_contact_guard, "the run never tried to read the lock"
    redis_contact_guard.clear()
    assert rules(report) == [RULE_RESTORE_LOCK]
    assert "could not be read" in report.breaker[0].detail
    assert orphan.exists()


def test_each_run_logs_one_info_line(
    layout: Layout, db: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    add_referenced_audio(db, layout, 1)
    write_file(layout.recordings / "orphan.wav")

    with caplog.at_level(logging.INFO, logger=hygiene.__name__):
        run(db)

    lines = [
        record.getMessage()
        for record in caplog.records
        if record.name == hygiene.__name__ and record.levelno == logging.INFO
    ]
    assert len(lines) == 1
    assert "report only" in lines[0]
    assert "audio 2 scanned, 1 unreferenced (10 B)" in lines[0]
    assert "tripped, nothing removed" in lines[0]  # one of two files is a bulk removal
