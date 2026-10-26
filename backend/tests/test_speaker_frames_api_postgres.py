"""The screen study's endpoints and the delete paths that take its frames (#653).

Postgres, like the document-file tests, because two of the delete paths delete
with Core statements and rely on the foreign keys' ON DELETE CASCADE, which the
SQLite suites do not enforce. Every frame is drawn by the test.

Skips unless NOJOIN_TEST_POSTGRES_URL names a server. CI sets it for the backend
suite.
"""

from __future__ import annotations

import importlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlmodel import Session

from backend.api.deps import get_current_recording_client_user, get_current_user, get_db
from backend.api.v1.api import api_router
from backend.api.v1.endpoints.recordings import routes_speaker_frames
from backend.core.db import driver_urls
from backend.models.recording import Recording, RecordingStatus
from backend.models.user import User, UserRole
from backend.tests.postgres_setup import fresh_schema
from backend.tests.speaker_frame_images import draw_frame, store_test_frame, timing
from backend.utils import rate_limit, speaker_frames
from backend.utils.config_manager import ConfigManager
from backend.utils.speaker_frames import settings as study_settings
from backend.utils.speaker_frames import storage

SCHEMA = "speaker_frames_api_test"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _add_users(engine) -> dict[str, int]:
    """The primary Owner first, so it holds the lowest id, then a second Owner."""
    with Session(engine) as session:
        owner = User(username="owner", hashed_password="x", role=UserRole.OWNER)
        session.add(owner)
        session.commit()
        second = User(username="second", hashed_password="x", role=UserRole.OWNER)
        member = User(username="member", hashed_password="x", role=UserRole.USER)
        session.add_all([second, member])
        session.commit()
        return {"owner": owner.id, "second": second.id, "member": member.id}


def _reset_caches() -> None:
    storage._reset_caches()
    rate_limit._fallback_windows.clear()
    rate_limit._fallback_concurrency.clear()


@pytest.fixture
def harness(postgres_test_url, tmp_path, monkeypatch, stub_celery_dispatch):
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )
    from sqlalchemy.pool import NullPool

    importlib.import_module("backend.models.registry")
    async_url, sync_url = driver_urls(postgres_test_url)
    sync_engine = create_engine(
        sync_url, connect_args={"options": f"-csearch_path={SCHEMA},public"}
    )
    with sync_engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    fresh_schema(sync_engine, SCHEMA)
    async_engine = create_async_engine(
        async_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
    )
    maker = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )

    data_dir = tmp_path / "data"
    recordings_dir = data_dir / "recordings"
    documents_dir = data_dir / "documents"
    recordings_dir.mkdir(parents=True)
    documents_dir.mkdir(parents=True)
    monkeypatch.setenv("RECORDINGS_DIR", str(recordings_dir))
    monkeypatch.setenv("DOCUMENTS_DIR", str(documents_dir))
    monkeypatch.setattr(
        storage, "path_manager", SimpleNamespace(user_data_directory=data_dir)
    )
    config_path = tmp_path / "config.json"

    def set_config(values: dict) -> None:
        config_path.write_text(json.dumps(values), encoding="utf-8")
        monkeypatch.setattr(
            study_settings,
            "config_manager",
            ConfigManager(config_path=str(config_path)),
        )

    set_config({"speaker_frame_sampling": "owner"})

    async def _no_redis():
        return None

    monkeypatch.setattr(rate_limit, "_get_redis", _no_redis)
    _reset_caches()

    users = _add_users(sync_engine)

    acting = SimpleNamespace(user=None)

    async def override_db():
        async with maker() as session:
            yield session

    app = FastAPI()
    app.include_router(api_router)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: acting.user
    app.dependency_overrides[get_current_recording_client_user] = lambda: acting.user

    def act_as(name: str) -> None:
        with Session(sync_engine) as session:
            acting.user = session.get(User, users[name])

    act_as("owner")
    try:
        yield SimpleNamespace(
            app=app,
            engine=sync_engine,
            users=users,
            act_as=act_as,
            set_config=set_config,
            data_dir=data_dir,
            recordings_dir=recordings_dir,
        )
    finally:
        _reset_caches()
        sync_engine.dispose()


