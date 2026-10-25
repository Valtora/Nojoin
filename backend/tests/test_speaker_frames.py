"""The screen study's frame store, its deletion and what it stays out of (#653).

Every frame here is drawn by the test itself: a flat background and a few
rectangles, like an empty meeting grid. No image is committed and none is a
real screen.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import logging
import stat
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from sqlalchemy import create_engine, text
from sqlmodel import Session

from backend.tests.speaker_frame_images import draw_frame
from backend.tests.sqlite_schemas import RECORDINGS_SCHEMA
from backend.utils import speaker_frames
from backend.utils.config_manager import DEFAULT_SYSTEM_CONFIG, ConfigManager
from backend.utils.logging_config import SpeakerFrameAccessFilter
from backend.utils.speaker_frames import __main__ as cli
from backend.utils.speaker_frames import settings as study_settings
from backend.utils.speaker_frames import storage
from backend.utils.speaker_frames.retention import sweep_speaker_frames

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(
        storage, "path_manager", SimpleNamespace(user_data_directory=root)
    )
    storage._summaries.clear()
    storage._announced.clear()
    storage._total_cache.clear()
    yield root
    storage._summaries.clear()
    storage._announced.clear()
    storage._total_cache.clear()


def _store(public_id: str, sequence: int = 0, offset_ms: int = 0, **kwargs) -> bytes:
    data = kwargs.pop("data", None) or draw_frame(highlight=sequence % 4)
    speaker_frames.store_frame(
        public_id,
        sequence=sequence,
        offset_ms=offset_ms,
        data=data,
        received_at=kwargs.pop("received_at", NOW),
    )
    return data


# --- settings ---------------------------------------------------------------


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def load(values: dict) -> ConfigManager:
        path = tmp_path / "config.json"
        path.write_text(json.dumps(values), encoding="utf-8")
        manager = ConfigManager(config_path=str(path))
        monkeypatch.setattr(study_settings, "config_manager", manager)
        return manager

    return load


def test_the_study_is_off_and_one_frame_a_second_by_default(config) -> None:
    assert DEFAULT_SYSTEM_CONFIG["speaker_frame_sampling"] == "off"
    assert DEFAULT_SYSTEM_CONFIG["speaker_frame_rate_hz"] == 1
    config({})
    assert speaker_frames.sampling_enabled() is False
    assert speaker_frames.frame_rate_hz() == 1


@pytest.mark.parametrize("value", ["owner", "Owner", " OWNER "])
def test_owner_turns_the_study_on(config, value: str) -> None:
    config({"speaker_frame_sampling": value})
    assert speaker_frames.sampling_enabled() is True


@pytest.mark.parametrize("value", ["on", "true", True, 1, "all", None, ""])
def test_any_other_value_leaves_the_study_off(config, value) -> None:
    config({"speaker_frame_sampling": value})
    assert speaker_frames.sampling_enabled() is False


@pytest.mark.parametrize(
    ("value", "expected"), [(1, 1), (2, 2), (3, 1), ("2", 1), (True, 1), (0, 1)]
)
def test_the_rate_is_one_or_two_and_anything_else_reads_as_one(
    config, value, expected: int
) -> None:
    config({"speaker_frame_rate_hz": value})
    assert speaker_frames.frame_rate_hz() == expected


def test_an_edit_to_config_json_reaches_the_next_read(config, tmp_path: Path) -> None:
    config({"speaker_frame_sampling": "off"})
    assert speaker_frames.sampling_enabled() is False
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"speaker_frame_sampling": "owner", "speaker_frame_rate_hz": 2}),
        encoding="utf-8",
    )
    assert speaker_frames.sampling_enabled() is True
    assert speaker_frames.frame_rate_hz() == 2


# --- the JPEG check ---------------------------------------------------------


def test_the_size_comes_from_the_frame_header() -> None:
    assert speaker_frames.jpeg_dimensions(draw_frame(width=1280, height=720)) == (
        1280,
        720,
    )
    assert speaker_frames.jpeg_dimensions(draw_frame(width=720, height=1280)) == (
        720,
        1280,
    )


def test_a_progressive_jpeg_is_read_too() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (640, 360), (10, 20, 30)).save(buffer, "JPEG", progressive=True)
    assert speaker_frames.jpeg_dimensions(buffer.getvalue()) == (640, 360)


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 36)).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.mark.parametrize(
    "body",
    [b"", b"not an image", _png(), draw_frame()[:-200], b"\xff\xd8\xff\xd9"],
    ids=["empty", "text", "png", "truncated", "no-header"],
)
def test_anything_but_a_whole_jpeg_is_refused(body: bytes) -> None:
    with pytest.raises(speaker_frames.InvalidFrame):
        speaker_frames.jpeg_dimensions(body)


# --- storing ----------------------------------------------------------------


def test_a_frame_is_stored_private_and_indexed_exactly_as_the_contract_says(
    data_dir: Path,
) -> None:
    public_id = str(uuid.uuid4())
    data = _store(public_id, sequence=7, offset_ms=1500)

    directory = data_dir / "speaker_frames" / public_id
    frame = directory / "7-1500.jpg"
    assert frame.read_bytes() == data
    assert stat.S_IMODE(frame.stat().st_mode) == 0o600
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((data_dir / "speaker_frames").stat().st_mode) == 0o700
    index = directory / "index.jsonl"
    assert stat.S_IMODE(index.stat().st_mode) == 0o600
    assert sorted(path.name for path in directory.iterdir()) == [
        "7-1500.jpg",
        "index.jsonl",
    ]

    (line,) = index.read_text(encoding="utf-8").splitlines()
    entry = json.loads(line)
    assert list(entry) == [
        "sequence",
        "offset_ms",
        "received_at",
        "bytes",
        "sha256",
        "width",
        "height",
        "platform_hint",
    ]
    assert entry == {
        "sequence": 7,
        "offset_ms": 1500,
        "received_at": "2026-10-25T12:00:00.000Z",
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "width": 1280,
        "height": 720,
        "platform_hint": None,
    }


def test_a_retried_frame_is_kept_once_and_indexed_once(data_dir: Path) -> None:
    public_id = str(uuid.uuid4())
    data = draw_frame()
    first = speaker_frames.store_frame(
        public_id, sequence=1, offset_ms=0, data=data, received_at=NOW
    )
    again = speaker_frames.store_frame(
        public_id, sequence=1, offset_ms=0, data=data, received_at=NOW
    )

    assert first.duplicate is False and again.duplicate is True
    assert again.summary == speaker_frames.FrameSummary(frames=1, bytes=len(data))
    index = data_dir / "speaker_frames" / public_id / "index.jsonl"
    assert len(index.read_text().splitlines()) == 1


def test_the_summary_counts_every_frame(data_dir: Path) -> None:
    public_id = str(uuid.uuid4())
    sizes = [len(_store(public_id, sequence=sequence)) for sequence in range(3)]
    storage._summaries.clear()
    assert speaker_frames.frames_summary(public_id) == speaker_frames.FrameSummary(
        frames=3, bytes=sum(sizes)
    )


@pytest.mark.parametrize(
    ("sequence", "offset_ms", "data"),
    [
        (-1, 0, None),
        (0, -1, None),
        (0, storage.MAX_OFFSET_MS + 1, None),
        (0, 0, draw_frame(width=2600, height=1462)),
        (0, 0, b"\xff\xd8\xff" + b"\x00" * (speaker_frames.MAX_FRAME_BYTES + 1)),
    ],
    ids=[
        "negative-sequence",
        "negative-offset",
        "offset-too-far",
        "too-big",
        "over-1mb",
    ],
)
def test_frames_out_of_bounds_are_refused(
    data_dir: Path, sequence, offset_ms, data
) -> None:
    with pytest.raises(speaker_frames.InvalidFrame):
        speaker_frames.store_frame(
            str(uuid.uuid4()),
            sequence=sequence,
            offset_ms=offset_ms,
            data=data or draw_frame(),
            received_at=NOW,
        )
    assert not (data_dir / "speaker_frames").exists() or not any(
        (data_dir / "speaker_frames").iterdir()
    )


@pytest.mark.parametrize("public_id", ["../escape", "a/b", "", "not-a-uuid", "A" * 36])
def test_only_a_recording_id_names_a_directory(data_dir: Path, public_id: str) -> None:
    with pytest.raises(speaker_frames.InvalidRecordingId):
        speaker_frames.store_frame(
            public_id, sequence=0, offset_ms=0, data=draw_frame(), received_at=NOW
        )
    assert speaker_frames.delete_recording_frames(public_id, reason="test") == 0


def test_new_frames_are_refused_once_the_total_passes_the_cap(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = str(uuid.uuid4())
    size = len(_store(first))
    monkeypatch.setattr(storage, "TOTAL_CAP_BYTES", size * 2 + 10)
    _store(str(uuid.uuid4()))

    with pytest.raises(speaker_frames.StorageCapReached):
        _store(str(uuid.uuid4()))
    # Deleting frames frees the room again.
    speaker_frames.delete_recording_frames(first, reason="test")
    _store(str(uuid.uuid4()))


def test_the_cap_counts_frames_another_process_wrote(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    public_id = str(uuid.uuid4())
    size = len(_store(public_id))
    monkeypatch.setattr(storage, "TOTAL_CAP_BYTES", size * 3)
    stranger = data_dir / "speaker_frames" / str(uuid.uuid4())
    stranger.mkdir()
    (stranger / "0-0.jpg").write_bytes(b"x" * (size * 2))
    assert speaker_frames.total_bytes(refresh=True) == size * 3
    with pytest.raises(speaker_frames.StorageCapReached):
        _store(public_id, sequence=1)


# --- stop, window and deletion ---------------------------------------------


def test_the_upload_window_closes_a_minute_after_the_recording_stops(
    data_dir: Path,
) -> None:
    public_id = str(uuid.uuid4())
    assert (
        speaker_frames.mark_recording_stopped(public_id, NOW) is False
    )  # no frames yet
    _store(public_id)
    assert speaker_frames.mark_recording_stopped(public_id, NOW) is True

    state = data_dir / "speaker_frame_state" / f"{public_id}.json"
    assert stat.S_IMODE(state.stat().st_mode) == 0o600
    assert speaker_frames.recording_stopped_at(public_id) == NOW
    assert speaker_frames.accepts_frames_after_stop(
        public_id, now=NOW + timedelta(seconds=59)
    )
    assert not speaker_frames.accepts_frames_after_stop(
        public_id, now=NOW + timedelta(seconds=61)
    )
    assert speaker_frames.delete_after(public_id) == NOW + timedelta(days=14)


def test_a_recording_that_never_stopped_has_no_window(data_dir: Path) -> None:
    public_id = str(uuid.uuid4())
    _store(public_id)
    assert not speaker_frames.accepts_frames_after_stop(public_id, now=NOW)
    assert speaker_frames.delete_after(public_id, now=NOW) == NOW + timedelta(days=14)


def test_deleting_removes_the_frames_and_the_state_and_logs_one_line(
    data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    public_id = str(uuid.uuid4())
    for sequence in range(3):
        _store(public_id, sequence=sequence)
    speaker_frames.mark_recording_stopped(public_id, NOW)

    caplog.set_level(logging.INFO, logger=storage.logger.name)
    caplog.clear()
    assert speaker_frames.delete_recording_frames(public_id, reason="discarded") == 3

    assert not (data_dir / "speaker_frames" / public_id).exists()
    assert not (data_dir / "speaker_frame_state" / f"{public_id}.json").exists()
    assert speaker_frames.frames_summary(public_id).frames == 0
    assert [record.getMessage() for record in caplog.records] == [
        f"Deleted 3 screen study frames of recording {public_id} (discarded)"
    ]


def test_storing_logs_one_line_per_capture_and_nothing_per_frame(
    data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    public_id = str(uuid.uuid4())
    caplog.set_level(logging.DEBUG)
    frames = [_store(public_id, sequence=sequence) for sequence in range(5)]

    mine = [record for record in caplog.records if record.name.startswith("backend")]
    assert [record.getMessage() for record in mine] == [
        f"Screen study frames are being stored for recording {public_id}"
    ]
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for data in frames:
        assert hashlib.sha256(data).hexdigest() not in logged


@pytest.mark.parametrize(
    ("path", "kept"),
    [
        ("/api/v1/recordings/0b0c/speaker-frames?sequence=3&offset_ms=0", False),
        ("/api/v1/recordings/0b0c/speaker-frames", False),
        ("/api/v1/recordings/0b0c/segment?sequence=3", True),
        ("/api/v1/recordings/0b0c?view=speaker-frames", True),
    ],
)
def test_the_api_access_log_leaves_out_frame_requests(path: str, kept: bool) -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("10.0.0.1:5000", "POST", path, "1.1", 200),
        None,
    )
    assert SpeakerFrameAccessFilter().filter(record) is kept


def test_the_api_installs_the_access_filter() -> None:
    import backend.main  # noqa: F401 -- the API installs its log filters at import

    access = logging.getLogger("uvicorn.access")
    assert any(isinstance(f, SpeakerFrameAccessFilter) for f in access.filters)


# --- retention --------------------------------------------------------------


@pytest.fixture
def recordings_db(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'sweep.sqlite'}")
    with engine.begin() as connection:
        connection.execute(text(RECORDINGS_SCHEMA))

    def add(
        public_id: str, *, status: str, is_deleted: bool = False, last_activity=None
    ):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO recordings (created_at, updated_at, name, public_id,"
                    " meeting_uid, audio_path, status, upload_progress,"
                    " processing_progress, is_archived, is_deleted, last_activity_at,"
                    " capture_epoch, user_id) VALUES (:created, :created, 'Study',"
                    " :public_id, :uid, :audio, :status, 0, 0, 0, :deleted, :activity,"
                    " 0, 1)"
                ),
                {
                    "created": (NOW - timedelta(days=30)).replace(tzinfo=None),
                    "public_id": public_id,
                    "uid": str(uuid.uuid4()),
                    "audio": f"recordings/{public_id}.flac",
                    "status": status,
                    "deleted": is_deleted,
                    "activity": last_activity.replace(tzinfo=None)
                    if last_activity
                    else None,
                },
            )

    yield SimpleNamespace(engine=engine, add=add)
    engine.dispose()


def test_the_sweep_deletes_what_is_due_and_keeps_the_rest(
    data_dir: Path, recordings_db
) -> None:
    ids = {
        name: str(uuid.uuid4())
        for name in (
            "recording",
            "stale_capture",
            "fresh",
            "due",
            "unstamped",
            "binned",
            "gone",
        )
    }
    recordings_db.add(ids["recording"], status="UPLOADING", last_activity=NOW)
    recordings_db.add(
        ids["stale_capture"], status="PAUSED", last_activity=NOW - timedelta(days=15)
    )
    recordings_db.add(ids["fresh"], status="PROCESSED")
    recordings_db.add(ids["due"], status="PROCESSED")
    recordings_db.add(ids["unstamped"], status="QUEUED")
    recordings_db.add(ids["binned"], status="PROCESSED", is_deleted=True)
    for public_id in ids.values():
        _store(public_id)
    speaker_frames.mark_recording_stopped(ids["fresh"], NOW - timedelta(days=13))
    speaker_frames.mark_recording_stopped(
        ids["due"], NOW - timedelta(days=14, minutes=1)
    )
    unknown = data_dir / "speaker_frames" / "not-a-recording"
    unknown.mkdir()
    orphan_state = data_dir / "speaker_frame_state" / f"{uuid.uuid4()}.json"
    orphan_state.write_text('{"stopped_at": "2026-10-01T00:00:00Z"}')

    with Session(recordings_db.engine) as session:
        result = sweep_speaker_frames(session, now=NOW)

    assert sorted(result.deleted_recordings) == sorted(
        [ids["stale_capture"], ids["due"], ids["binned"], ids["gone"]]
    )
    assert result.deleted_frames == 4
    left = {path.name for path in (data_dir / "speaker_frames").iterdir()}
    assert left == {ids["recording"], ids["fresh"], ids["unstamped"], "not-a-recording"}
    assert result.unrecognised == ["not-a-recording"]
    # A stopped recording with no stop time saved gets one, and 14 days from it.
    assert speaker_frames.recording_stopped_at(ids["unstamped"]) == NOW
    assert not orphan_state.exists()


def test_the_sweep_runs_with_the_study_switched_off(
    data_dir: Path, recordings_db, config
) -> None:
    config({"speaker_frame_sampling": "off"})
    public_id = str(uuid.uuid4())
    recordings_db.add(public_id, status="PROCESSED")
    _store(public_id)
    speaker_frames.mark_recording_stopped(public_id, NOW - timedelta(days=20))

    with Session(recordings_db.engine) as session:
        assert sweep_speaker_frames(session, now=NOW).deleted_recordings == [public_id]


def test_the_sweep_is_scheduled_hourly_on_the_io_lane() -> None:
    from backend.celery_app import IO_QUEUE, TASK_ROUTES, celery_app

    entry = celery_app.conf.beat_schedule["sweep-speaker-frames-every-1h"]
    assert entry == {
        "task": "backend.worker.tasks.sweep_speaker_frames_task",
        "schedule": 3600.0,
    }
    assert TASK_ROUTES["backend.worker.tasks.sweep_speaker_frames_task"] == {
        "queue": IO_QUEUE
    }


# --- purge and audit --------------------------------------------------------


def test_purge_saves_the_hashes_first_then_deletes(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    keep, purge = str(uuid.uuid4()), str(uuid.uuid4())
    _store(keep)
    frames = [_store(purge, sequence=sequence) for sequence in range(2)]

    assert cli.main(["purge", purge]) == 0

    assert not (data_dir / "speaker_frames" / purge).exists()
    assert (data_dir / "speaker_frames" / keep).is_dir()
    (hash_list,) = (data_dir / "speaker_frame_hashes").iterdir()
    assert stat.S_IMODE(hash_list.stat().st_mode) == 0o600
    assert stat.S_IMODE((data_dir / "speaker_frame_hashes").stat().st_mode) == 0o700
    assert hash_list.read_text().splitlines() == [
        f"{hashlib.sha256(data).hexdigest()}  {purge}/{sequence}-0.jpg"
        for sequence, data in enumerate(frames)
    ]
    assert "Deleted 2 frames from 1 recordings." in capsys.readouterr().out


def test_purge_all_takes_every_recording(data_dir: Path) -> None:
    for _ in range(3):
        _store(str(uuid.uuid4()))
    assert cli.main(["purge", "--all"]) == 0
    assert list((data_dir / "speaker_frames").iterdir()) == []
    (hash_list,) = (data_dir / "speaker_frame_hashes").iterdir()
    assert len(hash_list.read_text().splitlines()) == 3


def test_purge_refuses_a_name_that_is_not_a_recording_id(data_dir: Path) -> None:
    assert cli.main(["purge", "../speaker_frames"]) == cli.EXIT_USAGE
    assert cli.main(["purge"]) == cli.EXIT_USAGE


def test_audit_reports_counts_and_never_content(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    public_id = str(uuid.uuid4())
    frames = [_store(public_id, sequence=sequence) for sequence in range(2)]
    speaker_frames.mark_recording_stopped(public_id, NOW)

    assert cli.main(["audit"]) == 0

    out = capsys.readouterr().out
    assert f"{public_id}  frames=2  bytes={sum(map(len, frames))}  indexed=2" in out
    assert "Recordings with frames: 1, frames: 2" in out
    assert "State files: 1" in out
    for data in frames:
        assert hashlib.sha256(data).hexdigest() not in out


def test_audit_scan_finds_a_copy_left_behind_even_inside_a_zip(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    public_id = str(uuid.uuid4())
    data = _store(public_id)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "copy.jpg").write_bytes(data)
    with zipfile.ZipFile(elsewhere / "backup.zip", "w") as archive:
        archive.writestr("documents/frame.jpg", data)
        archive.writestr("documents/other.jpg", draw_frame(highlight=3))
    assert cli.main(["purge", "--all"]) == 0
    capsys.readouterr()

    assert cli.main(["audit", "--scan", str(elsewhere)]) == cli.EXIT_MATCH

    out = capsys.readouterr().out
    assert "3 JPEG files checked, 2 match a saved hash" in out
    assert f"match: {elsewhere / 'copy.jpg'}" in out
    assert f"match: {elsewhere / 'backup.zip'}!documents/frame.jpg" in out
    (elsewhere / "copy.jpg").unlink()
    (elsewhere / "backup.zip").unlink()
    assert cli.main(["audit", "--scan", str(elsewhere)]) == cli.EXIT_OK


# --- what the frames stay out of --------------------------------------------

# The only backend modules that may name the study's code or its directories.
# Exports, MCP, search, backups, telemetry, notes and the document pipeline
# are not among them, so nothing they do can read a frame.
ALLOWED_MODULES = {
    "backend/utils/speaker_frames/__init__.py",
    "backend/utils/speaker_frames/__main__.py",
    "backend/utils/speaker_frames/jpeg.py",
    "backend/utils/speaker_frames/retention.py",
    "backend/utils/speaker_frames/settings.py",
    "backend/utils/speaker_frames/storage.py",
    "backend/api/v1/endpoints/recordings/routes_speaker_frames.py",
    "backend/api/v1/endpoints/recordings/__init__.py",
    "backend/api/v1/endpoints/recordings/routes_actions.py",
    "backend/api/v1/endpoints/recordings/routes_batch_init.py",
    "backend/api/v1/endpoints/recordings/routes_capture.py",
    "backend/api/v1/endpoints/users.py",
    "backend/worker/tasks/system.py",
    "backend/utils/config_manager.py",
    "backend/celery_app.py",
}


def _backend_sources() -> list[Path]:
    return [
        path
        for path in (REPO_ROOT / "backend").rglob("*.py")
        if "tests" not in path.relative_to(REPO_ROOT).parts
    ]


def test_only_the_study_and_the_delete_paths_reach_the_frames() -> None:
    reaching = set()
    for path in _backend_sources():
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = any(
            (
                isinstance(node, ast.ImportFrom)
                and (node.module or "").startswith("backend.utils.speaker_frames")
            )
            or (
                isinstance(node, ast.ImportFrom)
                and node.module == "backend.utils"
                and any(alias.name == "speaker_frames" for alias in node.names)
            )
            or (
                isinstance(node, ast.Import)
                and any(
                    alias.name.startswith("backend.utils.speaker_frames")
                    for alias in node.names
                )
            )
            for node in ast.walk(tree)
        )
        names_a_directory = any(
            name in source
            for name in (
                "speaker_frames",
                "speaker_frame_state",
                "speaker_frame_hashes",
            )
        )
        if imports or names_a_directory:
            reaching.add(path.relative_to(REPO_ROOT).as_posix())
    assert reaching <= ALLOWED_MODULES, sorted(reaching - ALLOWED_MODULES)


@pytest.mark.parametrize(
    "package",
    ["backend/services/exports", "backend/mcp_server", "backend/core/backup"],
)
def test_exports_mcp_and_backups_never_name_the_frames(package: str) -> None:
    for path in (REPO_ROOT / package).rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "speaker_frame" not in source, path


def test_search_and_telemetry_never_name_the_frames() -> None:
    for relative in (
        "backend/services/context_search.py",
        "backend/utils/telemetry.py",
        "backend/worker/tasks/embeddings.py",
        "backend/worker/tasks/documents.py",
    ):
        assert "speaker_frame" not in (REPO_ROOT / relative).read_text(encoding="utf-8")


def test_the_data_directory_stays_out_of_git_and_of_every_image() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    dockerignore = (
        (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    )
    assert "/data" in gitignore
    assert "data/" in dockerignore


def test_a_backup_carries_no_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from backend.core.backup import BackupManager
    from backend.core.backup.format import ARCHIVE_QUALITY_ORIGINAL
    from backend.tests.backup_harness import (
        build_test_context,
        patch_backup_manager,
        seed_source_data,
    )

    context = build_test_context(tmp_path / "source")
    patch_backup_manager(monkeypatch, context)
    monkeypatch.setattr(storage, "path_manager", context.path_manager)
    storage._summaries.clear()
    storage._total_cache.clear()
    public_id = str(uuid.uuid4())
    audio = context.path_manager.recordings_directory / "quarterly-planning.wav"
    audio.write_bytes(b"RIFF0000WAVE")

    async def run() -> Path:
        await seed_source_data(
            context.async_session_maker,
            recording_public_id=public_id,
            recording_audio_path=str(audio),
            recording_proxy_path=None,
        )
        zip_path, _ = await BackupManager.create_backup(
            include_audio=True, archive_quality=ARCHIVE_QUALITY_ORIGINAL
        )
        await context.async_engine.dispose()
        return Path(zip_path)

    frames = [_store(public_id, sequence=sequence) for sequence in range(3)]
    speaker_frames.mark_recording_stopped(public_id, NOW)
    zip_path = asyncio.run(run())

    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        assert names, "the backup is empty"
        assert not [name for name in names if "speaker_frame" in name]
        for name in names:
            body = archive.read(name)
            for data in frames:
                assert data not in body, name
            assert b"speaker_frame" not in body, name


def test_telemetry_carries_nothing_of_the_study(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    from backend.utils import telemetry

    class _Scalar:
        def one(self) -> int:
            return 1

    class FakeSession:
        # build_payload only reads scalars, as in test_telemetry.py.
        def exec(self, _statement) -> _Scalar:
            return _Scalar()

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"speaker_frame_sampling": "owner", "speaker_frame_rate_hz": 2}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        telemetry, "config_manager", ConfigManager(config_path=str(config_path))
    )
    monkeypatch.setattr(
        telemetry.path_manager, "_user_data_directory", tmp_path, raising=False
    )
    _store(str(uuid.uuid4()))

    serialised = json.dumps(telemetry.build_payload(FakeSession())).lower()

    assert "speaker_frame" not in serialised
    assert "screen" not in serialised
    assert "frame" not in serialised
