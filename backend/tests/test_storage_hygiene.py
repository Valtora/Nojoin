"""Storage hygiene against real directories and a real schema: what it removes.

Each test lays out ``data/`` under tmp_path as a container has it, points the
data directory and the working directory there, and runs the routine as the
daily task does. The tests that depend on how stored paths are read also run on
PostgreSQL when NOJOIN_TEST_POSTGRES_URL is set. The circuit breaker has its own
file, test_storage_hygiene_breaker.py.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine

import backend.utils.storage_hygiene as hygiene
from backend.tests.storage_hygiene_harness import (
    HOUR,
    Layout,
    add_document,
    add_recording,
    add_referenced_audio,
    add_referenced_documents,
    make_layout,
    postgres_engine,
    rules,
    run,
    set_age,
    sqlite_engine,
    uuid_name,
    write_file,
)
from backend.utils.storage_hygiene import (
    AUDIO,
    DOCUMENTS,
    PARTIAL_UPLOADS,
    PROXIES,
    UNRECOGNISED,
)
from backend.utils.storage_hygiene_report import KEPT


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
    """Referenced files, and one orphan of every kind, few enough per directory
    that the split-brain rule stays clear."""
    base = uuid_name("")
    audio = write_file(layout.recordings / f"{base}.wav")
    proxy = write_file(layout.recordings / f"{base}.mp3")
    # Spelled cwd-relative, as uploads store them.
    recording_id = add_recording(
        engine, f"data/recordings/{base}.wav", f"data/recordings/{base}.mp3"
    )
    document = write_file(layout.documents / uuid_name(".pdf"))
    add_document(engine, recording_id, document)
    kept = [audio, proxy, document]
    kept += add_referenced_audio(engine, layout, 4)
    kept += add_referenced_documents(engine, layout, 2)
    lost = uuid_name("")
    return {
        "kept": kept,
        DOCUMENTS: [write_file(layout.documents / uuid_name(".pdf"), size=100)],
        AUDIO: [write_file(layout.recordings / f"{lost}.flac", size=200)],
        PROXIES: [write_file(layout.recordings / f"{lost}.mp3", size=300)],
        PARTIAL_UPLOADS: [
            write_file(layout.recordings / f"{uuid_name('.flac')}.part", size=40),
            write_file(
                layout.documents / f"{uuid_name('.pdf')}.{'0' * 32}.upload-tmp",
                size=50,
            ),
        ],
    }


def test_flag_mode_reports_every_kind_of_orphan_and_removes_nothing(
    layout: Layout, db: Engine
) -> None:
    library = _mixed_library(layout, db)

    report = run(db, bulk=True)

    found = {
        name: (entry.scanned, entry.unreferenced, entry.unreferenced_bytes)
        for name, entry in report.categories.items()
    }
    assert found == {
        DOCUMENTS: (4, 1, 100),
        AUDIO: (7, 1, 200),
        PROXIES: (2, 1, 300),
        PARTIAL_UPLOADS: (2, 2, 90),
        UNRECOGNISED: (0, 0, 0),
    }
    assert report.reclaimable_bytes == 690
    assert not report.tripped
    assert all(entry.deleted == 0 for entry in report.categories.values())
    assert all(path.exists() for paths in library.values() for path in paths)


def test_delete_mode_removes_exactly_the_orphans(layout: Layout, db: Engine) -> None:
    library = _mixed_library(layout, db)

    report = run(db, delete=True, bulk=True)

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
    lost = library[AUDIO][0]
    when = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(lost.stat().st_mtime_ns // 1_000_000_000)
    )

    dry_run = run(db, bulk=True)

    assert {flagged.path for flagged in dry_run.flagged} == orphans
    assert all(flagged.outcome is None for flagged in dry_run.flagged)

    with caplog.at_level(logging.INFO, logger=hygiene.__name__):
        report = run(db, delete=True, bulk=True)

    assert {flagged.outcome for flagged in report.flagged} == {"deleted"}
    removed = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("Storage hygiene removed ")
    ]
    assert sorted(line.split()[3] for line in removed) == sorted(map(str, orphans))
    assert (
        f"Storage hygiene removed {lost.resolve()} (audio, 200 bytes, modified {when})"
        in removed
    )


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_a_failed_unlink_is_counted_and_the_rest_still_go(
    layout: Layout, db: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    library = _mixed_library(layout, db)
    layout.documents.chmod(0o500)
    try:
        with caplog.at_level(logging.WARNING, logger=hygiene.__name__):
            report = run(db, delete=True, bulk=True)
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
    names = {key: uuid_name(".wav") for key in ("abs", "rel", "home", "dots", "link")}
    for name in names.values():
        write_file(layout.recordings / name)
    add_recording(any_db, layout.recordings / names["abs"])
    add_recording(any_db, f"data/recordings/{names['rel']}")
    add_recording(any_db, f"~/data/recordings/{names['home']}")
    add_recording(any_db, f"./data/documents/../recordings/{names['dots']}")
    linked_dir = tmp_path / "linked"
    linked_dir.symlink_to(layout.recordings)
    add_recording(any_db, linked_dir / names["link"])
    orphan = write_file(layout.recordings / uuid_name(".wav"))

    report = run(any_db, delete=True, bulk=True)

    assert not orphan.exists()
    assert report.categories[AUDIO].unreferenced == 1
    for key, name in names.items():
        assert (layout.recordings / name).exists(), key
    assert report.categories[AUDIO].missing_rows == 0


def test_a_file_a_row_names_under_another_name_is_kept_by_its_inode(
    layout: Layout, any_db: Engine, tmp_path: Path
) -> None:
    add_referenced_documents(any_db, layout, 2)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    hard = write_file(layout.documents / uuid_name(".pdf"))
    os.link(hard, elsewhere / "agenda.pdf")
    soft = write_file(layout.documents / uuid_name(".pdf"))
    (elsewhere / "minutes.pdf").symlink_to(soft)
    holder = add_recording(any_db, write_file(layout.recordings / uuid_name(".wav")))
    add_document(any_db, holder, elsewhere / "agenda.pdf")
    add_document(any_db, holder, elsewhere / "minutes.pdf")
    orphan = write_file(layout.documents / uuid_name(".pdf"))

    report = run(any_db, delete=True, bulk=True)

    assert hard.exists() and soft.exists()
    assert not orphan.exists()
    assert report.categories[DOCUMENTS].unreferenced == 1


def test_a_file_whose_name_a_row_stores_elsewhere_is_kept(
    layout: Layout, any_db: Engine
) -> None:
    """A stored path that resolves away from the file, as from another mount or
    working directory, still keeps every file of that name, in any case."""
    add_referenced_audio(any_db, layout, 4)
    moved = write_file(layout.recordings / uuid_name(".wav"))
    add_recording(any_db, f"/srv/old-mount/recordings/{moved.name}")
    cased = write_file(layout.recordings / uuid_name(".flac"))
    add_recording(any_db, layout.recordings / cased.name.upper())
    orphan = write_file(layout.recordings / uuid_name(".wav"))

    report = run(any_db, delete=True, bulk=True)

    assert moved.exists() and cased.exists()
    assert not orphan.exists()
    # The rows still name files that are not where they say.
    assert report.categories[AUDIO].missing_rows >= 1


def test_a_soft_deleted_recording_keeps_its_files(
    layout: Layout, any_db: Engine
) -> None:
    base = uuid_name("")
    audio = write_file(layout.recordings / f"{base}.wav")
    proxy = write_file(layout.recordings / f"{base}.mp3")
    document = write_file(layout.documents / uuid_name(".pdf"))
    recording_id = add_recording(any_db, audio, proxy, is_deleted=True)
    add_document(any_db, recording_id, document)
    add_referenced_audio(any_db, layout, 2)
    orphan = write_file(layout.recordings / uuid_name(".wav"))

    report = run(any_db, delete=True, bulk=True)

    assert audio.exists() and proxy.exists() and document.exists()
    assert not orphan.exists()
    assert not report.tripped


def test_a_document_with_no_recording_keeps_its_file_and_counts_if_missing(
    layout: Layout, any_db: Engine
) -> None:
    add_referenced_documents(any_db, layout, 3)
    unattached = write_file(layout.documents / uuid_name(".pdf"))
    add_document(any_db, None, unattached)
    add_document(any_db, None, layout.documents / uuid_name(".pdf"))
    orphan = write_file(layout.documents / uuid_name(".pdf"))

    report = run(any_db, delete=True, bulk=True)

    assert unattached.exists()
    assert not orphan.exists()
    assert report.categories[DOCUMENTS].missing_rows == 1


def test_nothing_modified_within_the_grace_is_touched(
    layout: Layout, db: Engine
) -> None:
    now = float(int(time.time()))
    add_referenced_audio(db, layout, 1)
    at_the_limit = write_file(layout.recordings / uuid_name(".wav"))
    just_past = write_file(layout.recordings / uuid_name(".wav"))
    recent = write_file(layout.recordings / uuid_name(".wav"))
    # Spelled out rather than read from the module, so a shorter grace fails here.
    cutoff = now - 24 * HOUR
    os.utime(at_the_limit, (cutoff, cutoff))
    os.utime(just_past, (cutoff - 1, cutoff - 1))
    set_age(recent, 1, now=now)

    report = run(db, delete=True, bulk=True, now=now)

    assert at_the_limit.exists()
    assert recent.exists()
    assert not just_past.exists()
    assert report.categories[AUDIO].unreferenced == 1


def test_temp_failed_dotfiles_and_symlinks_are_left_alone(
    layout: Layout, db: Engine, tmp_path: Path
) -> None:
    add_referenced_audio(db, layout, 1)
    target = write_file(tmp_path / "outside" / uuid_name(".pdf"))
    link = layout.documents / uuid_name(".pdf")
    link.symlink_to(target)
    set_age(link, 48)
    untouchable = [
        write_file(layout.recordings / "temp" / "7" / "000001.wav"),
        write_file(layout.recordings / "failed" / "7_failed_1.wav"),
        write_file(layout.recordings / f".{uuid_name('.wav')}"),
        write_file(layout.documents / ".DS_Store"),
        target,
    ]
    orphan = write_file(layout.recordings / uuid_name(".wav"))

    report = run(db, delete=True, bulk=True)

    assert not orphan.exists()
    assert link.is_symlink()
    assert all(path.exists() for path in untouchable)
    assert report.categories[DOCUMENTS].scanned == 0
    assert report.categories[AUDIO].scanned == 2
    assert report.categories[UNRECOGNISED].scanned == 0


def test_both_recordings_roots_are_scanned(
    layout: Layout, db: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    moved = tmp_path / "moved-recordings"
    monkeypatch.setenv("RECORDINGS_DIR", str(moved))
    kept = [
        write_file(moved / uuid_name(".wav")),
        write_file(layout.recordings / uuid_name(".wav")),
    ]
    for path in kept:
        add_recording(db, path)
    orphans = [
        write_file(moved / uuid_name(".wav")),
        write_file(layout.recordings / uuid_name(".wav")),
    ]

    report = run(db, delete=True, bulk=True)

    assert not any(path.exists() for path in orphans)
    assert all(path.exists() for path in kept)
    scanned = [directory.path for directory in report.directories]
    assert moved.resolve() in scanned
    assert layout.recordings.resolve() in scanned


def test_two_spellings_of_one_directory_are_scanned_once(
    layout: Layout, db: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    link = tmp_path / "recordings-link"
    link.symlink_to(layout.recordings)
    monkeypatch.setenv("RECORDINGS_DIR", str(link))
    add_referenced_audio(db, layout, 3)
    orphan = write_file(layout.recordings / uuid_name(".wav"))

    report = run(db, delete=True, bulk=True)

    assert [d.path for d in report.directories] == [
        layout.documents.resolve(),
        layout.recordings.resolve(),
    ]
    assert report.categories[AUDIO].scanned == 4
    assert not orphan.exists()
    assert report.deleted == 1


def test_a_row_committed_after_the_listing_keeps_its_file(
    layout: Layout, any_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_referenced_documents(any_db, layout, 20)
    recording_id = add_recording(
        any_db, write_file(layout.recordings / uuid_name(".wav"))
    )
    late = write_file(layout.documents / uuid_name(".pdf"))
    orphan = write_file(layout.documents / uuid_name(".pdf"))
    read = hygiene._read_references
    reads: list[int] = []

    def row_commits_first(session_factory):
        if not reads:
            add_document(any_db, recording_id, late)
        reads.append(1)
        return read(session_factory)

    monkeypatch.setattr(hygiene, "_read_references", row_commits_first)

    run(any_db, delete=True)

    assert late.exists()
    assert not orphan.exists()


def test_a_row_committed_after_the_first_read_keeps_its_file(
    layout: Layout, any_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rows are read again just before the unlinks, so a file moved into
    place with its old mtime, its row committed after the first read, stays."""
    add_referenced_audio(any_db, layout, 20)
    moved = write_file(layout.recordings / uuid_name(".flac"))
    orphan = write_file(layout.recordings / uuid_name(".flac"))
    read = hygiene._read_references
    reads: list[int] = []

    def commit_after_the_first_read(session_factory):
        references = read(session_factory)
        if not reads:
            add_recording(any_db, moved)
        reads.append(1)
        return references

    monkeypatch.setattr(hygiene, "_read_references", commit_after_the_first_read)

    report = run(any_db, delete=True)

    assert len(reads) == 2
    assert moved.exists()
    assert not orphan.exists()
    outcomes = {flagged.path: flagged.outcome for flagged in report.flagged}
    assert outcomes[moved] == KEPT


def test_a_file_that_appears_after_the_listing_is_never_deleted(
    layout: Layout, any_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_referenced_audio(any_db, layout, 20)
    orphan = write_file(layout.recordings / uuid_name(".wav"))
    read = hygiene._read_references
    appeared: list[Path] = []

    def file_lands_first(session_factory):
        # Old by its mtime and named by no row: only the order protects it.
        if not appeared:
            appeared.append(write_file(layout.recordings / uuid_name(".wav")))
        return read(session_factory)

    monkeypatch.setattr(hygiene, "_read_references", file_lands_first)

    run(any_db, delete=True)

    assert appeared[0].exists()
    assert not orphan.exists()


@pytest.mark.parametrize("change", ["new_inode", "new_mtime"])
def test_a_file_changed_after_the_listing_is_kept(
    layout: Layout, db: Engine, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    add_referenced_audio(db, layout, 20)
    orphan = write_file(layout.recordings / uuid_name(".wav"))
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

    report = run(db, delete=True)

    assert rules(report) == []
    assert orphan.exists()
    assert report.categories[AUDIO].deleted == 0
    assert report.categories[AUDIO].failed == 0
    assert [flagged.outcome for flagged in report.flagged] == ["kept"]
