"""An import's audio is kept by ``keep_imported_audio_task``, before processing.

Every import route stores the upload as it arrived and queues the task; the
task keeps the audio and queues processing. The routes and the task share a
SQLite file here, so a test follows an upload from the route through the task.
The tests of concurrent copies of the task, and of a delete racing it, also
run on PostgreSQL when ``NOJOIN_TEST_POSTGRES_URL`` names one. Celery
dispatches are recorded (``stub_celery_dispatch``); the ffmpeg cases skip
without ffmpeg.
"""

from __future__ import annotations

import contextlib
import importlib
import os
import resource
import shutil
import subprocess
import threading
import types
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel, create_engine

from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.models.recording import Recording, RecordingStatus
from backend.tests.test_reprocess import (
    RECORDING_AUDIO_CHUNKS_SCHEMA,
    RECORDING_AUDIO_WINDOW_MANIFESTS_SCHEMA,
    RECORDINGS_SCHEMA,
    build_test_user,
)
from backend.utils import import_audio
from backend.utils.import_audio import (
    KEEPING_AUDIO_STEP,
    AudioExtractionError,
    KeptAudio,
    NoAudioStreamError,
    UnreadableAudioStreamError,
)
from backend.worker.tasks import imported_audio
from backend.worker.tasks.imported_audio import (
    SERVER_FAILURE_DETAIL,
    keep_imported_audio_task,
)

KEEP_TASK = "backend.worker.tasks.keep_imported_audio_task"
PROCESS_TASK = "backend.worker.tasks.process_recording_task"
PROXY_TASK = "backend.worker.tasks.generate_proxy_task"
ROUTES = ["import", "chunked", "upload"]

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg is not installed"
)


@pytest.fixture
def recordings_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "recordings"
    root.mkdir()
    monkeypatch.setenv("RECORDINGS_DIR", str(root))
    return root


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "nojoin.sqlite"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        for schema in (
            RECORDINGS_SCHEMA,
            RECORDING_AUDIO_CHUNKS_SCHEMA,
            RECORDING_AUDIO_WINDOW_MANIFESTS_SCHEMA,
        ):
            connection.execute(text(schema))
    engine.dispose()
    return path


@pytest.fixture
def sync_engine(db_path: Path):
    engine = create_engine(f"sqlite:///{db_path}")
    yield engine
    engine.dispose()


@contextlib.asynccontextmanager
async def _no_upload_limit(*args, **kwargs):
    yield


@contextlib.asynccontextmanager
async def _api_client(async_engine, monkeypatch: pytest.MonkeyPatch):
    from backend.api.v1.endpoints.recordings import routes_import_upload

    # /upload's concurrency limit lives in Redis, which no test may touch.
    monkeypatch.setattr(
        routes_import_upload, "enforce_upload_concurrency", _no_upload_limit
    )
    maker = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    async def override_get_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = build_test_user
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", timeout=60
    ) as async_client:
        yield async_client
    await async_engine.dispose()


