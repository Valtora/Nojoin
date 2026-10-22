"""The storage hygiene circuit breaker: every rule, at and past its limit.

Each rule turns a delete run into a report. The tests lay out ``data/`` as a
container has it, as test_storage_hygiene.py does, and check both that the rule
trips and that every file is still there.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

import backend.utils.storage_hygiene as hygiene
from backend.models.recording import RecordingStatus
from backend.tests.storage_hygiene_harness import (
    FakeLockStore,
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
from backend.utils.path_manager import PathManager
from backend.utils.storage_hygiene import (
    AUDIO,
    DOCUMENTS,
    PROXIES,
    RULE_BULK,
    RULE_DATA_ROOT,
    RULE_EMPTY_TABLE,
    RULE_MISSING_FILES,
    RULE_RESTORE_LOCK,
    RULE_SPLIT_BRAIN,
    RULE_WORKING_DIRECTORY,
    HygieneOptions,
    run_storage_hygiene,
)
from backend.utils.storage_hygiene_report import summary_line


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
    if request.param == "postgres":
        with postgres_engine(request.getfixturevalue("postgres_test_url")) as engine:
            yield engine
        return
    engine = sqlite_engine(tmp_path / "hygiene.db")
    yield engine
    engine.dispose()


def _old_library(layout: Layout, recordings: int, documents: int) -> list[Path]:
    """What an install's data/ holds: audio, proxies and documents, all old."""
    files = []
    for _ in range(recordings):
        base = uuid.uuid4()
        files.append(write_file(layout.recordings / f"{base}.flac", size=1000))
        files.append(write_file(layout.recordings / f"{base}.mp3", size=200))
    for _ in range(documents):
        files.append(write_file(layout.documents / uuid_name(".pdf"), size=300))
    return files


# --- the empty recordings table -------------------------------------------------


@pytest.mark.parametrize("files", ["recent", "old", "none"])
def test_breaker_an_empty_recordings_table(
    layout: Layout, any_db: Engine, files: str
) -> None:
    on_disk = []
    if files != "none":
        on_disk = [
            write_file(layout.documents / uuid_name(".pdf")),
            write_file(layout.recordings / uuid_name(".wav")),
        ]
    if files == "recent":
        for path in on_disk:
            set_age(path, 1)

    report = run(any_db, delete=True, bulk=True)

    expected = {
        # Recent files alone trip nothing else, so this rule stands by itself.
        "recent": [RULE_EMPTY_TABLE],
        "old": [RULE_EMPTY_TABLE, RULE_SPLIT_BRAIN, RULE_SPLIT_BRAIN],
        "none": [],
    }
    assert rules(report) == expected[files]
    assert all(path.exists() for path in on_disk)


def test_recordings_still_uploading_make_the_table_not_empty(
    layout: Layout, any_db: Engine
) -> None:
    base = uuid_name("")
    add_recording(
        any_db, f"data/recordings/{base}.wav", status=RecordingStatus.UPLOADING
    )
    master = write_file(layout.recordings / f"{base}.flac")

    report = run(any_db, delete=True)

    assert rules(report) == []
    assert master.exists()


# --- a directory at the data root ---------------------------------------------


