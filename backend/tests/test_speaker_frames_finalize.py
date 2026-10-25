# ruff: noqa: F811 -- the API fixtures are imported from test_recording_segment_transcode and requested by name
"""Finalize starts a screen study's 14 days and closes its upload window (#653).

Through the real init, segment and finalize routes. The frame is drawn by the
test.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile
from httpx import AsyncClient
from sqlalchemy.orm import sessionmaker

from backend.tests.speaker_frame_images import draw_frame
from backend.tests.test_recording_segment_transcode import (  # noqa: F401 -- fixtures
    TRUSTED_ORIGIN,
    api_app,
    client,
    live_dispatches,
    pipeline_metrics,
    set_session_cookie,
    sqlite_urls,
    sync_engine,
    test_session_maker,
    transcode_dispatches,
)
from backend.utils import speaker_frames
from backend.utils.speaker_frames import storage


@pytest.fixture
def frames_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(
        storage, "path_manager", SimpleNamespace(user_data_directory=root)
    )
    storage._summaries.clear()
    storage._total_cache.clear()
    yield root
    storage._summaries.clear()
    storage._total_cache.clear()


def _flac_segment() -> bytes:
    rng = np.random.default_rng(0)
    pcm = rng.integers(-3000, 3000, size=(32_000, 2), dtype=np.int16)
    buffer = io.BytesIO()
    soundfile.write(buffer, pcm, 16_000, format="FLAC", subtype="PCM_16")
    return buffer.getvalue()


async def _capture_one_segment(client: AsyncClient) -> str:
    set_session_cookie(client)
    init = await client.post(
        "/api/v1/recordings/init",
        params={"name": "Study meeting"},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert init.status_code == 200
    public_id = init.json()["id"]
    upload = await client.post(
        f"/api/v1/recordings/{public_id}/segment",
        params={"sequence": 0},
        headers={"Origin": TRUSTED_ORIGIN},
        files={"file": ("0.flac", _flac_segment(), "audio/flac")},
    )
    assert upload.status_code == 200
    return public_id


async def _finalize(client: AsyncClient, public_id: str) -> None:
    response = await client.post(
        f"/api/v1/recordings/{public_id}/finalize", headers={"Origin": TRUSTED_ORIGIN}
    )
    assert response.status_code == 200, response.text


@pytest.mark.anyio
async def test_finalize_records_when_a_study_recording_stopped(
    client: AsyncClient, test_session_maker: sessionmaker, frames_data_dir: Path
) -> None:
    public_id = await _capture_one_segment(client)
    speaker_frames.store_frame(
        public_id,
        sequence=0,
        offset_ms=500,
        data=draw_frame(),
        received_at=datetime.now(UTC),
    )
    before = datetime.now(UTC)

    await _finalize(client, public_id)

    stopped = speaker_frames.recording_stopped_at(public_id)
    assert stopped is not None
    assert before - timedelta(seconds=1) <= stopped <= datetime.now(UTC)
    assert speaker_frames.delete_after(public_id) == stopped + timedelta(days=14)
    assert speaker_frames.accepts_frames_after_stop(
        public_id, now=stopped + timedelta(seconds=20)
    )


@pytest.mark.anyio
async def test_finalize_writes_nothing_for_a_recording_without_frames(
    client: AsyncClient, test_session_maker: sessionmaker, frames_data_dir: Path
) -> None:
    public_id = await _capture_one_segment(client)

    await _finalize(client, public_id)

    assert speaker_frames.recording_stopped_at(public_id) is None
    assert not (frames_data_dir / "speaker_frames").exists()
    assert not (frames_data_dir / "speaker_frame_state").exists()
