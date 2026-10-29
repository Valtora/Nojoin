"""The live read says when the live lane is behind a running capture (issue #780).

Windows are built from the uploaded chunks by the capture's own window builder
and marked processed by the live lane's own marker, as a capture's are.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlmodel import select

from backend.models.pipeline import RecordingAudioWindowManifest
from backend.tests.test_live_speaker_naming_api import REC, _seed
from backend.tests.test_live_speaker_naming_api import client as client
from backend.tests.test_live_speaker_naming_api import session_maker as session_maker
from backend.utils.audio_windows import (
    WINDOW_STATUS_LIVE_PROCESSED,
    apply_audio_window_specs,
    build_audio_window_specs,
    mark_audio_windows_processed,
)
from backend.utils.config_manager import config_manager

pytestmark = pytest.mark.anyio

CHUNK_MS = 2_000


def _upload(sync_session: Any, chunks: int) -> None:
    """Windows for ``chunks`` two-second uploads, none read by the live lane."""
    rows = [
        SimpleNamespace(
            sequence_no=n,
            source_kind="browser",
            absolute_start_ms=n * CHUNK_MS,
            absolute_end_ms=(n + 1) * CHUNK_MS,
        )
        for n in range(chunks)
    ]
    existing = (
        sync_session.execute(select(RecordingAudioWindowManifest)).scalars().all()
    )
    for row in apply_audio_window_specs(
        recording_id=1,
        existing_rows=existing,
        window_specs=build_audio_window_specs(rows),
    ):
        sync_session.add(row)
    sync_session.commit()


def _live_lane_reads(sync_session: Any, up_to_sequence: int) -> None:
    rows = sync_session.execute(select(RecordingAudioWindowManifest)).scalars().all()
    for row in mark_audio_windows_processed(
        rows, up_to_sequence=up_to_sequence, status=WINDOW_STATUS_LIVE_PROCESSED
    ):
        sync_session.add(row)
    sync_session.commit()


def _config(monkeypatch: pytest.MonkeyPatch, *, live: bool, canonical: bool) -> None:
    values = {
        "enable_live_transcription": live,
        "enable_canonical_transcript_writes": canonical,
    }
    read = config_manager.get
    monkeypatch.setattr(
        config_manager,
        "get",
        lambda key, default=None: values[key] if key in values else read(key, default),
    )


async def _waiting(client: Any) -> bool:
    response = await client.get(f"/api/v1/transcripts/{REC}/utterances")
    assert response.status_code == 200, response.text
    return response.json().get("live_waiting", False)


@pytest.mark.parametrize("canonical", [True, False])
async def test_uploads_the_lane_has_not_read_are_reported_until_it_reads_them(
    client: Any, session_maker: Any, monkeypatch: pytest.MonkeyPatch, canonical: bool
) -> None:
    _config(monkeypatch, live=True, canonical=canonical)
    await _seed(session_maker)
    async with session_maker() as session:
        # 50 s uploaded; the lane read the first 20 s before the worker got busy.
        await session.run_sync(lambda sync: _upload(sync, 25))
        await session.run_sync(lambda sync: _live_lane_reads(sync, 9))

    assert await _waiting(client) is True

    async with session_maker() as session:
        await session.run_sync(lambda sync: _live_lane_reads(sync, 24))

    assert await _waiting(client) is False


async def test_a_lane_keeping_up_is_not_reported(
    client: Any, session_maker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config(monkeypatch, live=True, canonical=True)
    await _seed(session_maker)
    async with session_maker() as session:
        await session.run_sync(lambda sync: _upload(sync, 14))
        await session.run_sync(lambda sync: _live_lane_reads(sync, 13))
        # One more upload, not yet read: the ordinary lag behind a capture.
        await session.run_sync(lambda sync: _upload(sync, 15))

    assert await _waiting(client) is False


@pytest.mark.parametrize(
    ("status", "live"),
    [("PAUSED", True), ("PROCESSING", True), ("UPLOADING", False)],
)
async def test_paused_stopped_or_live_off_captures_are_never_reported(
    client: Any,
    session_maker: Any,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    live: bool,
) -> None:
    _config(monkeypatch, live=live, canonical=True)
    await _seed(session_maker, status=status)
    async with session_maker() as session:
        await session.run_sync(lambda sync: _upload(sync, 15))

    assert await _waiting(client) is False