def _point_data_dir_through_a_link(
    layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> Path:
    link = layout.app.parent / "data-link"
    link.symlink_to(layout.data)
    monkeypatch.setenv("NOJOIN_DATA_DIR", str(link))
    monkeypatch.setattr(PathManager, "_instance", None)
    return layout.data


_DATA_ROOT_CASES = {
    # (variable, where it points, whether config.json is there yet)
    "documents_at_data_root": ("DOCUMENTS_DIR", "data", True),
    "documents_at_data_root_before_config_json": ("DOCUMENTS_DIR", "data", False),
    "recordings_at_data_root": ("RECORDINGS_DIR", "data", True),
    "recordings_above_data_root": ("RECORDINGS_DIR", "app", True),
    "documents_holding_config_json": ("DOCUMENTS_DIR", "beside_config", True),
    "documents_through_a_symlink": ("DOCUMENTS_DIR", "symlink", False),
    "documents_through_dot_dot": ("DOCUMENTS_DIR", "dot_dot", False),
    "data_dir_is_a_symlink": ("DOCUMENTS_DIR", "data_via_link", False),
}


def _data_root_target(
    layout: Layout, monkeypatch: pytest.MonkeyPatch, points_at: str
) -> str:
    if points_at == "symlink":
        link = layout.app.parent / "documents-link"
        link.symlink_to(layout.data)
        return str(link)
    if points_at == "dot_dot":
        # Relative, so taken against the data directory's parent: app/data.
        return "data/documents/.."
    if points_at == "data_via_link":
        return str(_point_data_dir_through_a_link(layout, monkeypatch))
    if points_at == "beside_config":
        target = layout.app.parent / "beside-config"
        write_file(target / "config.json")
        return str(target)
    return str({"data": layout.data, "app": layout.app}[points_at])


@pytest.mark.parametrize("case", _DATA_ROOT_CASES.values(), ids=_DATA_ROOT_CASES.keys())
def test_breaker_a_directory_at_the_data_root(
    layout: Layout,
    db: Engine,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, str, bool],
) -> None:
    variable, points_at, config_json = case
    if not config_json:
        # The rule stands on the path alone, before config.json is first written.
        (layout.data / "config.json").unlink()
    monkeypatch.setenv(variable, _data_root_target(layout, monkeypatch, points_at))
    add_referenced_documents(db, layout, 2)
    orphan = write_file(layout.documents / uuid_name(".pdf"))
    before = sorted(path for path in layout.app.parent.rglob("*") if path.is_file())

    report = run(db, delete=True, bulk=True)

    assert rules(report) == [RULE_DATA_ROOT]
    assert orphan.exists()
    after = sorted(path for path in layout.app.parent.rglob("*") if path.is_file())
    assert after == before


# --- stored paths naming missing files -----------------------------------------


@pytest.mark.parametrize("missing", [6, 7], ids=["half", "over_half"])
def test_breaker_most_of_one_categorys_paths_name_missing_files(
    layout: Layout, any_db: Engine, missing: int
) -> None:
    """Measured per category: thirty documents that are all there cannot hide
    that most of the audio is gone."""
    holder = add_recording(any_db, write_file(layout.recordings / uuid_name(".wav")))
    for _ in range(30):
        add_document(any_db, holder, write_file(layout.documents / uuid_name(".pdf")))
    add_referenced_audio(any_db, layout, 11 - missing)
    for _ in range(missing):
        add_recording(any_db, layout.recordings / uuid_name(".wav"))
    orphan = write_file(layout.recordings / uuid_name(".wav"))

    report = run(any_db, delete=True, bulk=True)

    assert report.categories[AUDIO].missing_rows == missing
    if missing == 7:  # seven of twelve audio paths
        assert rules(report) == [RULE_MISSING_FILES]
        assert (
            report.breaker[0].detail == "7 of 12 stored audio paths name missing files"
        )
        assert orphan.exists()
    else:  # six of twelve: exactly half
        assert rules(report) == []
        assert not orphan.exists()


def test_what_counts_as_a_row_missing_its_file(layout: Layout, any_db: Engine) -> None:
    add_referenced_audio(any_db, layout, 6)
    add_referenced_documents(any_db, layout, 2)
    # Missing: an absolute path, one that cannot be resolved, recordings still
    # to be processed or processing, and an MP3 that is its own proxy, counted
    # once, as audio.
    add_recording(any_db, layout.recordings / uuid_name(".wav"))
    add_recording(any_db, f"~nosuchuser{uuid.uuid4().hex}/{uuid_name('.wav')}")
    for status in (
        RecordingStatus.RECORDED,
        RecordingStatus.QUEUED,
        RecordingStatus.PROCESSING,
    ):
        add_recording(any_db, layout.recordings / uuid_name(".wav"), status=status)
    mp3 = layout.recordings / uuid_name(".mp3")
    add_recording(any_db, mp3, mp3)
    add_document(any_db, None, layout.documents / uuid_name(".pdf"))
    # Not missing: a recording whose upload is still arriving, and one paused.
    for status in (RecordingStatus.UPLOADING, RecordingStatus.PAUSED):
        add_recording(any_db, layout.recordings / uuid_name(".wav"), status=status)
    add_recording(
        any_db,
        layout.recordings / uuid_name(".wav"),
        status=RecordingStatus.UPLOADING,
        is_deleted=True,
    )

    report = run(any_db, delete=True)

    missing = {
        name: report.categories[name].missing_rows
        for name in (AUDIO, PROXIES, DOCUMENTS)
    }
    assert missing == {AUDIO: 6, PROXIES: 0, DOCUMENTS: 1}
    assert rules(report) == []


