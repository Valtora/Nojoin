"""Real resume and finalise routes and live workers retain capture geometry."""

import asyncio
import sys
import wave
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
import soundfile as sf
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import Session, select

from backend.api.deps import get_current_recording_client_user, get_db
from backend.api.v1.api import api_router
from backend.api.v1.endpoints.recordings import routes_capture
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.processing import live_settling, transcribe, vad
from backend.tests.test_canonical_publication_fence import SCHEMA
from backend.tests.test_canonical_publication_fence import (
    publication_database as publication_database,
)
from backend.tests.test_capture_stop_publication import (
    CaptureHarness,
    _capture_token,
    _pause_route,
    _request,
    _stop,
)
from backend.tests.test_capture_stop_publication import (
    capture_harness as capture_harness,
)
from backend.tests.test_live_resume_lane import ResumeLane
from backend.tests.test_live_resume_lane import resume_lane as resume_lane
from backend.tests.test_live_settling_publication import (
    settling_engine as settling_engine,
)
from backend.tests.test_pipeline_stages import (
    _FakeRecording,
    _FakeSession,
    _install,
    _install_happy_path_modules,
    _run_task,
)
from backend.utils import transcript_utils
from backend.utils.capture_resume import load_resume_boundaries
from backend.utils.recording_audio_registration import (
    ChunkStream,
    register_recording_audio_chunk,
)


def _wav(path: Path, milliseconds: int) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(2)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0" * (milliseconds * 16 * 4))


@pytest.mark.parametrize("marked", [False, True])
def test_resume_cut_uses_final_chunk_offsets_and_survives_finalize(
    capture_harness: CaptureHarness, monkeypatch: pytest.MonkeyPatch, marked: bool
) -> None:
    import asyncio

    engine, recording_id, _, _, directory = capture_harness
    _capture_token(capture_harness, monkeypatch, marked)
    _wav(directory / "0.wav", 125)
    _wav(directory / "1.wav", 375)

    async def resume(db: Any, public_id: str, user: Any) -> Any:
        return await routes_capture.resume_upload(
            public_id, epoch=0, db=db, current_user=user
        )

    asyncio.run(_request(capture_harness, _pause_route, "pause"))
    assert asyncio.run(_request(capture_harness, resume, "resume")).last_sequence == 1
    _wav(directory / "2.wav", 250)
    with Session(engine) as session:
        stream = ChunkStream(recording_id, "browser", directory)
        for sequence in [2, 1]:
            register_recording_audio_chunk(
                session,
                stream,
                sequence=sequence,
                storage_path=directory / f"{sequence}.wav",
            )
        transcript = session.exec(select(Transcript)).one()
        assert load_resume_boundaries(session, transcript) == []
        register_recording_audio_chunk(
            session, stream, sequence=0, storage_path=directory / "0.wav"
        )
        assert load_resume_boundaries(session, transcript) == [500]
        session.commit()
    _stop(capture_harness)
    with Session(engine) as session:
        transcript = session.exec(select(Transcript)).one()
        assert transcript.settling_state["resume_boundaries_ms"] == [500]
        assert bool(transcript.settling_state.get("eligible")) is marked


