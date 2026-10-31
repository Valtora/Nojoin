"""A transcode queued or running when its capture is discarded (#869).

Discard deletes the recording and then its folder. A transcode that runs after
it, or fails because of it, must not write the folder back or leave a
``.transcode_failed`` marker in it, and stops at INFO. Both go through the real
Discard endpoint.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import anyio
import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from backend.tests.test_recording_pause_resume import DOCUMENTS_SCHEMA
from backend.tests.test_recording_segment_transcode import (
    TRUSTED_ORIGIN,
    _lookup_internal_recording_id,
    _recording_temp_dir,
    set_session_cookie,
)
from backend.tests.test_recording_segment_transcode import api_app as api_app
from backend.tests.test_recording_segment_transcode import client as client
from backend.tests.test_recording_segment_transcode import (
    live_dispatches as live_dispatches,
)
from backend.tests.test_recording_segment_transcode import (
    pipeline_metrics as pipeline_metrics,
)
from backend.tests.test_recording_segment_transcode import sqlite_urls as sqlite_urls
from backend.tests.test_recording_segment_transcode import sync_engine as sync_engine
from backend.tests.test_recording_segment_transcode import (
    test_session_maker as test_session_maker,
)
from backend.tests.test_recording_segment_transcode import (
    transcode_dispatches as transcode_dispatches,
)


@dataclass
class Capture:
    client: AsyncClient
    public_id: str
    recording_id: int
    upload_dir: Path
    live_dispatches: list
    pipeline_metrics: list

    async def discard(self) -> None:
        response = await self.client.post(
            f"/api/v1/recordings/{self.public_id}/discard",
            headers={"Origin": TRUSTED_ORIGIN},
        )
        assert response.status_code == 200, response.text


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def staged(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    sync_engine,
    live_dispatches,
    pipeline_metrics,
) -> Capture:
    """A capture whose one segment is staged as WebM, waiting for its transcode."""
    with sync_engine.begin() as connection:
        connection.execute(text(DOCUMENTS_SCHEMA))
    set_session_cookie(client)
    init = await client.post(
        "/api/v1/recordings/init",
        params={"name": "Example capture"},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert init.status_code == 200
    public_id = init.json()["id"]
    recording_id = await _lookup_internal_recording_id(
        test_session_maker, public_id=public_id
    )
    upload = await client.post(
        f"/api/v1/recordings/{public_id}/segment",
        params={"sequence": 0},
        headers={"Origin": TRUSTED_ORIGIN},
        files={"file": ("0.webm", b"webm-opus-segment", "audio/webm")},
    )
    assert upload.status_code == 200
    database = Path(str(sync_engine.url.database))
    upload_dir = _recording_temp_dir(database.parent, recording_id, create=False)
    assert (upload_dir / "0.webm").exists()
    return Capture(
        client, public_id, recording_id, upload_dir, live_dispatches, pipeline_metrics
    )


def _loud(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.levelno >= logging.WARNING]


@pytest.mark.anyio
async def test_a_transcode_queued_before_a_discard_leaves_no_folder(
    staged: Capture, caplog: pytest.LogCaptureFixture
) -> None:
    from backend.processing import segment_transcode

    await staged.discard()
    assert not staged.upload_dir.exists()
    caplog.set_level(logging.INFO)

    result = segment_transcode.transcode_segment_task.run(staged.recording_id, 0)

    assert result == {"status": "skipped", "segment": 0}
    assert not staged.upload_dir.exists()
    assert staged.live_dispatches == []
    assert staged.pipeline_metrics == []
    assert _loud(caplog) == []
    assert any(
        "the recording was discarded" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.anyio
async def test_a_transcode_failing_under_a_discard_writes_no_marker(
    staged: Capture,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The Discard lands while ffmpeg runs, and ffmpeg fails as its input goes."""
    from backend.processing import segment_transcode

    started, discarded = threading.Event(), threading.Event()

    def ffmpeg(input_path, output_path):
        started.set()
        assert discarded.wait(10)
        raise RuntimeError(f"{input_path}: No such file or directory")

    monkeypatch.setattr(segment_transcode, "_run_ffmpeg_transcode", ffmpeg)
    caplog.set_level(logging.INFO)
    results: list[dict] = []

    async def transcode() -> None:
        results.append(
            await anyio.to_thread.run_sync(
                segment_transcode.transcode_segment_task.run, staged.recording_id, 0
            )
        )

    async with anyio.create_task_group() as group:
        group.start_soon(transcode)
        assert await anyio.to_thread.run_sync(started.wait, 10)
        await staged.discard()
        discarded.set()

    assert results == [{"status": "skipped", "segment": 0}]
    assert not staged.upload_dir.exists()
    assert staged.live_dispatches == []
    assert staged.pipeline_metrics == []
    assert _loud(caplog) == []


@pytest.mark.anyio
async def test_a_transcode_failing_while_the_capture_exists_still_marks_it(
    staged: Capture,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from backend.processing import segment_transcode

    def ffmpeg(input_path, output_path):
        raise RuntimeError("broken container")

    monkeypatch.setattr(segment_transcode, "_run_ffmpeg_transcode", ffmpeg)
    caplog.set_level(logging.INFO)

    result = segment_transcode.transcode_segment_task.run(staged.recording_id, 0)

    assert result == {"status": "failed", "segment": 0}
    assert (staged.upload_dir / "0.transcode_failed").exists()
    assert [metric["stage"] for metric in staged.pipeline_metrics] == [
        "segment_transcode_failed"
    ]
    assert any(
        record.getMessage().startswith("Segment transcode failed")
        for record in _loud(caplog)
    )
