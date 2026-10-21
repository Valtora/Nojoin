"""Which unreferenced files storage hygiene may remove, by their names.

Only names Nojoin writes are candidates; anything else in a scanned directory is
reported as unrecognised and never removed. A recording still uploading keeps
every file of its base name, so the capture master that finalize writes beside
the ``.wav`` its row names is kept before the row is updated to name it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine

from backend.api.v1.endpoints.recordings.helpers import _browser_master_output_path
from backend.models.recording import Recording, RecordingStatus
from backend.tests.storage_hygiene_harness import (
    Layout,
    add_recording,
    add_referenced_audio,
    make_layout,
    postgres_engine,
    rules,
    run,
    sqlite_engine,
    uuid_name,
    write_file,
)
from backend.utils.browser_capture_audio import _partial_path
from backend.utils.storage_hygiene import (
    AUDIO,
    PARTIAL_UPLOADS,
    PROXIES,
    UNRECOGNISED,
)
from backend.utils.storage_hygiene_scan import RECORDINGS_ROLE, References, StoredFile


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


def _nojoin_names() -> list[str]:
    """Every shape of name Nojoin gives a stored file or leaves from a cut write."""
    first, second = uuid.uuid4(), uuid.uuid4()
    return [
        f"{first}.flac",
        f"{uuid.uuid4()}.WAV",  # an old /upload kept the client's extension case
        f"{uuid.uuid4()}",
        f"Weekly sync__{uuid.uuid4()}.wav",  # a restored file whose name was taken
        f"{first}__{second}.flac",  # and one restored twice
        f"{uuid.uuid4()}.restored.wav",  # audio rebuilt from its proxy
        "demo_welcome_7.wav",
        f"{uuid.uuid4()}.flac.part",
        f"{uuid.uuid4()}.wav.{uuid.uuid4().hex}.upload-tmp",
    ]


_THEIR_NAMES = [
    "holiday-2019.mp3",
    "README.txt",
    "notes.part",
    "Backup.WAV",
    f"{str(uuid.uuid4()).upper()}.wav",
    f"{uuid.uuid4()}-copy.wav",
    f"{uuid.uuid4().hex}.wav",
    f"old.{uuid.uuid4().hex}.upload-tmp",
]


def test_only_names_nojoin_writes_are_removed(
    layout: Layout, db: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RECORDINGS_DIR pointed at a folder that also holds the operator's own files."""
    shared = tmp_path / "nas-recordings"
    monkeypatch.setenv("RECORDINGS_DIR", str(shared))
    for _ in range(20):
        add_recording(db, write_file(shared / uuid_name(".flac"), size=1000))
    theirs = [write_file(shared / name, size=900) for name in _THEIR_NAMES]
    ours = [write_file(shared / name) for name in _nojoin_names()]

    report = run(db, delete=True, bulk=True)

    assert rules(report) == []
    assert all(path.exists() for path in theirs)
    assert not any(path.exists() for path in ours)
    unrecognised = report.categories[UNRECOGNISED]
    assert (unrecognised.scanned, unrecognised.unreferenced) == (8, 8)
    assert unrecognised.deleted == 0
    assert report.reclaimable_bytes == 9 * 10
    assert report.categories[PARTIAL_UPLOADS].deleted == 2
    assert {flagged.path for flagged in report.flagged} == set(ours)


def test_unrecognised_files_alone_trip_nothing(
    layout: Layout, db: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared = tmp_path / "nas-recordings"
    monkeypatch.setenv("RECORDINGS_DIR", str(shared))
    add_recording(db, write_file(shared / uuid_name(".flac")))
    theirs = [write_file(shared / f"take-{n}.wav", size=1000) for n in range(30)]

    report = run(db, delete=True)

    assert rules(report) == []
    assert all(path.exists() for path in theirs)


@pytest.mark.parametrize("status", [RecordingStatus.UPLOADING, RecordingStatus.PAUSED])
@pytest.mark.parametrize("spelling", ["relative", "absolute"])
def test_a_capture_master_whose_finalize_never_committed_is_kept(
    layout: Layout, any_db: Engine, status: RecordingStatus, spelling: str
) -> None:
    """Finalize writes the master beside the ``.wav`` the row names and then
    commits the row naming it. When that commit fails, the row still names the
    ``.wav`` that never existed, and a day later the temp sweep has removed the
    segments, so the master is the only copy of the meeting."""
    add_referenced_audio(any_db, layout, 30)
    name = uuid_name(".wav")
    audio_path = (
        f"data/recordings/{name}"
        if spelling == "relative"
        else str(layout.recordings / name)
    )
    add_recording(any_db, audio_path, status=status)
    # Named as the app names them, so a change there fails here.
    master_path = _browser_master_output_path(
        Recording(name="m", audio_path=audio_path)
    )
    master = write_file(layout.app / master_path, size=5)
    partial = write_file(_partial_path(master), size=5)
    orphan = write_file(layout.recordings / uuid_name(".flac"))

    report = run(any_db, delete=True)

    assert rules(report) == []
    assert master.exists() and partial.exists()
    assert not orphan.exists()


def test_an_mp3_is_audio_unless_it_sits_beside_its_audio(
    layout: Layout, any_db: Engine
) -> None:
    add_referenced_audio(any_db, layout, 20)
    own_proxy = write_file(layout.recordings / uuid_name(".mp3"))
    add_recording(any_db, own_proxy, own_proxy)
    imported = write_file(layout.recordings / uuid_name(".mp3"), size=30)
    base = uuid_name("")
    write_file(layout.recordings / f"{base}.flac", size=1)
    proxy = write_file(layout.recordings / f"{base}.mp3", size=20)

    report = run(any_db, bulk=True)

    by_path = {flagged.path: flagged.category for flagged in report.flagged}
    assert by_path[imported] == AUDIO
    assert by_path[proxy] == PROXIES
    assert own_proxy not in by_path
    assert report.categories[AUDIO].scanned == 23
    assert report.categories[PROXIES].scanned == 1
    assert report.categories[PROXIES].missing_rows == 0


def test_a_file_two_columns_name_is_reported_as_audio(tmp_path: Path) -> None:
    """Whichever row is read first, as a recording's proxy that another names as
    its audio is."""
    path = write_file(tmp_path / uuid_name(".mp3"))
    status = path.stat()
    listed = StoredFile(
        path, tmp_path, RECORDINGS_ROLE, status.st_dev, status.st_ino, 10, 0
    )
    for order in ((PROXIES, AUDIO), (AUDIO, PROXIES)):
        references = References()
        for category in order:
            references.add(category, str(path), pending=False)

        assert references.category_of(listed) == AUDIO, order