async def _request(harness, method: str, url: str, **kwargs):
    transport = ASGITransport(app=harness.app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.request(method, url, **kwargs)


def _add_recording(
    harness,
    *,
    owner: str = "owner",
    status: RecordingStatus = RecordingStatus.UPLOADING,
    is_deleted: bool = False,
) -> str:
    audio = harness.recordings_dir / f"{uuid.uuid4()}.wav"
    audio.write_bytes(b"RIFF")
    with Session(harness.engine) as session:
        recording = Recording(
            name="Study meeting",
            audio_path=str(audio),
            status=status,
            is_deleted=is_deleted,
            user_id=harness.users[owner],
        )
        session.add(recording)
        session.commit()
        return recording.public_id


async def _upload(
    harness, public_id: str, *, sequence=0, offset_ms=0, body=None, **kwargs
):
    content_type = kwargs.pop("content_type", "image/jpeg")
    params = {
        "sequence": sequence,
        "offset_ms": offset_ms,
        "tick": sequence,
        "lost_before": 0,
        "frame_ts_us": 1_000_000 * (sequence + 1),
        "grab_delay_ms": 15,
        **kwargs.pop("params", {}),
    }
    return await _request(
        harness,
        "POST",
        f"/recordings/{public_id}/speaker-frames",
        params={key: value for key, value in params.items() if value is not None},
        files={"file": ("frame.jpg", body or draw_frame(), content_type)},
    )


def _frames_dir(harness, public_id: str) -> Path:
    return harness.data_dir / "speaker_frames" / public_id


def _seed_frames(harness, public_id: str, count: int = 2) -> None:
    for sequence in range(count):
        store_test_frame(
            public_id,
            sequence=sequence,
            offset_ms=0,
            data=draw_frame(highlight=sequence),
            received_at=datetime.now(UTC),
            timing=timing(sequence),
        )


# --- the gate ----------------------------------------------------------------


@pytest.mark.anyio
async def test_every_route_is_404_while_the_study_is_off(harness) -> None:
    public_id = _add_recording(harness)
    harness.set_config({"speaker_frame_sampling": "off"})

    responses = [
        await _request(harness, "GET", "/recordings/speaker-frames/availability"),
        await _upload(harness, public_id),
        await _request(harness, "GET", f"/recordings/{public_id}/speaker-frames"),
        await _request(harness, "DELETE", f"/recordings/{public_id}/speaker-frames"),
    ]

    assert [response.status_code for response in responses] == [404] * 4
    assert not _frames_dir(harness, public_id).exists()


@pytest.mark.anyio
async def test_the_primary_owner_sees_the_study_and_its_rate(harness) -> None:
    harness.set_config({"speaker_frame_sampling": "owner", "speaker_frame_rate_hz": 2})
    response = await _request(harness, "GET", "/recordings/speaker-frames/availability")
    assert response.status_code == 200
    assert response.json() == {
        "rate_hz": 2,
        "retention_days": 14,
        "max_frame_bytes": 1024 * 1024,
    }


@pytest.mark.anyio
@pytest.mark.parametrize("who", ["second", "member"])
async def test_nobody_but_the_primary_owner_may_use_it(harness, who: str) -> None:
    public_id = _add_recording(harness, owner=who)
    harness.act_as(who)

    assert (
        await _request(harness, "GET", "/recordings/speaker-frames/availability")
    ).status_code == 404
    assert (await _upload(harness, public_id)).status_code == 404
    assert not _frames_dir(harness, public_id).exists()


@pytest.mark.anyio
async def test_the_owner_cannot_add_frames_to_anyone_elses_recording(harness) -> None:
    public_id = _add_recording(harness, owner="member")
    assert (await _upload(harness, public_id)).status_code == 404
    assert (
        await _request(harness, "GET", f"/recordings/{public_id}/speaker-frames")
    ).status_code == 404
    assert (
        await _request(harness, "DELETE", f"/recordings/{public_id}/speaker-frames")
    ).status_code == 404
    assert not _frames_dir(harness, public_id).exists()


# --- uploads ----------------------------------------------------------------


@pytest.mark.anyio
async def test_a_frame_is_stored_and_the_totals_come_back(harness) -> None:
    public_id = _add_recording(harness)
    body = draw_frame()

    response = await _upload(harness, public_id, sequence=4, offset_ms=1000, body=body)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "stored"
    assert payload["frames"] == 1 and payload["bytes"] == len(body)
    assert payload["recording_stopped"] is False
    deadline = datetime.fromisoformat(payload["delete_after"].replace("Z", "+00:00"))
    assert abs(deadline - (datetime.now(UTC) + timedelta(days=14))) < timedelta(
        minutes=1
    )
    assert (_frames_dir(harness, public_id) / "4-1000.jpg").read_bytes() == body

    again = await _upload(harness, public_id, sequence=4, offset_ms=1000, body=body)
    assert again.json()["status"] == "duplicate" and again.json()["frames"] == 1


@pytest.mark.anyio
async def test_a_paused_capture_still_takes_its_queued_frames(harness) -> None:
    public_id = _add_recording(harness, status=RecordingStatus.PAUSED)
    assert (await _upload(harness, public_id)).status_code == 200


@pytest.mark.anyio
async def test_only_a_jpeg_is_taken(harness) -> None:
    public_id = _add_recording(harness)
    assert (
        await _upload(harness, public_id, content_type="image/png")
    ).status_code == 415
    response = await _upload(harness, public_id, body=b"<html>hello</html>")
    assert response.status_code == 422
    assert not any(_frames_dir(harness, public_id).glob("*.jpg"))


@pytest.mark.anyio
async def test_a_frame_over_1_mb_is_refused(harness) -> None:
    public_id = _add_recording(harness)
    body = b"\xff\xd8\xff" + bytes(speaker_frames.MAX_FRAME_BYTES)
    assert (await _upload(harness, public_id, body=body)).status_code == 413
    assert not _frames_dir(harness, public_id).exists()


@pytest.mark.anyio
async def test_an_offset_past_the_chunk_is_refused(harness) -> None:
    public_id = _add_recording(harness)
    assert (await _upload(harness, public_id, offset_ms=10_001)).status_code == 422
    assert (await _upload(harness, public_id, sequence=-1)).status_code == 422


@pytest.mark.anyio
async def test_frames_have_their_own_upload_limit_apart_from_the_audio(harness) -> None:
    public_id = _add_recording(harness)
    owner = str(harness.users["owner"])
    segments = f"{rate_limit.RATE_LIMIT_KEY_PREFIX}:concurrency:upload_segment:{owner}"
    frames = (
        f"{rate_limit.RATE_LIMIT_KEY_PREFIX}:concurrency:upload_speaker_frame:{owner}"
    )

    # Audio uploads at their limit hold no frame back.
    rate_limit._fallback_concurrency[segments] = 5
    assert (await _upload(harness, public_id)).status_code == 200

    # Frames at their own limit are refused, and audio is not counted against it.
    rate_limit._fallback_concurrency[frames] = (
        routes_speaker_frames.SPEAKER_FRAME_UPLOAD_CONCURRENCY
    )
    assert (await _upload(harness, public_id, sequence=1)).status_code == 429
    assert rate_limit._fallback_concurrency[segments] == 5


@pytest.mark.anyio
async def test_frames_are_rate_limited_per_minute(harness, monkeypatch) -> None:
    public_id = _add_recording(harness)
    monkeypatch.setattr(routes_speaker_frames, "SPEAKER_FRAME_RATE_LIMIT_PER_MINUTE", 3)
    codes = [
        (await _upload(harness, public_id, sequence=n)).status_code for n in range(4)
    ]
    assert codes == [200, 200, 200, 429]


@pytest.mark.anyio
async def test_the_cap_refuses_new_frames(harness, monkeypatch) -> None:
    public_id = _add_recording(harness)
    first = draw_frame()
    assert (await _upload(harness, public_id, body=first)).status_code == 200
    monkeypatch.setattr(storage, "TOTAL_CAP_BYTES", len(first) + 10)
    storage._reset_caches()

    response = await _upload(harness, public_id, sequence=1)

    assert response.status_code == 507
    assert "20 GB" in response.json()["detail"]
    assert len(list(_frames_dir(harness, public_id).glob("*.jpg"))) == 1


# --- the window after Stop --------------------------------------------------


@pytest.mark.anyio
async def test_frames_still_land_for_a_minute_after_the_recording_stops(
    harness, monkeypatch
) -> None:
    public_id = _add_recording(harness)
    assert (await _upload(harness, public_id)).status_code == 200
    stopped = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
    with Session(harness.engine) as session:
        session.execute(
            text("UPDATE recordings SET status = 'QUEUED' WHERE public_id = :id"),
            {"id": public_id},
        )
        session.commit()
    speaker_frames.mark_recording_stopped(public_id, stopped)

    monkeypatch.setattr(
        routes_speaker_frames, "_now", lambda: stopped + timedelta(seconds=30)
    )
    inside = await _upload(harness, public_id, sequence=1)
    monkeypatch.setattr(
        routes_speaker_frames, "_now", lambda: stopped + timedelta(seconds=90)
    )
    outside = await _upload(harness, public_id, sequence=2)

    assert inside.status_code == 200
    assert inside.json()["recording_stopped"] is True
    assert inside.json()["delete_after"] == "2026-11-08T12:00:00.000Z"
    assert outside.status_code == 409


@pytest.mark.anyio
async def test_a_finished_recording_with_no_stop_time_takes_no_frames(harness) -> None:
    public_id = _add_recording(harness, status=RecordingStatus.PROCESSED)
    assert (await _upload(harness, public_id)).status_code == 409


# --- status and deletion ----------------------------------------------------


@pytest.mark.anyio
async def test_the_status_reads_what_is_on_disk(harness) -> None:
    public_id = _add_recording(harness)
    _seed_frames(harness, public_id, count=3)
    response = await _request(harness, "GET", f"/recordings/{public_id}/speaker-frames")
    assert response.status_code == 200
    assert response.json()["frames"] == 3


@pytest.mark.anyio
async def test_delete_takes_this_meetings_frames_and_leaves_the_recording(
    harness,
) -> None:
    public_id = _add_recording(harness)
    other = _add_recording(harness)
    _seed_frames(harness, public_id)
    _seed_frames(harness, other)

    response = await _request(
        harness, "DELETE", f"/recordings/{public_id}/speaker-frames"
    )

    assert response.status_code == 200 and response.json() == {"deleted": 2}
    assert not _frames_dir(harness, public_id).exists()
    assert _frames_dir(harness, other).is_dir()
    with Session(harness.engine) as session:
        remaining = session.execute(
            text("SELECT count(*) FROM recordings WHERE public_id = :id"),
            {"id": public_id},
        ).scalar_one()
    assert remaining == 1
    # The capture goes on, but takes no frame again: one in flight is refused.
    late = await _upload(harness, public_id, sequence=9)
    assert late.status_code == 409
    assert "were deleted" in late.json()["detail"]
    assert not _frames_dir(harness, public_id).exists()


# Each route that removes a recording, or moves it to the bin, with what it needs.
DELETE_ROUTES = {
    "discard": (
        RecordingStatus.UPLOADING,
        False,
        "POST",
        "/recordings/{id}/discard",
        None,
    ),
    "delete": (RecordingStatus.PROCESSED, False, "DELETE", "/recordings/{id}", None),
    "soft_delete": (
        RecordingStatus.PROCESSED,
        False,
        "POST",
        "/recordings/{id}/soft-delete",
        None,
    ),
    "batch_soft_delete": (
        RecordingStatus.PROCESSED,
        False,
        "POST",
        "/recordings/batch/soft-delete",
        "batch",
    ),
    "permanent": (
        RecordingStatus.PROCESSED,
        True,
        "DELETE",
        "/recordings/{id}/permanent",
        None,
    ),
    "batch_permanent": (
        RecordingStatus.PROCESSED,
        True,
        "POST",
        "/recordings/batch/permanent",
        "batch",
    ),
}


@pytest.mark.anyio
@pytest.mark.parametrize("route", sorted(DELETE_ROUTES))
@pytest.mark.parametrize("study", ["owner", "off"])
async def test_every_delete_path_takes_the_frames_at_once(
    harness, route, study
) -> None:
    """Whatever the setting says: switching the study off keeps no frame."""
    status, is_deleted, method, url, body = DELETE_ROUTES[route]
    public_id = _add_recording(harness, status=status, is_deleted=is_deleted)
    bystander = _add_recording(harness)
    _seed_frames(harness, public_id)
    _seed_frames(harness, bystander)
    speaker_frames.mark_recording_stopped(public_id, datetime.now(UTC))
    harness.set_config({"speaker_frame_sampling": study})

    kwargs = {"json": {"recording_ids": [public_id]}} if body == "batch" else {}
    response = await _request(harness, method, url.format(id=public_id), **kwargs)

    assert response.status_code == 200, response.text
    assert not _frames_dir(harness, public_id).exists()
    assert not (harness.data_dir / "speaker_frame_state" / f"{public_id}.json").exists()
    assert _frames_dir(harness, bystander).is_dir()


@pytest.mark.anyio
async def test_deleting_the_owners_account_takes_the_frames(harness) -> None:
    public_id = _add_recording(harness, owner="owner")
    _seed_frames(harness, public_id)
    harness.act_as("second")

    response = await _request(harness, "DELETE", f"/users/{harness.users['owner']}")

    assert response.status_code == 200, response.text
    assert not _frames_dir(harness, public_id).exists()


# --- review of #722 ---------------------------------------------------------------


@pytest.mark.anyio
async def test_the_sampling_details_are_required_and_kept(harness) -> None:
    public_id = _add_recording(harness)
    missing = await _upload(harness, public_id, params={"tick": None})
    assert missing.status_code == 422

    response = await _upload(
        harness,
        public_id,
        sequence=3,
        params={
            "tick": 7,
            "lost_before": 2,
            "frame_ts_us": 98_765,
            "grab_delay_ms": 41,
        },
    )

    assert response.status_code == 200, response.text
    (line,) = (_frames_dir(harness, public_id) / "index.jsonl").read_text().splitlines()
    entry = json.loads(line)
    assert {
        key: entry[key]
        for key in ("tick", "lost_before", "frame_ts_us", "grab_delay_ms")
    } == {
        "tick": 7,
        "lost_before": 2,
        "frame_ts_us": 98_765,
        "grab_delay_ms": 41,
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    "params",
    [
        {"sequence": 10_000_001},
        {"tick": 100_000_001},
        {"lost_before": -1},
        {"grab_delay_ms": 3_600_001},
        {"frame_ts_us": 2**60},
    ],
)
async def test_out_of_range_numbers_are_a_422(harness, params) -> None:
    public_id = _add_recording(harness)
    sequence = params.pop("sequence", 0)
    response = await _upload(harness, public_id, sequence=sequence, params=params)
    assert response.status_code == 422


@pytest.mark.anyio
async def test_a_capture_in_the_bin_takes_no_frames(harness) -> None:
    public_id = _add_recording(
        harness, status=RecordingStatus.UPLOADING, is_deleted=True
    )
    assert (await _upload(harness, public_id)).status_code == 409
    assert not _frames_dir(harness, public_id).exists()


@pytest.mark.anyio
async def test_frames_are_refused_when_free_disk_space_is_short(
    harness, monkeypatch
) -> None:
    import shutil
    from collections import namedtuple

    public_id = _add_recording(harness)
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: usage(10, 9, 1024))

    response = await _upload(harness, public_id)

    assert response.status_code == 507
    assert "free disk space" in response.json()["detail"]
    assert not _frames_dir(harness, public_id).exists()


@pytest.mark.anyio
async def test_a_delete_that_leaves_frames_says_so(harness, monkeypatch) -> None:
    public_id = _add_recording(harness)
    _seed_frames(harness, public_id)

    def refuse(_path):
        raise OSError(39, "Directory not empty")

    monkeypatch.setattr(storage.shutil, "rmtree", refuse)
    response = await _request(
        harness, "DELETE", f"/recordings/{public_id}/speaker-frames"
    )

    assert response.status_code == 500
    assert _frames_dir(harness, public_id).is_dir()