def test_recordings_that_settled_without_their_file_do_not_count_as_missing(
    layout: Layout, any_db: Engine
) -> None:
    """An import that fails removes its audio and keeps the row in ERROR, and a
    capture whose master could not be built stays in ERROR naming the ``.wav``
    it never got. Neither says the database is another data directory's."""
    add_referenced_audio(any_db, layout, 2)
    for _ in range(3):
        add_recording(
            any_db, f"data/recordings/{uuid_name('.wav')}", status=RecordingStatus.ERROR
        )
    add_recording(
        any_db, f"data/recordings/{uuid_name('.wav')}", status=RecordingStatus.CANCELLED
    )
    # Still a reference: a failed recording whose file is there keeps it.
    failed = write_file(layout.recordings / uuid_name(".wav"))
    add_recording(
        any_db, f"data/recordings/{failed.name}", status=RecordingStatus.ERROR
    )
    orphan = write_file(layout.recordings / uuid_name(".wav"))

    report = run(any_db, delete=True, bulk=True)

    assert rules(report) == []
    assert report.categories[AUDIO].missing_rows == 0
    assert failed.exists()
    assert not orphan.exists()


@pytest.mark.parametrize("missing", [2, 3], ids=["half", "over_half"])
def test_breaker_relative_paths_that_miss_from_this_working_directory(
    layout: Layout, any_db: Engine, missing: int
) -> None:
    """Measured over the paths stored relative to the working directory, which
    is where they resolve. Absolute ones keep the per-category rule clear."""
    add_referenced_audio(any_db, layout, 4)
    for index in range(4):
        name = uuid_name(".wav")
        if index >= missing:
            write_file(layout.recordings / name)
        add_recording(any_db, f"data/recordings/{name}")

    report = run(any_db, delete=True)

    if missing == 3:
        assert rules(report) == [RULE_WORKING_DIRECTORY]
        assert "3 of 4 stored paths relative to" in report.breaker[0].detail
    else:
        assert rules(report) == []