@asynccontextmanager
async def _capture_client(harness: CaptureHarness) -> AsyncIterator[AsyncClient]:
    """The real recordings API on the harness's PostgreSQL schema, as its owner."""
    engine, _, _, owner_id, _ = harness
    async_engine = create_async_engine(
        engine.url.set(drivername="postgresql+asyncpg"),
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
    )

    async def database() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(async_engine, expire_on_commit=False) as session:
            yield session

    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_recording_client_user] = lambda: User(
        id=owner_id, username="fence-owner", hashed_password="x"
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client
    finally:
        await async_engine.dispose()


def _settling_state(engine: Any) -> dict[str, Any]:
    with Session(engine) as session:
        return session.exec(select(Transcript)).one().settling_state or {}


async def _paused_and_resumed_capture(harness: CaptureHarness) -> None:
    """Two segments, Pause, a takeover Resume and one more segment, then Stop."""
    engine, _, public_id, _, directory = harness
    base = f"/api/v1/recordings/{public_id}"

    async with _capture_client(harness) as client:

        async def segment(sequence: int, epoch: int) -> None:
            _wav(directory.parent / "upload.wav", 250)
            response = await client.post(
                f"{base}/segment",
                params={"sequence": sequence, "epoch": epoch},
                files={
                    "file": (
                        f"{sequence}.wav",
                        (directory.parent / "upload.wav").read_bytes(),
                        "audio/wav",
                    )
                },
            )
            assert response.status_code == 200, response.text

        await segment(0, 0)
        await segment(1, 0)
        assert (await client.post(f"{base}/pause", params={"epoch": 0})).is_success
        resumed = await client.post(f"{base}/resume")
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["last_sequence"] == 1
        assert resumed.json()["capture_epoch"] == 1
        # The tab that held the capture resumes too late: refused, no second fence.
        stale = await client.post(f"{base}/resume", params={"epoch": 0})
        assert stale.status_code == 409
        # The holder resuming again records the same fence once.
        again = await client.post(f"{base}/resume", params={"epoch": 1})
        assert again.status_code == 200, again.text
        assert _settling_state(engine)["resume_chunk_sequences"] == [2]
        await segment(2, 1)
        stopped = await client.post(f"{base}/finalize", params={"epoch": 1})
        assert stopped.status_code == 200, stopped.text


@pytest.mark.parametrize("marked", [False, True])
def test_resume_over_http_breaks_the_final_line_at_the_resumed_audio(
    capture_harness: CaptureHarness, monkeypatch: pytest.MonkeyPatch, marked: bool
) -> None:
    """Pause, Resume and Stop through the API, then the final pass on that row."""
    engine, recording_id, _, _, _ = capture_harness
    _capture_token(capture_harness, monkeypatch, marked)
    asyncio.run(_paused_and_resumed_capture(capture_harness))
    # 0 and 1 are 250 ms each, so the resumed audio starts 500 ms in.
    assert _settling_state(engine)["resume_boundaries_ms"] == [500]

    with Session(engine, expire_on_commit=False) as session:
        transcript = session.exec(select(Transcript)).one()
    session = _FakeSession(_FakeRecording(recording_id), transcript)
    _install_happy_path_modules(monkeypatch)
    # Speech running straight across the resume, as one ASR segment.
    words = [
        {"start": 0.2, "end": 0.5, "word": " Before"},
        {"start": 0.5, "end": 0.7, "word": " after"},
    ]
    _install(
        monkeypatch,
        "backend.processing.transcribe",
        transcribe_audio=lambda *a, **k: {
            "text": " Before after",
            "language": "en",
            "segments": [
                {"start": 0.2, "end": 0.7, "text": " Before after", "words": words}
            ],
        },
        release_model_cache=lambda: None,
    )
    monkeypatch.setitem(sys.modules, "backend.utils.transcript_utils", transcript_utils)
    _run_task(monkeypatch, session, recording_id=recording_id)

    assert [
        (segment["text"], segment["start"], segment["end"])
        for segment in transcript.segments
    ] == [("Before", 0.2, 0.5), ("after", 0.5, 0.7)]


@pytest.mark.parametrize(
    "resume_lane", [("off", False), ("cpu", False), ("off", True)], indirect=True
)
def test_legacy_worker_cuts_continuous_vad_audio_before_each_asr_call(
    resume_lane: ResumeLane, monkeypatch: pytest.MonkeyPatch
) -> None:
    lane = resume_lane
    with Session(lane.engine) as session:
        transcript = session.exec(select(Transcript)).one()
        transcript.settling_state = {
            **(transcript.settling_state or {}),
            "resume_boundaries_ms": [3000],
        }
        session.add(transcript)
        session.commit()
    lane.upload(1)
    lane.upload(2)
    monkeypatch.setattr(
        vad,
        "detect_speech_segments",
        # The 0.3 s tail exceeds TRAIL_EPS, so VAD considers this region complete.
        lambda audio, **kwargs: [{"start": 0.1, "end": audio.numel() / 16000 - 0.3}],
    )
    durations = []

    def decode(path: str, **kwargs: Any) -> dict[str, Any]:
        durations.append(sf.info(path).duration)
        text = "before" if len(durations) == 1 else "after"
        return {
            "text": text,
            "segments": [{"start": 0.0, "end": durations[-1], "text": text}],
        }

    monkeypatch.setattr(transcribe, "transcribe_audio", decode)
    lane.run(1)
    assert durations == pytest.approx([1.9, 2.7])
    assert [(row[1], row[2], row[3]) for row in lane.rows()][-2:] == [
        ("before", 1100, 3000),
        ("after", 3000, 5700),
    ]
    before, after = lane.rows()[-2:]
    assert "capture_interval" not in before[4]
    assert after[4]["capture_interval"] == 1


@pytest.mark.parametrize("resume_lane", [("on", True)], indirect=True)
def test_rolling_worker_assigns_capture_intervals_after_a_resume_cut(
    resume_lane: ResumeLane, monkeypatch: pytest.MonkeyPatch
) -> None:
    lane = resume_lane
    with Session(lane.engine) as session:
        transcript = session.exec(select(Transcript)).one()
        transcript.settling_state = {
            **transcript.settling_state,
            "resume_boundaries_ms": [3000],
        }
        session.add(transcript)
        session.commit()
    lane.upload(1)
    lane.upload(2)

    def decode(*args: Any) -> tuple[dict[str, Any], str]:
        return {
            "asr_segments": [
                {
                    "words": [
                        {"word": "Before", "start_ms": 2500, "end_ms": 3000},
                        {"word": " after", "start_ms": 3000, "end_ms": 3500},
                    ]
                }
            ]
        }, "cuda"

    monkeypatch.setattr(live_settling, "_decode_words", decode)
    lane.run(1)
    before, after = lane.rows()[-2:]
    assert before[1] == "Before" and after[1] == "after"
    assert "capture_interval" not in before[4]
    assert after[4]["capture_interval"] == 1
