"""Tests for live transcription config keys and early Transcript creation."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_current_recording_client_user, get_db
from backend.api.v1.api import api_router
from backend.utils.config_manager import (
    DEFAULT_SYSTEM_CONFIG,
    ConfigManager,
    config_manager,
)

validate_config_value = config_manager.validate_config_value


# --- Config key tests -------------------------------------------------------


def test_config_default_live_transcription_keys_present():
    """The two live transcription keys ship in DEFAULT_SYSTEM_CONFIG."""
    assert DEFAULT_SYSTEM_CONFIG["enable_live_transcription"] is True
    assert DEFAULT_SYSTEM_CONFIG["live_transcription_backend"] == "parakeet"


def test_live_transcription_keys_survive_reload(tmp_path):
    """Persisted live transcription keys survive a config_manager reload.

    Keys absent from DEFAULT_SYSTEM_CONFIG are dropped by the persistence
    filter on reload, so this guards against that regression.
    """
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "enable_live_transcription": False,
                "live_transcription_backend": "whisper",
            }
        ),
        encoding="utf-8",
    )

    manager = ConfigManager(config_path=str(config_path))
    assert manager.get("enable_live_transcription") is False
    assert manager.get("live_transcription_backend") == "whisper"

    manager.reload()
    assert manager.get("enable_live_transcription") is False
    assert manager.get("live_transcription_backend") == "whisper"


def test_validate_config_value_live_transcription_backend():
    """live_transcription_backend accepts known backends and rejects others."""
    assert validate_config_value("live_transcription_backend", "whisper") is True
    assert validate_config_value("live_transcription_backend", "parakeet") is True
    assert validate_config_value("live_transcription_backend", "bogus") is False


def test_validate_config_value_enable_live_transcription():
    """enable_live_transcription is validated as a boolean."""
    assert validate_config_value("enable_live_transcription", True) is True
    assert validate_config_value("enable_live_transcription", False) is True
    assert validate_config_value("enable_live_transcription", "yes") is False


# --- init endpoint Transcript creation test ---------------------------------

RECORDINGS_SCHEMA = """
CREATE TABLE recordings (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    name VARCHAR(255) NOT NULL,
    public_id VARCHAR(36) NOT NULL,
    meeting_uid VARCHAR(36) NOT NULL,
    audio_path VARCHAR(1024) NOT NULL,
    proxy_path VARCHAR(1024),
    celery_task_id VARCHAR(255),
    duration_seconds FLOAT,
    file_size_bytes INTEGER,
    status VARCHAR(32) NOT NULL,
    client_status VARCHAR(32),
    upload_progress INTEGER NOT NULL,
    processing_progress INTEGER NOT NULL,
    processing_step VARCHAR(255),
    processing_started_at DATETIME,
    processing_completed_at DATETIME,
    is_archived BOOLEAN NOT NULL,
    is_deleted BOOLEAN NOT NULL,
    user_id INTEGER
)
"""

TRANSCRIPTS_SCHEMA = """
CREATE TABLE transcripts (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    recording_id INTEGER NOT NULL UNIQUE,
    text TEXT,
    segments JSON,
    notes TEXT,
    user_notes TEXT,
    notes_status VARCHAR(32) NOT NULL,
    transcript_status VARCHAR(32) NOT NULL,
    error_message TEXT
)
"""


def build_test_user(user_id: int = 1, username: str = "alice"):
    from types import SimpleNamespace

    return SimpleNamespace(
        id=user_id,
        username=username,
        force_password_change=False,
    )


@pytest.fixture
async def test_session_maker() -> sessionmaker:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as connection:
        await connection.execute(text(RECORDINGS_SCHEMA))
        await connection.execute(text(TRANSCRIPTS_SCHEMA))

    try:
        yield session_maker
    finally:
        await engine.dispose()


@pytest.fixture
async def api_app() -> FastAPI:
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    return app


@pytest.fixture
async def client(api_app: FastAPI, test_session_maker: sessionmaker, monkeypatch) -> AsyncClient:
    async def override_get_db():
        async with test_session_maker() as session:
            yield session

    api_app.dependency_overrides[get_db] = override_get_db
    api_app.dependency_overrides[get_current_recording_client_user] = lambda: build_test_user()

    transport = ASGITransport(app=api_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client

    api_app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_init_endpoint_creates_processing_transcript(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    tmp_path,
    monkeypatch,
) -> None:
    """POST /recordings/init creates a Transcript row in 'processing' status."""
    from backend.api.v1.endpoints import recordings as recordings_module

    monkeypatch.setattr(
        recordings_module, "recordings_root_dir", lambda: tmp_path
    )
    monkeypatch.setattr(
        recordings_module, "recording_upload_temp_dir", lambda *a, **k: tmp_path
    )

    response = await client.post("/api/v1/recordings/init", params={"name": "Live meeting"})

    assert response.status_code == 200

    async with test_session_maker() as session:
        result = await session.execute(
            text("SELECT recording_id, transcript_status FROM transcripts")
        )
        rows = result.all()

    assert len(rows) == 1
    assert rows[0][1] == "processing"


# --- detect_speech_segments VAD helper tests --------------------------------


def test_detect_speech_segments_normalises_tensor_input(monkeypatch):
    """detect_speech_segments returns clean {start, end} float dicts for a tensor."""
    import torch

    from backend.processing import vad as vad_module

    # Synthetic 16 kHz mono buffer: silence + noise burst + silence (3 seconds).
    sample_rate = 16000
    buffer = torch.zeros(3 * sample_rate)
    buffer[sample_rate : 2 * sample_rate] = torch.randn(sample_rate) * 0.5

    fake_timestamps = [{"start": 1.0, "end": 2.0}]

    monkeypatch.setattr(
        vad_module.silero_vad, "load_silero_vad", lambda *a, **k: object()
    )
    monkeypatch.setattr(
        vad_module.silero_vad,
        "get_speech_timestamps",
        lambda *a, **k: fake_timestamps,
    )

    result = vad_module.detect_speech_segments(buffer, sample_rate=sample_rate)

    assert isinstance(result, list)
    assert len(result) == 1
    segment = result[0]
    assert set(segment.keys()) == {"start", "end"}
    assert isinstance(segment["start"], float)
    assert isinstance(segment["end"], float)
    assert segment["start"] < segment["end"]
    assert 0.0 <= segment["start"]
    assert segment["end"] <= 3.0


def test_detect_speech_segments_path_input(monkeypatch):
    """detect_speech_segments loads a WAV path via safe_read_audio."""
    import torch

    from backend.processing import vad as vad_module

    dummy_tensor = torch.zeros(16000)
    calls = {}

    def fake_safe_read_audio(path, sampling_rate=16000):
        calls["path"] = path
        calls["sampling_rate"] = sampling_rate
        return dummy_tensor

    monkeypatch.setattr(vad_module, "safe_read_audio", fake_safe_read_audio)
    monkeypatch.setattr(
        vad_module.silero_vad, "load_silero_vad", lambda *a, **k: object()
    )
    monkeypatch.setattr(
        vad_module.silero_vad,
        "get_speech_timestamps",
        lambda *a, **k: [{"start": 0.25, "end": 0.75}],
    )

    result = vad_module.detect_speech_segments("/tmp/sample.wav", sample_rate=16000)

    assert calls["path"] == "/tmp/sample.wav"
    assert calls["sampling_rate"] == 16000
    assert result == [{"start": 0.25, "end": 0.75}]


def test_detect_speech_segments_min_silence_override(monkeypatch):
    """An explicit min_silence_duration_ms overrides the config value."""
    import torch

    from backend.processing import vad as vad_module

    captured = {}

    def fake_get_speech_timestamps(*a, **k):
        captured.update(k)
        return []

    monkeypatch.setattr(
        vad_module.silero_vad, "load_silero_vad", lambda *a, **k: object()
    )
    monkeypatch.setattr(
        vad_module.silero_vad, "get_speech_timestamps", fake_get_speech_timestamps
    )

    vad_module.detect_speech_segments(
        torch.zeros(16000), sample_rate=16000, min_silence_duration_ms=700
    )

    assert captured["min_silence_duration_ms"] == 700


def test_detect_speech_segments_min_silence_default(monkeypatch):
    """Without an override, the config/default min_silence value is used."""
    import torch

    from backend.processing import vad as vad_module

    captured = {}

    def fake_get_speech_timestamps(*a, **k):
        captured.update(k)
        return []

    monkeypatch.setattr(
        vad_module.silero_vad, "load_silero_vad", lambda *a, **k: object()
    )
    monkeypatch.setattr(
        vad_module.silero_vad, "get_speech_timestamps", fake_get_speech_timestamps
    )

    vad_module.detect_speech_segments(torch.zeros(16000), sample_rate=16000)

    # get_vad_config_from_settings ships the batch-tuned default of 100 ms.
    assert captured["min_silence_duration_ms"] == 100


def test_detect_speech_segments_silence_returns_empty(monkeypatch):
    """detect_speech_segments returns [] when no speech is detected."""
    import torch

    from backend.processing import vad as vad_module

    silence = torch.zeros(2 * 16000)

    monkeypatch.setattr(
        vad_module.silero_vad, "load_silero_vad", lambda *a, **k: object()
    )
    monkeypatch.setattr(
        vad_module.silero_vad, "get_speech_timestamps", lambda *a, **k: []
    )

    result = vad_module.detect_speech_segments(silence, sample_rate=16000)

    assert result == []


# --- live_transcribe helper + task tests -----------------------------------


def _make_segment_wav(temp_dir, sequence: int, seconds: float):
    """Write a synthetic 16 kHz mono WAV segment to the recording temp dir."""
    import torch
    import torchaudio

    from backend.processing.live_transcribe import LIVE_SAMPLE_RATE

    samples = int(seconds * LIVE_SAMPLE_RATE)
    tensor = torch.zeros(1, samples)
    path = temp_dir / f"{sequence}.wav"
    torchaudio.save(str(path), tensor, LIVE_SAMPLE_RATE)
    return path


def test_classify_speech_empty():
    """Empty speech drops the silent buffer; cut point is the whole buffer."""
    from backend.processing.live_transcribe import classify_speech

    complete, cut = classify_speech([], 5.0)
    assert complete == []
    assert cut == 5.0


def test_classify_speech_trailing_incomplete_carries():
    """A region touching the buffer end is carried from its start."""
    from backend.processing.live_transcribe import classify_speech

    speech = [{"start": 0.0, "end": 1.0}, {"start": 3.0, "end": 5.0}]
    complete, cut = classify_speech(speech, 5.0)
    assert complete == [{"start": 0.0, "end": 1.0}]
    assert cut == 3.0


def test_classify_speech_last_ends_with_silence():
    """A region ending before the buffer end is complete; cut at its end."""
    from backend.processing.live_transcribe import classify_speech

    speech = [{"start": 0.0, "end": 1.0}, {"start": 2.0, "end": 3.0}]
    complete, cut = classify_speech(speech, 5.0)
    assert complete == speech
    assert cut == 3.0


def test_classify_speech_forced_cut():
    """A trailing region longer than FORCED_MAX is treated as complete."""
    from backend.processing.live_transcribe import classify_speech

    speech = [{"start": 0.0, "end": 31.0}]
    complete, cut = classify_speech(speech, 31.0)
    assert complete == speech
    assert cut == 31.0


def _patch_live_deps(monkeypatch, *, speech_map, transcribe_text="hello", db_status="UPLOADING"):
    """Patch torch-free fakes for vad / transcribe / DB used by the live task.

    speech_map: callable(combined_tensor) -> list[{"start", "end"}].
    """
    import torch

    from backend.processing import live_transcribe as lt
    from backend.processing import vad as vad_module
    from backend.processing import transcribe as transcribe_module

    monkeypatch.setattr(vad_module, "detect_speech_segments", lambda audio, *a, **k: speech_map(audio))
    monkeypatch.setattr(
        transcribe_module,
        "transcribe_audio",
        lambda path, config=None: {"text": transcribe_text, "language": None, "segments": []},
    )
    # silero_vad.save_audio is real (torchaudio-backed); the buffer.wav it
    # writes is needed for the carry-over seam test.


class _FakeTranscript:
    def __init__(self, segments=None):
        self.segments = segments if segments is not None else []


class _FakeRecording:
    def __init__(self, status, transcript):
        self.status = status
        self.transcript = transcript


class _FakeSession:
    def __init__(self, recording):
        self._recording = recording
        self.committed = False

    def get(self, model, recording_id):
        return self._recording

    def add(self, obj):
        pass

    def commit(self):
        self.committed = True

    def close(self):
        pass


def _run_live_task(monkeypatch, recording_id, sequence, fake_session):
    """Invoke the live task with the DB session and flag_modified stubbed."""
    from backend.core import db as db_module
    from sqlalchemy.orm import attributes

    from backend.processing.live_transcribe import transcribe_segment_live_task

    monkeypatch.setattr(db_module, "get_sync_session", lambda: fake_session)
    monkeypatch.setattr(attributes, "flag_modified", lambda obj, key: None)
    transcribe_segment_live_task.run(recording_id, sequence)


def test_live_in_order_run(monkeypatch, tmp_path):
    """Segments 1,2,3 dispatched in order append provisional segments in order."""
    from backend.models.recording import RecordingStatus
    from backend.processing import live_transcribe as lt

    temp_dir = tmp_path / "42"
    temp_dir.mkdir()

    monkeypatch.setattr(lt, "recording_upload_temp_dir", lambda rid, create=False: temp_dir)

    # Each single-segment run yields one speech region ending in silence.
    def speech_map(audio):
        return [{"start": 0.0, "end": 1.0}]

    _patch_live_deps(monkeypatch, speech_map=speech_map)

    transcript = _FakeTranscript()
    recording = _FakeRecording(RecordingStatus.UPLOADING, transcript)
    session = _FakeSession(recording)

    # Create each segment just-in-time so each run drains a single segment.
    for seq in (1, 2, 3):
        _make_segment_wav(temp_dir, seq, 2.0)
        _run_live_task(monkeypatch, 42, seq, session)

    assert len(transcript.segments) == 3
    starts = [s["start"] for s in transcript.segments]
    # buffer_abs_start advances by cut_point (1.0) each run.
    assert starts == [0.0, 1.0, 2.0]
    assert all(s["provisional"] is True for s in transcript.segments)
    state = lt.read_live_state(temp_dir / "live")
    assert state["next_expected"] == 4


def test_live_out_of_order_arrival(monkeypatch, tmp_path):
    """seq=2 first returns without advancing; seq=1 drains the run [1,2]."""
    from backend.models.recording import RecordingStatus
    from backend.processing import live_transcribe as lt

    temp_dir = tmp_path / "7"
    temp_dir.mkdir()

    monkeypatch.setattr(lt, "recording_upload_temp_dir", lambda rid, create=False: temp_dir)

    def speech_map(audio):
        return [{"start": 0.0, "end": 1.0}]

    _patch_live_deps(monkeypatch, speech_map=speech_map)

    transcript = _FakeTranscript()
    recording = _FakeRecording(RecordingStatus.UPLOADING, transcript)
    session = _FakeSession(recording)

    # seq=2 arrives first; only its file exists.
    _make_segment_wav(temp_dir, 2, 2.0)
    _run_live_task(monkeypatch, 7, 2, session)
    assert transcript.segments == []
    assert lt.read_live_state(temp_dir / "live")["next_expected"] == 1

    # seq=1 arrives; both files now present -> drains [1, 2].
    _make_segment_wav(temp_dir, 1, 2.0)
    _run_live_task(monkeypatch, 7, 1, session)

    assert len(transcript.segments) == 1
    assert transcript.segments[0]["start"] == 0.0
    assert lt.read_live_state(temp_dir / "live")["next_expected"] == 3


def test_live_carry_over_across_seam(monkeypatch, tmp_path):
    """A region touching the buffer end is carried and completed by a later run."""
    from backend.models.recording import RecordingStatus
    from backend.processing import live_transcribe as lt

    temp_dir = tmp_path / "9"
    temp_dir.mkdir()

    monkeypatch.setattr(lt, "recording_upload_temp_dir", lambda rid, create=False: temp_dir)

    # Run 1 (3 s buffer): speech runs to the end -> incomplete, carried.
    # Run 2 (carry ~1 s + 3 s = ~4 s): one region ending in silence -> complete.
    calls = {"n": 0}

    def speech_map(audio):
        calls["n"] += 1
        if calls["n"] == 1:
            return [{"start": 2.0, "end": 3.0}]
        return [{"start": 0.0, "end": 1.5}]

    _patch_live_deps(monkeypatch, speech_map=speech_map)

    transcript = _FakeTranscript()
    recording = _FakeRecording(RecordingStatus.UPLOADING, transcript)
    session = _FakeSession(recording)

    _make_segment_wav(temp_dir, 1, 3.0)
    _run_live_task(monkeypatch, 9, 1, session)
    # Run 1 carried the trailing utterance: no completed segment yet.
    assert transcript.segments == []
    assert (temp_dir / "live" / "buffer.wav").exists()

    _make_segment_wav(temp_dir, 2, 3.0)
    _run_live_task(monkeypatch, 9, 2, session)
    # Run 2 completes exactly one provisional segment.
    assert len(transcript.segments) == 1
    assert transcript.segments[0]["provisional"] is True


def test_live_forced_cut(monkeypatch, tmp_path):
    """A >30 s continuous speech region produces a completed provisional segment."""
    from backend.models.recording import RecordingStatus
    from backend.processing import live_transcribe as lt

    temp_dir = tmp_path / "11"
    temp_dir.mkdir()
    _make_segment_wav(temp_dir, 1, 31.0)

    monkeypatch.setattr(lt, "recording_upload_temp_dir", lambda rid, create=False: temp_dir)

    def speech_map(audio):
        return [{"start": 0.0, "end": 31.0}]

    _patch_live_deps(monkeypatch, speech_map=speech_map)

    transcript = _FakeTranscript()
    recording = _FakeRecording(RecordingStatus.UPLOADING, transcript)
    session = _FakeSession(recording)

    _run_live_task(monkeypatch, 11, 1, session)

    assert len(transcript.segments) == 1
    assert transcript.segments[0]["start"] == 0.0
    assert transcript.segments[0]["end"] == 31.0


def test_live_entry_guard_bails_when_not_uploading(monkeypatch, tmp_path):
    """A task whose recording is no longer UPLOADING stops at entry: no DB
    write, no work, and no orphan live dir recreated after finalize()."""
    from backend.models.recording import RecordingStatus
    from backend.processing import live_transcribe as lt

    temp_dir = tmp_path / "13"
    temp_dir.mkdir()
    _make_segment_wav(temp_dir, 1, 2.0)

    monkeypatch.setattr(lt, "recording_upload_temp_dir", lambda rid, create=False: temp_dir)

    def speech_map(audio):
        return [{"start": 0.0, "end": 1.0}]

    _patch_live_deps(monkeypatch, speech_map=speech_map)

    transcript = _FakeTranscript()
    recording = _FakeRecording(RecordingStatus.PROCESSING, transcript)
    session = _FakeSession(recording)

    _run_live_task(monkeypatch, 13, 1, session)

    assert transcript.segments == []
    assert session.committed is False
    # Entry guard ran before any file work: no live dir was created.
    assert not (temp_dir / "live").exists()


def test_live_race_guard_skips_db_write_on_late_status_flip(monkeypatch, tmp_path):
    """finalize() landing mid-run (UPLOADING at entry, PROCESSING at the DB
    write) skips the provisional write but still advances the lane cleanly."""
    from backend.models.recording import RecordingStatus
    from backend.processing import live_transcribe as lt

    temp_dir = tmp_path / "14"
    temp_dir.mkdir()
    _make_segment_wav(temp_dir, 1, 2.0)

    monkeypatch.setattr(lt, "recording_upload_temp_dir", lambda rid, create=False: temp_dir)

    def speech_map(audio):
        return [{"start": 0.0, "end": 1.0}]

    _patch_live_deps(monkeypatch, speech_map=speech_map)

    class _FlipToProcessingRecording:
        """UPLOADING on the first status read, PROCESSING thereafter."""

        def __init__(self, transcript):
            self.transcript = transcript
            self._reads = 0

        @property
        def status(self):
            self._reads += 1
            return (
                RecordingStatus.UPLOADING
                if self._reads == 1
                else RecordingStatus.PROCESSING
            )

    transcript = _FakeTranscript()
    recording = _FlipToProcessingRecording(transcript)
    session = _FakeSession(recording)

    _run_live_task(monkeypatch, 14, 1, session)

    assert transcript.segments == []
    assert session.committed is False
    # The run completed normally; only the DB write was guarded.
    assert lt.read_live_state(temp_dir / "live")["next_expected"] == 2


def test_live_failure_path_advances_without_raising(monkeypatch, tmp_path):
    """transcribe_audio raising is logged; the lane advances; no re-raise."""
    from backend.models.recording import RecordingStatus
    from backend.processing import live_transcribe as lt
    from backend.processing import vad as vad_module
    from backend.processing import transcribe as transcribe_module

    temp_dir = tmp_path / "15"
    temp_dir.mkdir()
    _make_segment_wav(temp_dir, 1, 2.0)

    monkeypatch.setattr(lt, "recording_upload_temp_dir", lambda rid, create=False: temp_dir)
    monkeypatch.setattr(
        vad_module, "detect_speech_segments", lambda audio, *a, **k: [{"start": 0.0, "end": 1.0}]
    )

    import silero_vad

    monkeypatch.setattr(silero_vad, "save_audio", lambda *a, **k: None)

    def boom(path, config=None):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(transcribe_module, "transcribe_audio", boom)

    transcript = _FakeTranscript()
    recording = _FakeRecording(RecordingStatus.UPLOADING, transcript)
    session = _FakeSession(recording)

    # Must not raise.
    _run_live_task(monkeypatch, 15, 1, session)

    assert transcript.segments == []
    assert lt.read_live_state(temp_dir / "live")["next_expected"] == 2


# --- segment endpoint live-task dispatch tests ------------------------------


async def _insert_uploading_recording(
    session_maker: sessionmaker,
    *,
    recording_id: int = 101,
    public_id: str = "live-rec-public-id",
    user_id: int = 1,
) -> None:
    """Insert a single recording row in UPLOADING status into the test DB."""
    async with session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, status, upload_progress, processing_progress,
                    is_archived, is_deleted, user_id
                ) VALUES (
                    :id, :now, :now, :name, :public_id, :meeting_uid,
                    :audio_path, :status, 0, 0, 0, 0, :user_id
                )
                """
            ),
            {
                "id": recording_id,
                "now": "2026-05-16 00:00:00",
                "name": "Live meeting",
                "public_id": public_id,
                "meeting_uid": "live-meeting-uid",
                "audio_path": "/tmp/live.wav",
                "status": "UPLOADING",
                "user_id": user_id,
            },
        )
        await session.commit()


