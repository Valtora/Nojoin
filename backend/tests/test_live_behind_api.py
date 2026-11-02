"""The live read says when the live lane is behind a running capture (issue #780).

Lag is the uploaded audio in chunks at or past the lane's ``next_expected``,
which the lane writes with ``write_live_state`` after every run, silent or
failed. Chunk rows are the ones the upload route registers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from backend.models.pipeline import RecordingAudioChunk
from backend.processing.live_state import write_live_state
from backend.tests.test_live_speaker_naming_api import REC, _seed
from backend.tests.test_live_speaker_naming_api import client as client
from backend.tests.test_live_speaker_naming_api import session_maker as session_maker
from backend.utils.config_manager import config_manager
from backend.utils.recording_storage import recording_upload_temp_dir

pytestmark = pytest.mark.anyio

CHUNK_MS = 2_000


@pytest.fixture(autouse=True)
def recordings_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECORDINGS_DIR", str(tmp_path))
    assert recording_upload_temp_dir(1).is_relative_to(tmp_path)


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


async def _upload(session_maker: Any, sequences: range) -> None:
    async with session_maker() as session:
        for n in sequences:
            session.add(
                RecordingAudioChunk(
                    recording_id=1,
                    sequence_no=n,
                    absolute_start_ms=n * CHUNK_MS,
                    absolute_end_ms=(n + 1) * CHUNK_MS,
                    duration_ms=CHUNK_MS,
                    sample_rate_hz=16_000,
                    channel_count=1,
                    byte_size=64_000,
                    sha256=f"{n:064x}",
                    storage_path=f"/tmp/{n}.wav",
                )
            )
        await session.commit()


def _lane_ran(through_sequence: int) -> None:
    """The state a live run leaves behind once it has consumed a sequence."""
    live_dir = recording_upload_temp_dir(1, create=True) / "live"
    live_dir.mkdir(exist_ok=True)
    write_live_state(live_dir, {"next_expected": through_sequence + 1})


async def _status(session_maker: Any, status: str) -> None:
    async with session_maker() as session:
        await session.execute(text("UPDATE recordings SET status = :s"), {"s": status})
        await session.commit()


async def _behind(client: Any) -> bool:
    response = await client.get(f"/api/v1/transcripts/{REC}/utterances")
    assert response.status_code == 200, response.text
    return response.json().get("live_behind", False)


@pytest.mark.parametrize("canonical", [True, False])
async def test_uploads_the_lane_has_not_read_are_reported_until_it_reads_them(
    client: Any, session_maker: Any, monkeypatch: pytest.MonkeyPatch, canonical: bool
) -> None:
    _config(monkeypatch, live=True, canonical=canonical)
    await _seed(session_maker)
    # 50 s uploaded; the lane read the first 20 s before it stopped keeping up.
    await _upload(session_maker, range(25))
    _lane_ran(9)

    assert await _behind(client) is True

    _lane_ran(24)

    assert await _behind(client) is False


async def test_a_quiet_lane_keeping_up_is_never_reported_across_pause_and_resume(
    client: Any, session_maker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config(monkeypatch, live=True, canonical=True)
    await _seed(session_maker)
    # Each upload is polled before the lane reads it, as it is between runs.
    for sequence in range(20):
        if sequence == 10:
            await _status(session_maker, "PAUSED")
            assert await _behind(client) is False
            await _status(session_maker, "UPLOADING")
        await _upload(session_maker, range(sequence, sequence + 1))
        assert await _behind(client) is False, f"after upload {sequence}"
        _lane_ran(sequence)


async def test_a_state_file_caught_mid_write_is_not_read_as_no_progress(
    client: Any, session_maker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config(monkeypatch, live=True, canonical=True)
    await _seed(session_maker)
    await _upload(session_maker, range(25))
    # Before the lane's first run there is no state: all 50 s are unread.
    assert await _behind(client) is True

    _lane_ran(24)
    state_path = recording_upload_temp_dir(1) / "live" / "state.json"
    state_path.write_text('{"next_exp', encoding="utf-8")

    assert await _behind(client) is False


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
    await _upload(session_maker, range(25))

    assert await _behind(client) is False