def test_breaker_run_from_another_working_directory(
    layout: Layout, any_db: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audio and proxies stored relative, documents absolute, and the run started
    somewhere other than the app directory: three documents to every recording
    would keep a share taken over all paths under one half."""
    library = []
    for _ in range(10):
        base = uuid.uuid4()
        library += [
            write_file(layout.recordings / f"{base}.flac"),
            write_file(layout.recordings / f"{base}.mp3"),
        ]
        recording_id = add_recording(
            any_db, f"data/recordings/{base}.flac", f"data/recordings/{base}.mp3"
        )
        for _ in range(3):
            document = write_file(layout.documents / uuid_name(".pdf"))
            add_document(any_db, recording_id, document)
            library.append(document)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    report = run(any_db, delete=True, bulk=True)

    assert set(rules(report)) == {RULE_MISSING_FILES, RULE_WORKING_DIRECTORY}
    assert str(elsewhere) in next(
        trip.detail for trip in report.breaker if trip.rule == RULE_WORKING_DIRECTORY
    )
    assert all(path.exists() for path in library)
    # And the names alone already keep every one of them.
    assert report.flagged == []


# --- a database that is not this data directory's ------------------------------


def test_breaker_a_wiped_database_with_the_demo_seed_even_with_allow_bulk(
    layout: Layout, any_db: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """Postgres volume lost, data/ intact, the setup wizard run again: its demo
    seed keeps the old install's demo file and inserts one row naming it, so
    the table is not empty and no stored path is missing."""
    library = _old_library(layout, recordings=40, documents=20)
    demo = write_file(layout.recordings / "demo_welcome_1.wav", size=50)
    add_recording(any_db, demo)

    with caplog.at_level(logging.ERROR, logger=hygiene.__name__):
        daily = run(any_db, delete=True)
    allowed = run(any_db, delete=True, bulk=True)

    assert RULE_SPLIT_BRAIN in rules(daily)
    assert rules(allowed) == [RULE_SPLIT_BRAIN, RULE_SPLIT_BRAIN]
    assert {d.path: d.candidates for d in allowed.directories} == {
        layout.documents.resolve(): 20,
        layout.recordings.resolve(): 80,
    }
    assert all(path.exists() for path in library) and demo.exists()
    errors = [
        r.getMessage()
        for r in caplog.records
        if r.name == hygiene.__name__ and r.levelno == logging.ERROR
    ]
    assert errors and "the database may not belong to this data directory" in errors[0]


@pytest.mark.parametrize("delete", [False, True], ids=["flag", "delete"])
def test_split_brain_is_an_error_only_when_files_would_go(
    layout: Layout, db: Engine, caplog: pytest.LogCaptureFixture, delete: bool
) -> None:
    """One stray old document and no others: a report-only run warns, every day,
    rather than raising an error."""
    add_referenced_audio(db, layout, 30)
    write_file(layout.documents / uuid_name(".pdf"))

    with caplog.at_level(logging.INFO, logger=hygiene.__name__):
        report = run(db, delete=delete)

    assert rules(report) == [RULE_SPLIT_BRAIN, RULE_BULK]
    trips = [
        r
        for r in caplog.records
        if r.name == hygiene.__name__ and "may not belong" in r.getMessage()
    ]
    assert [r.levelno for r in trips] == [logging.ERROR if delete else logging.WARNING]


@pytest.mark.parametrize("orphans", [2, 3], ids=["half", "over_half"])
def test_breaker_most_of_a_directorys_old_files_unreferenced(
    layout: Layout, db: Engine, orphans: int
) -> None:
    kept = add_referenced_audio(db, layout, 2)
    lost = [write_file(layout.recordings / uuid_name(".wav")) for _ in range(orphans)]
    # Recent files and unrecognised names are not counted either way.
    set_age(write_file(layout.recordings / uuid_name(".wav")), 1)
    write_file(layout.recordings / "holiday.mp3")

    report = run(db, delete=True, bulk=True)

    if orphans == 3:
        assert rules(report) == [RULE_SPLIT_BRAIN]
        assert all(path.exists() for path in lost)
    else:
        assert rules(report) == []
        assert not any(path.exists() for path in lost)
    assert all(path.exists() for path in kept)


# --- bulk removal -------------------------------------------------------------


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
    orphan = write_file(layout.recordings / uuid_name(".wav"), size=orphan_size)

    report = run(db, delete=True)

    assert rules(report) == ([RULE_BULK] if tripped else [])
    assert orphan.exists() is tripped


def test_the_bulk_rule_counts_each_directory_on_its_own(
    layout: Layout, db: Engine
) -> None:
    # One file in thirty-five overall, but a quarter of the documents directory,
    # by count alone: its bytes are well under a tenth.
    add_referenced_audio(db, layout, 30)
    add_referenced_documents(db, layout, 3, size=1000)
    orphan = write_file(layout.documents / uuid_name(".pdf"))

    report = run(db, delete=True)

    assert rules(report) == [RULE_BULK]
    assert str(layout.documents.resolve()) in report.breaker[0].detail
    assert orphan.exists()


def test_an_allowance_lifts_the_bulk_rule_for_its_directory_and_count_only(
    layout: Layout, db: Engine
) -> None:
    add_referenced_audio(db, layout, 3)
    add_referenced_documents(db, layout, 3)
    audio = [write_file(layout.recordings / uuid_name(".wav")) for _ in range(3)]
    document = write_file(layout.documents / uuid_name(".pdf"))
    recordings = layout.recordings.resolve()

    wrong_count = run(db, delete=True, bulk={recordings: 2})
    other_directory = run(db, delete=True, bulk={recordings: 3})

    assert rules(wrong_count) == [RULE_BULK, RULE_BULK]
    assert f"{recordings}: expected 2 files, but the run would remove 3" in [
        trip.detail for trip in wrong_count.breaker
    ]
    assert rules(other_directory) == [RULE_BULK]
    assert str(layout.documents.resolve()) in other_directory.breaker[0].detail
    assert all(path.exists() for path in [*audio, document])

    both = run(db, delete=True, bulk={layout.recordings: 3, Path("data/documents"): 1})

    assert rules(both) == []
    assert not any(path.exists() for path in [*audio, document])


def test_an_allowance_for_a_directory_the_run_does_not_scan_trips(
    layout: Layout, db: Engine, tmp_path: Path
) -> None:
    add_referenced_audio(db, layout, 20)
    orphan = write_file(layout.recordings / uuid_name(".wav"))
    elsewhere = tmp_path / "elsewhere"

    report = run(db, delete=True, bulk={elsewhere: 1})

    assert rules(report) == [RULE_BULK]
    assert (
        report.breaker[0].detail
        == f"{elsewhere}: allowed, but this run does not scan it"
    )
    assert orphan.exists()


# --- the restore lock ---------------------------------------------------------


def test_breaker_a_restore_holding_the_lock(layout: Layout, db: Engine) -> None:
    add_referenced_audio(db, layout, 1)
    orphan = write_file(layout.recordings / uuid_name(".wav"))

    report = run(db, delete=True, bulk=True, lock=FakeLockStore(True))

    assert rules(report) == [RULE_RESTORE_LOCK]
    assert report.breaker[0].detail == "the restore lock is set"
    assert orphan.exists()


def test_a_restore_running_during_the_listing_stops_the_deletion(
    layout: Layout, db: Engine
) -> None:
    # Held when the run starts and gone by the unlink: the restore may have
    # moved rows while the directories were listed and read.
    add_referenced_audio(db, layout, 1)
    orphan = write_file(layout.recordings / uuid_name(".wav"))

    report = run(db, delete=True, bulk=True, lock=FakeLockStore(True, False))

    assert rules(report) == [RULE_RESTORE_LOCK]
    assert orphan.exists()


def test_a_restore_that_starts_before_the_first_unlink_stops_the_deletion(
    layout: Layout, db: Engine
) -> None:
    add_referenced_audio(db, layout, 1)
    orphan = write_file(layout.recordings / uuid_name(".wav"))
    # Free at the start and before the clock probe, held by the first unlink.
    lock = FakeLockStore(False, False, True)

    report = run(db, delete=True, bulk=True, lock=lock)

    assert lock.reads == 3
    assert rules(report) == [RULE_RESTORE_LOCK]
    assert orphan.exists()


def test_the_lock_is_read_again_every_25_flagged_files(
    layout: Layout, db: Engine
) -> None:
    add_referenced_audio(db, layout, 30)
    orphans = [write_file(layout.recordings / uuid_name(".wav")) for _ in range(30)]
    # Free at the start, before the clock probe, at the first unlink, then
    # held by the 26th.
    lock = FakeLockStore(False, False, False, True)

    report = run(db, delete=True, bulk={layout.recordings: 30}, lock=lock)

    assert lock.reads == 4
    assert rules(report) == [RULE_RESTORE_LOCK]
    assert report.deleted == 25
    assert sum(path.exists() for path in orphans) == 5
    assert "partway, deletion stopped" in summary_line(report)


def test_breaker_an_unreachable_lock_store(
    layout: Layout, db: Engine, redis_contact_guard: list[str]
) -> None:
    add_referenced_audio(db, layout, 1)
    orphan = write_file(layout.recordings / uuid_name(".wav"))

    # The real client, which the suite's guard refuses as a Redis outage would.
    report = run_storage_hygiene(
        lambda: Session(db),
        HygieneOptions(delete=True, bulk_allowances={layout.recordings: 1}),
    )

    assert redis_contact_guard, "the run never tried to read the lock"
    redis_contact_guard.clear()
    assert rules(report) == [RULE_RESTORE_LOCK]
    assert "could not be read" in report.breaker[0].detail
    assert orphan.exists()


# --- what a run logs ----------------------------------------------------------


class _BrokenLockStore(FakeLockStore):
    def exists(self, key: str) -> int:
        raise ConnectionError("Error 111 connecting to redis:6379. Connection refused.")


def test_a_trip_is_a_warning_naming_its_rules_and_so_is_the_summary(
    layout: Layout, db: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    add_referenced_audio(db, layout, 1)
    write_file(layout.recordings / uuid_name(".wav"))

    with caplog.at_level(logging.INFO, logger=hygiene.__name__):
        report = run(db, delete=True, lock=_BrokenLockStore())

    records = [r for r in caplog.records if r.name == hygiene.__name__]
    warnings = [r.getMessage() for r in records if r.levelno == logging.WARNING]
    assert warnings == [
        "Storage hygiene circuit breaker tripped (bulk, restore_lock): "
        f"{report.breaker[0].detail}; the restore lock could not be read: "
        "Error 111 connecting to redis:6379. Connection refused"
    ]
    summary = records[-1]
    assert summary.levelno == logging.INFO
    assert summary.getMessage().endswith(
        "Circuit breaker tripped (bulk, restore_lock), nothing removed."
    )
    assert ".." not in summary.getMessage() and ".." not in warnings[0]