@pytest.mark.anyio
async def test_segment_upload_dispatches_live_task_when_enabled(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    tmp_path,
    monkeypatch,
) -> None:
    """When enable_live_transcription is true, a segment upload enqueues the task."""
    from backend.api.v1.endpoints import recordings as recordings_module

    await _insert_uploading_recording(test_session_maker, recording_id=101)

    monkeypatch.setattr(
        recordings_module, "recording_upload_temp_dir", lambda *a, **k: tmp_path
    )
    monkeypatch.setattr(
        recordings_module.config_manager,
        "get",
        lambda key, default=None: True if key == "enable_live_transcription" else default,
    )

    calls = []
    monkeypatch.setattr(
        recordings_module.transcribe_segment_live_task,
        "delay",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    response = await client.post(
        "/api/v1/recordings/live-rec-public-id/segment",
        params={"sequence": 3},
        files={"file": ("3.wav", b"fake-wav-bytes", "audio/wav")},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "received", "segment": 3}
    assert len(calls) == 1
    assert calls[0][0] == (101, 3)


@pytest.mark.anyio
async def test_segment_upload_skips_live_task_when_disabled(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    tmp_path,
    monkeypatch,
) -> None:
    """When enable_live_transcription is false, no task is enqueued."""
    from backend.api.v1.endpoints import recordings as recordings_module

    await _insert_uploading_recording(test_session_maker, recording_id=101)

    monkeypatch.setattr(
        recordings_module, "recording_upload_temp_dir", lambda *a, **k: tmp_path
    )
    monkeypatch.setattr(
        recordings_module.config_manager,
        "get",
        lambda key, default=None: False if key == "enable_live_transcription" else default,
    )

    calls = []
    monkeypatch.setattr(
        recordings_module.transcribe_segment_live_task,
        "delay",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    response = await client.post(
        "/api/v1/recordings/live-rec-public-id/segment",
        params={"sequence": 0},
        files={"file": ("0.wav", b"fake-wav-bytes", "audio/wav")},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "received", "segment": 0}
    assert calls == []


@pytest.mark.anyio
async def test_segment_upload_swallows_live_task_dispatch_failure(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    tmp_path,
    monkeypatch,
) -> None:
    """A failing .delay() is logged and the segment upload still succeeds."""
    from backend.api.v1.endpoints import recordings as recordings_module

    await _insert_uploading_recording(test_session_maker, recording_id=101)

    monkeypatch.setattr(
        recordings_module, "recording_upload_temp_dir", lambda *a, **k: tmp_path
    )
    monkeypatch.setattr(
        recordings_module.config_manager,
        "get",
        lambda key, default=None: True if key == "enable_live_transcription" else default,
    )

    def boom(*args, **kwargs):
        raise RuntimeError("broker unreachable")

    monkeypatch.setattr(
        recordings_module.transcribe_segment_live_task, "delay", boom
    )

    response = await client.post(
        "/api/v1/recordings/live-rec-public-id/segment",
        params={"sequence": 5},
        files={"file": ("5.wav", b"fake-wav-bytes", "audio/wav")},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "received", "segment": 5}


# --- Celery registration test ----------------------------------------------


def test_live_task_registered_with_celery():
    """The live task module is wired into the Celery app and the task registers.

    Without backend.processing.live_transcribe in the Celery include list the
    worker would never import the module, so transcribe_segment_live_task.delay
    would fail at runtime with a NotRegistered error.
    """
    from backend.celery_app import celery_app

    celery_app.loader.import_default_modules()

    assert (
        "backend.processing.live_transcribe.transcribe_segment_live_task"
        in celery_app.tasks
    )
