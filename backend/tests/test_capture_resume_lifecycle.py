"""Real resume and finalise routes and live workers retain capture geometry."""

import wave
from pathlib import Path
from typing import Any

import pytest
import soundfile as sf
from sqlmodel import Session, select

from backend.api.v1.endpoints.recordings import routes_capture
from backend.models.transcript import Transcript
from backend.processing import live_settling, transcribe, vad
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