@pytest.fixture
async def client(db_path: Path, monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with _api_client(engine, monkeypatch) as async_client:
        yield async_client


@dataclass
class _Env:
    """An import's world: the API, the database the task reads, the
    recordings directory, the recorded dispatches and a scratch directory."""

    client: AsyncClient
    engine: Engine
    recordings: Path
    dispatched: list
    tmp: Path


@pytest.fixture
def env(
    client: AsyncClient,
    sync_engine: Engine,
    recordings_dir: Path,
    stub_celery_dispatch: list,
    tmp_path: Path,
) -> _Env:
    return _Env(client, sync_engine, recordings_dir, stub_celery_dispatch, tmp_path)


_USER_ROW = (
    "INSERT INTO users (id, created_at, updated_at, username, hashed_password,"
    " is_active, is_superuser, force_password_change, role, token_version,"
    " settings, has_seen_demo_recording) VALUES (1, now(), now(), 'alice', 'x',"
    " true, false, false, 'user', 0, '{}', false)"
)


@pytest.fixture
async def pg_env(
    postgres_test_url: str,
    recordings_dir: Path,
    stub_celery_dispatch: list,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """``env`` on PostgreSQL, in a schema of its own."""
    importlib.import_module("backend.models.registry")
    schema = f"import_task_{uuid.uuid4().hex[:12]}"
    admin = create_engine(postgres_test_url)
    with admin.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    engine = create_engine(
        postgres_test_url, connect_args={"options": f"-csearch_path={schema},public"}
    )
    SQLModel.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text(_USER_ROW))
    async_engine = create_async_engine(
        postgres_test_url.replace("postgresql://", "postgresql+asyncpg://", 1),
        connect_args={"server_settings": {"search_path": f"{schema},public"}},
    )
    try:
        async with _api_client(async_engine, monkeypatch) as async_client:
            yield _Env(
                async_client, engine, recordings_dir, stub_celery_dispatch, tmp_path
            )
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        admin.dispose()


@pytest.fixture(params=["sqlite", "postgres"])
def any_env(request: pytest.FixtureRequest) -> _Env:
    return request.getfixturevalue("env" if request.param == "sqlite" else "pg_env")


async def _post(env: _Env, route: str, source: Path):
    """Send ``source`` through /import, chunked import or /upload."""
    if route != "chunked":
        return await env.client.post(
            f"/api/v1/recordings/{route}",
            files={"file": (source.name, source.read_bytes(), "video/x-test")},
        )
    init = await env.client.post(
        "/api/v1/recordings/import/chunked/init", params={"filename": source.name}
    )
    assert init.status_code == 200, init.text
    recording_id = init.json()["id"]
    segment = await env.client.post(
        "/api/v1/recordings/import/chunked/segment",
        params={"recording_id": recording_id, "sequence": 0},
        files={"file": ("0.part", source.read_bytes(), "application/octet-stream")},
    )
    assert segment.status_code == 200, segment.text
    return await env.client.post(
        "/api/v1/recordings/import/chunked/finalize",
        params={"recording_id": recording_id},
    )


async def _import(env: _Env, route: str, source: Path) -> int:
    """Import ``source`` through ``route``; return the recording's id."""
    response = await _post(env, route, source)
    assert response.status_code == 200, response.text
    return _recording(env)["id"]


def _recording(env: _Env) -> dict:
    with env.engine.connect() as connection:
        return dict(
            connection.execute(
                text(
                    "SELECT id, status, processing_step, audio_path, proxy_path,"
                    " duration_seconds, file_size_bytes, celery_task_id"
                    " FROM recordings"
                )
            )
            .mappings()
            .one()
        )


def _staged_import_chunks(env: _Env) -> list[tuple[str, int]]:
    with env.engine.connect() as connection:
        return [
            tuple(row)
            for row in connection.execute(
                text(
                    "SELECT storage_path, duration_ms FROM recording_audio_chunks"
                    " WHERE source_kind = 'import'"
                )
            )
        ]


# The task's body, so that copies can run at once, each on its own session as
# on separate workers (the bound task shares one ``session`` attribute).
_TASK_BODY = type(keep_imported_audio_task._get_current_object()).run


def _run_task(env: _Env, recording_id: int) -> None:
    """Run the task as a worker does, on its own session."""
    session = Session(env.engine)
    try:
        _TASK_BODY(types.SimpleNamespace(session=session), recording_id)
    finally:
        session.close()


def _dispatched_names(env: _Env) -> list[str]:
    return [name for name, _, _ in env.dispatched]


def _files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def _screen_recording(path: Path, audio_args: list[str]) -> None:
    _ffmpeg(
        *["-f", "lavfi", "-i", "testsrc=size=64x64:rate=25:duration=2"],
        *["-f", "lavfi", "-i", "sine=frequency=440:duration=2:sample_rate=48000"],
        *["-c:v", "mpeg4", *audio_args, "-shortest", str(path)],
    )


def _stream_types(path: Path | str) -> list[str]:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type"]
        + ["-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return probe.stdout.split()


def _assert_only_audio_kept(env: _Env, audio_path: str) -> None:
    """The kept audio is the recording's only file, and no file holds video.

    Chunked import's received ``.part`` files are left for the daily sweep,
    as for every import, and are not read again.
    """
    kept = [path for path in _files(env.recordings) if path.suffix != ".part"]
    outside_temp = [
        path for path in kept if "temp" not in path.relative_to(env.recordings).parts
    ]
    assert outside_temp == [Path(audio_path)]
    for path in kept:
        assert _stream_types(path) == ["audio"], path


def _assert_failed_and_removed(env: _Env, detail: str) -> None:
    """ERROR with ``detail`` for the library to show, no file left, nothing
    queued after the task."""
    row = _recording(env)
    assert (row["status"], row["processing_step"]) == ("ERROR", detail)
    assert _files(env.recordings) == []
    assert _dispatched_names(env) == [KEEP_TASK]


def _fixed_chunk_duration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the import window be built from bytes ffprobe cannot read."""
    from backend.utils import recording_audio_sync

    monkeypatch.setattr(recording_audio_sync, "get_audio_duration", lambda path: 2.0)


@pytest.mark.anyio
@pytest.mark.parametrize("route", ROUTES)
async def test_every_import_route_queues_the_task_in_place_of_processing(
    env: _Env, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    """No route extracts anything: it stores the upload as it came and queues
    the task, which queues processing and the proxy once the audio is kept."""
    _fixed_chunk_duration(monkeypatch)
    source = env.tmp / "screen.mkv"
    source.write_bytes(b"an upload the route never reads")

    response = await _post(env, route, source)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "QUEUED"
    row = _recording(env)
    assert env.dispatched == [(KEEP_TASK, [row["id"]], None)]
    assert row["celery_task_id"] == "stub-task-1"
    assert (row["status"], row["processing_step"]) == ("QUEUED", KEEPING_AUDIO_STEP)
    assert Path(row["audio_path"]).suffix == ".mkv"
    assert Path(row["audio_path"]).read_bytes() == source.read_bytes()


@pytest.mark.anyio
@needs_ffmpeg
@pytest.mark.parametrize("route", ROUTES)
async def test_the_kept_audio_replaces_the_upload_before_processing(
    env: _Env, route: str
) -> None:
    """MP3 audio is copied out of the MKV and the upload deleted. As an MP3 it
    is its own playback proxy, and the import's audio window is rebuilt from
    it. Processing is queued once."""
    source = env.tmp / "screen.mkv"
    _screen_recording(source, ["-c:a", "libmp3lame", "-b:a", "192k"])
    recording_id = await _import(env, route, source)
    upload = _recording(env)["audio_path"]

    _run_task(env, recording_id)

    row = _recording(env)
    assert row["audio_path"].endswith(".mp3")
    assert not Path(upload).exists()
    assert (row["status"], row["processing_step"]) == ("QUEUED", None)
    assert row["duration_seconds"] == pytest.approx(2.0, abs=0.1)
    assert row["file_size_bytes"] == Path(row["audio_path"]).stat().st_size
    assert env.dispatched[1:] == [
        (PROCESS_TASK, [recording_id], None),
        (PROXY_TASK, [recording_id], None),
    ]
    assert row["celery_task_id"] == "stub-task-2"
    _assert_only_audio_kept(env, row["audio_path"])
    if route == "upload":
        assert _staged_import_chunks(env) == []
    else:
        [(storage_path, duration_ms)] = _staged_import_chunks(env)
        assert storage_path.endswith(".mp3")
        assert duration_ms == pytest.approx(2000, abs=100)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("name", "queued"),
    [
        ("meeting.wav", [PROCESS_TASK, PROXY_TASK]),
        # An MP3 is its own playback proxy.
        ("meeting.mp3", [PROCESS_TASK]),
    ],
)
async def test_an_audio_file_is_queued_for_processing_as_it_came(
    env: _Env, monkeypatch: pytest.MonkeyPatch, name: str, queued: list[str]
) -> None:
    """A format that holds no video is neither probed nor rewritten, so this
    needs no ffmpeg."""
    _fixed_chunk_duration(monkeypatch)
    source = env.tmp / name
    source.write_bytes(b"audio")
    recording_id = await _import(env, "import", source)
    before = _recording(env)

    _run_task(env, recording_id)

    row = _recording(env)
    assert (row["audio_path"], row["processing_step"]) == (before["audio_path"], None)
    assert Path(row["audio_path"]).read_bytes() == b"audio"
    assert _dispatched_names(env)[1:] == queued


# Each media container import accepts, with a video track wherever the container
# can carry one, and ffmpeg's built-in encoders only: (suffix, output arguments).
_MEDIA_CONTAINER_FIXTURES = [
    (".mkv", ["-c:v", "mpeg4", "-c:a", "aac"]),
    (".mka", ["-c:a", "flac"]),
    (".mov", ["-c:v", "mpeg4", "-c:a", "aac"]),
    (".avi", ["-c:v", "mpeg4", "-c:a", "mp2"]),
    (".m4v", ["-c:v", "mpeg4", "-c:a", "aac", "-f", "mp4"]),
    (".ts", ["-c:v", "mpeg4", "-c:a", "aac"]),
    (".mts", ["-c:v", "mpeg4", "-c:a", "ac3", "-f", "mpegts"]),
    (".mpg", ["-c:v", "mpeg2video", "-c:a", "mp2"]),
    (".mpeg", ["-c:v", "mpeg1video", "-c:a", "mp2"]),
    (".3gp", ["-c:v", "mpeg4", "-c:a", "aac", "-ar", "16000", "-ac", "1"]),
]

# What an import of one of them is stored as: formats import accepted before
# media containers were, which the pipeline already reads.
_STORED_AUDIO_SUFFIXES = {".m4a", ".mp3", ".webm", ".ogg", ".flac"}


@pytest.mark.anyio
@needs_ffmpeg
@pytest.mark.parametrize(("suffix", "output_args"), _MEDIA_CONTAINER_FIXTURES)
async def test_each_media_container_is_stored_as_audio_processing_reads(
    env: _Env, suffix: str, output_args: list[str]
) -> None:
    """The kept audio decodes to the 16 kHz mono the pipeline works on."""
    import soundfile as sf

    from backend.processing.audio_preprocessing import (
        preprocess_audio_for_diarization,
    )

    inputs = ["-f", "lavfi", "-i", "sine=frequency=440:duration=2:sample_rate=48000"]
    if "-c:v" in output_args:
        inputs = [
            *["-f", "lavfi", "-i", "testsrc=size=64x64:rate=25:duration=2"],
            *inputs,
        ]
    source = env.tmp / f"meeting{suffix}"
    _ffmpeg(*inputs, *output_args, "-shortest", str(source))

    _run_task(env, await _import(env, "import", source))

    row = _recording(env)
    assert Path(row["audio_path"]).suffix in _STORED_AUDIO_SUFFIXES
    assert row["duration_seconds"] == pytest.approx(2.0, abs=0.2)
    _assert_only_audio_kept(env, row["audio_path"])
    processed = preprocess_audio_for_diarization(row["audio_path"])
    assert processed is not None
    try:
        info = sf.info(processed)
        assert (info.channels, info.samplerate) == (1, 16_000)
        assert info.duration == pytest.approx(2.0, abs=0.2)
    finally:
        Path(processed).unlink()


@pytest.mark.anyio
@needs_ffmpeg
async def test_the_duration_is_the_audio_s_not_the_video_s(env: _Env) -> None:
    """A screen recording whose video outlasts its audio is as long as its audio."""
    source = env.tmp / "screen.mkv"
    _ffmpeg(
        *["-f", "lavfi", "-i", "testsrc=size=64x64:rate=25:duration=6"],
        *["-f", "lavfi", "-i", "sine=frequency=440:duration=2"],
        *["-c:v", "mpeg4", "-c:a", "aac", str(source)],
    )

    _run_task(env, await _import(env, "import", source))

    assert _recording(env)["duration_seconds"] == pytest.approx(2.0, abs=0.1)


@pytest.mark.anyio
@needs_ffmpeg
@pytest.mark.parametrize(
    ("first_track", "dispositions"),
    [
        # 3 s of audio, not flagged default: the default second track wins.
        (["-f", "lavfi", "-i", "sine=frequency=440:duration=3"], ("0", "default")),
        # Flagged default but holding no packets: ffmpeg passes it over.
        (
            ["-ss", "30", "-f", "lavfi", "-i", "sine=frequency=440:duration=1"],
            ("default", "0"),
        ),
    ],
    ids=["default-track", "empty-default-track"],
)
async def test_the_kept_track_is_the_one_ffmpeg_decodes(
    env: _Env, first_track: list[str], dispositions: tuple[str, str]
) -> None:
    """Of two audio tracks, only the one ffmpeg decodes is stored. The second
    holds 5 s of audio."""
    source = env.tmp / "two-tracks.mkv"
    _ffmpeg(
        *["-f", "lavfi", "-i", "testsrc=size=64x64:rate=25:duration=5"],
        *first_track,
        *["-f", "lavfi", "-i", "sine=frequency=880:duration=5"],
        *["-map", "0:v", "-map", "1:a", "-map", "2:a"],
        *["-c:v", "mpeg4", "-c:a", "aac", "-ac", "2"],
        *["-disposition:a:0", dispositions[0], "-disposition:a:1", dispositions[1]],
        str(source),
    )

    _run_task(env, await _import(env, "import", source))

    row = _recording(env)
    assert _stream_types(row["audio_path"]) == ["audio"]
    assert row["duration_seconds"] == pytest.approx(5.0, abs=0.1)


@pytest.mark.anyio
@needs_ffmpeg
@pytest.mark.parametrize("route", ROUTES)
async def test_a_file_without_audio_fails_the_import_and_leaves_no_file(
    env: _Env, route: str
) -> None:
    """What /import once refused with 400 is now the recording's error."""
    source = env.tmp / "silent.mkv"
    _ffmpeg(
        *["-f", "lavfi", "-i", "testsrc=size=64x64:rate=25:duration=2"],
        *["-c:v", "mpeg4", str(source)],
    )

    _run_task(env, await _import(env, route, source))

    _assert_failed_and_removed(env, NoAudioStreamError.detail)


@pytest.mark.anyio
@needs_ffmpeg
@pytest.mark.parametrize(
    ("fixture", "detail"),
    [
        # An audio track with no packets: its DURATION tag is zero and a
        # decode yields 0 s, while the container still says 3 s.
        (
            [
                *["-f", "lavfi", "-i", "testsrc=size=64x64:rate=25:duration=3"],
                *["-ss", "30", "-f", "lavfi", "-i", "sine=frequency=440:duration=1"],
                *["-map", "0:v", "-map", "1:a", "-c:v", "mpeg4", "-c:a", "aac"],
                "empty.mkv",
            ],
            NoAudioStreamError.detail,
        ),
        # MPEG-PS whose first audio packet lies past ffprobe's default probe
        # window: the stream reports 0 channels and extraction would fail.
        (
            [
                *["-f", "lavfi", "-i", "testsrc=size=64x64:rate=25:duration=8"],
                *["-itsoffset", "6", "-f", "lavfi", "-i", "sine=duration=2"],
                *["-c:v", "mpeg2video", "-c:a", "mp2"],
                "late.mpg",
            ],
            UnreadableAudioStreamError.detail,
        ),
    ],
    ids=["empty-track", "late-audio"],
)
async def test_audio_import_cannot_use_fails_the_import_and_leaves_no_file(
    env: _Env, fixture: list[str], detail: str
) -> None:
    source = env.tmp / fixture[-1]
    _ffmpeg(*fixture[:-1], str(source))

    _run_task(env, await _import(env, "import", source))

    _assert_failed_and_removed(env, detail)


@pytest.mark.anyio
@needs_ffmpeg
async def test_a_failed_extraction_fails_the_import_and_leaves_no_file(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The upload is not kept in place of the audio ffmpeg could not write."""
    source = env.tmp / "screen.mkv"
    _screen_recording(source, ["-c:a", "aac"])
    recording_id = await _import(env, "import", source)
    monkeypatch.setattr(
        import_audio,
        "_output_plan",
        lambda track: import_audio._OutputPlan(".m4a", ["-c:a", "no_such_encoder"]),
    )

    _run_task(env, recording_id)

    _assert_failed_and_removed(env, AudioExtractionError.detail)


def _limit_ffmpeg_output(monkeypatch: pytest.MonkeyPatch, max_bytes: int) -> None:
    """Run the extraction's ffmpeg under a file-size limit, as a full disk quota
    would stop it (SIGXFSZ)."""
    real_run = subprocess.run

    def limit() -> None:
        resource.setrlimit(resource.RLIMIT_FSIZE, (max_bytes, max_bytes))

    def run(cmd, **kwargs):
        if cmd[0] == "ffmpeg":
            kwargs["preexec_fn"] = limit
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(import_audio.subprocess, "run", run)


@pytest.mark.anyio
@needs_ffmpeg
@pytest.mark.parametrize("route", ["import", "chunked"])
async def test_a_server_fault_fails_the_import_without_a_retry(
    env: _Env, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    """A full disk is not the file's fault: the recording says the server
    failed, not that the file should be converted. Like a processing fault
    that is not a network error, it is not retried; nothing of the upload is
    kept."""
    source = env.tmp / "screen.mkv"
    _screen_recording(source, ["-c:a", "aac"])
    recording_id = await _import(env, route, source)
    _limit_ffmpeg_output(monkeypatch, 1_000)

    _run_task(env, recording_id)

    _assert_failed_and_removed(env, SERVER_FAILURE_DETAIL)


def _fake_keep(calls: list[str], after=None):
    """Stand in for ``keep_imported_audio``: write ``<stem>.m4a``, delete the
    upload, then run ``after``."""

    def keep(source: str) -> KeptAudio:
        calls.append(source)
        kept = Path(source).with_suffix(".m4a")
        kept.write_bytes(b"kept audio")
        os.remove(source)
        if after is not None:
            after()
        return KeptAudio(str(kept))

    return keep


@pytest.mark.anyio
async def test_a_recording_deleted_while_its_audio_is_kept_is_not_recreated(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The task removes the audio it kept and writes nothing: no row comes
    back, and nothing is queued."""
    source = env.tmp / "screen.mkv"
    source.write_bytes(b"video")
    recording_id = await _import(env, "upload", source)

    def delete_the_recording() -> None:
        with env.engine.begin() as connection:
            connection.execute(text("DELETE FROM recordings"))

    calls: list[str] = []
    monkeypatch.setattr(
        imported_audio, "keep_imported_audio", _fake_keep(calls, delete_the_recording)
    )

    _run_task(env, recording_id)

    assert len(calls) == 1
    with env.engine.connect() as connection:
        count = connection.execute(text("SELECT COUNT(*) FROM recordings"))
        assert count.scalar_one() == 0
    assert _files(env.recordings) == []
    assert _dispatched_names(env) == [KEEP_TASK]


@pytest.mark.anyio
async def test_a_redelivered_task_changes_nothing(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once the audio is kept, the recording no longer waits for the task: a
    second delivery neither extracts again nor queues processing twice."""
    source = env.tmp / "screen.mkv"
    source.write_bytes(b"video")
    recording_id = await _import(env, "upload", source)
    calls: list[str] = []
    monkeypatch.setattr(imported_audio, "keep_imported_audio", _fake_keep(calls))

    _run_task(env, recording_id)
    kept = _recording(env)
    _run_task(env, recording_id)

    assert len(calls) == 1
    assert _recording(env) == kept
    assert kept["audio_path"].endswith(".m4a")
    assert _dispatched_names(env) == [KEEP_TASK, PROCESS_TASK, PROXY_TASK]


def _waiting_import(env: _Env, source: Path) -> int:
    """A recording waiting for its audio, as the import routes leave it."""
    upload = env.recordings / f"{uuid.uuid4()}{source.suffix}"
    shutil.copyfile(source, upload)
    with Session(env.engine) as session:
        recording = Recording(
            name="screen",
            audio_path=str(upload),
            status=RecordingStatus.QUEUED,
            processing_step=KEEPING_AUDIO_STEP,
            user_id=1,
        )
        session.add(recording)
        session.commit()
        return recording.id


def _gate_extraction(monkeypatch: pytest.MonkeyPatch, before: bool) -> dict:
    """Hold each copy of the task, named by its thread, at its extraction:
    ``before`` it starts, or once it has returned. Returns, per copy, the
    event it sets on arriving and the one it waits for."""
    gates = {name: (threading.Event(), threading.Event()) for name in ("A", "B")}
    real_keep = imported_audio.keep_imported_audio

    def gated(source: str) -> KeptAudio:
        arrived, go = gates[threading.current_thread().name]
        if before:
            arrived.set()
            assert go.wait(30)
            return real_keep(source)
        kept = real_keep(source)
        arrived.set()
        assert go.wait(30)
        return kept

    monkeypatch.setattr(imported_audio, "keep_imported_audio", gated)
    return gates


def _copy(env: _Env, recording_id: int, name: str) -> threading.Thread:
    thread = threading.Thread(
        target=_run_task, args=(env, recording_id), name=name, daemon=True
    )
    thread.start()
    return thread


@pytest.mark.anyio
@needs_ffmpeg
@pytest.mark.parametrize(
    ("first", "second", "second_extracts_after_the_first_stored"),
    [("A", "B", False), ("B", "A", False), ("A", "B", True)],
    ids=["A-stores-first", "B-stores-first", "B-starts-after-A-stored"],
)
async def test_two_copies_of_the_task_keep_one_audio_and_the_import(
    any_env: _Env,
    monkeypatch: pytest.MonkeyPatch,
    first: str,
    second: str,
    second_extracts_after_the_first_stored: bool,
) -> None:
    """Both copies pass the waiting check. Whichever stores first keeps its
    audio and deletes the upload; the other removes only what it extracted,
    even when it extracts after the upload is gone."""
    env = any_env
    source = env.tmp / "screen.mkv"
    _screen_recording(source, ["-c:a", "aac"])
    recording_id = _waiting_import(env, source)
    upload = _recording(env)["audio_path"]
    if second_extracts_after_the_first_stored:
        gates = _gate_extraction(monkeypatch, before=True)
        threads = {name: _copy(env, recording_id, name) for name in ("A", "B")}
        assert gates[first][0].wait(30) and gates[second][0].wait(30)
        gates[first][1].set()
        threads[first].join(30)
        gates[second][1].set()
        threads[second].join(30)
    else:
        gates = _gate_extraction(monkeypatch, before=False)
        threads = {name: _copy(env, recording_id, name) for name in ("A", "B")}
        assert gates["A"][0].wait(30) and gates["B"][0].wait(30)
        for name in (first, second):
            gates[name][1].set()
            threads[name].join(30)

    row = _recording(env)
    assert (row["status"], row["processing_step"]) == ("QUEUED", None)
    assert row["audio_path"].endswith(".m4a")
    assert _files(env.recordings) == [Path(row["audio_path"])]
    assert not Path(upload).exists()
    assert _dispatched_names(env) == [PROCESS_TASK, PROXY_TASK]


@pytest.mark.anyio
@needs_ffmpeg
async def test_a_fault_after_extraction_leaves_the_upload_for_the_next_copy(
    any_env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before the kept audio is stored, the upload is all there is: a fault
    there removes the extracted file only, and the import waits on."""
    env = any_env
    source = env.tmp / "screen.mkv"
    _screen_recording(source, ["-c:a", "aac"])
    recording_id = _waiting_import(env, source)
    before = _recording(env)

    def fail(*args, **kwargs) -> None:
        raise RuntimeError("connection lost")

    rebuild = imported_audio._rebuild_import_window
    monkeypatch.setattr(imported_audio, "_rebuild_import_window", fail)
    with pytest.raises(RuntimeError, match="connection lost"):
        _run_task(env, recording_id)

    assert _recording(env) == before
    assert _files(env.recordings) == [Path(before["audio_path"])]
    assert env.dispatched == []

    monkeypatch.setattr(imported_audio, "_rebuild_import_window", rebuild)
    _run_task(env, recording_id)

    row = _recording(env)
    assert (row["status"], row["processing_step"]) == ("QUEUED", None)
    assert _files(env.recordings) == [Path(row["audio_path"])]
