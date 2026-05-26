"""Tests for live transcription config keys and early Transcript creation."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import wave

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_current_recording_client_user, get_current_user, get_db
from backend.api.v1.api import api_router
from backend.utils.config_manager import (
    DEFAULT_SYSTEM_CONFIG,
    ConfigManager,
    config_manager,
    get_default_user_settings,
)

validate_config_value = config_manager.validate_config_value


# --- Config key tests -------------------------------------------------------


def test_config_default_live_transcription_keys_present():
    """Live transcription is enabled and uses the shared transcription backend."""
    assert DEFAULT_SYSTEM_CONFIG["enable_live_transcription"] is True
    assert DEFAULT_SYSTEM_CONFIG["enable_asr_window_result_ledger"] is True
    assert DEFAULT_SYSTEM_CONFIG["enable_rolling_diarization"] is True
    assert DEFAULT_SYSTEM_CONFIG["transcription_backend"] == "whisper"
    assert DEFAULT_SYSTEM_CONFIG["live_max_segment_s"] == 20.0
    assert DEFAULT_SYSTEM_CONFIG["rolling_diarization_window_ms"] == 20_000
    assert DEFAULT_SYSTEM_CONFIG["rolling_diarization_hop_ms"] == 5_000
    assert DEFAULT_SYSTEM_CONFIG["rolling_diarization_max_windows_per_pass"] == 2
    assert DEFAULT_SYSTEM_CONFIG["rolling_diarization_max_active_runs"] == 1


def test_live_transcription_keys_survive_reload(tmp_path):
    """Persisted live transcription enablement survives a config_manager reload."""
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "enable_live_transcription": False,
                "transcription_backend": "parakeet",
                "live_max_segment_s": 12.0,
            }
        ),
        encoding="utf-8",
    )

    manager = ConfigManager(config_path=str(config_path))
    assert manager.get("enable_live_transcription") is False
    assert manager.get("transcription_backend") == "parakeet"
    assert manager.get("live_max_segment_s") == 12.0

    manager.reload()
    assert manager.get("enable_live_transcription") is False
    assert manager.get("transcription_backend") == "parakeet"
    assert manager.get("live_max_segment_s") == 12.0


def test_validate_config_value_enable_live_transcription():
    """enable_live_transcription is validated as a boolean."""
    assert validate_config_value("enable_live_transcription", True) is True
    assert validate_config_value("enable_live_transcription", False) is True
    assert validate_config_value("enable_live_transcription", "yes") is False


def test_validate_config_value_enable_asr_window_result_ledger():
    """enable_asr_window_result_ledger is validated as a boolean."""
    assert validate_config_value("enable_asr_window_result_ledger", True) is True
    assert validate_config_value("enable_asr_window_result_ledger", False) is True
    assert validate_config_value("enable_asr_window_result_ledger", "yes") is False


def test_validate_config_value_enable_rolling_diarization():
    """enable_rolling_diarization is validated as a boolean."""
    assert validate_config_value("enable_rolling_diarization", True) is True
    assert validate_config_value("enable_rolling_diarization", False) is True
    assert validate_config_value("enable_rolling_diarization", "yes") is False


def test_validate_config_value_rolling_diarization_window_settings():
    """Rolling diarization window knobs must be positive integers."""
    assert validate_config_value("rolling_diarization_window_ms", 20_000) is True
    assert validate_config_value("rolling_diarization_hop_ms", 5_000) is True
    assert validate_config_value("rolling_diarization_max_windows_per_pass", 2) is True
    assert validate_config_value("rolling_diarization_max_active_runs", 1) is True
    assert validate_config_value("rolling_diarization_window_ms", 0) is False
    assert validate_config_value("rolling_diarization_hop_ms", -1) is False
    assert validate_config_value("rolling_diarization_max_windows_per_pass", 0) is False
    assert validate_config_value("rolling_diarization_max_active_runs", 0) is False


def test_default_user_settings_enable_meeting_edge_by_default():
    """Meeting Edge defaults to enabled for new users."""
    assert get_default_user_settings()["enable_meeting_edge"] is True


def test_validate_config_value_enable_meeting_edge():
    """enable_meeting_edge is validated as a boolean."""
    assert validate_config_value("enable_meeting_edge", True) is True
    assert validate_config_value("enable_meeting_edge", False) is True
    assert validate_config_value("enable_meeting_edge", "no") is False


def test_validate_config_value_live_max_segment_s():
    """live_max_segment_s must be a positive number."""
    assert validate_config_value("live_max_segment_s", 20) is True
    assert validate_config_value("live_max_segment_s", 7.5) is True
    assert validate_config_value("live_max_segment_s", 0) is False
    assert validate_config_value("live_max_segment_s", -1) is False


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
    pipeline_generation VARCHAR(32) DEFAULT 'unified',
    is_archived BOOLEAN NOT NULL,
    is_deleted BOOLEAN NOT NULL,
    user_id INTEGER,
    calendar_event_id INTEGER
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
    meeting_edge_focus TEXT,
    meeting_edge_payload JSON,
    meeting_edge_status VARCHAR(32) NOT NULL DEFAULT 'idle',
    meeting_edge_error_message TEXT,
    meeting_edge_source_signature TEXT,
    speaker_name_suggestions JSON,
    notes_status VARCHAR(32) NOT NULL,
    transcript_status VARCHAR(32) NOT NULL,
    error_message TEXT
)
"""

RECORDING_AUDIO_CHUNKS_SCHEMA = """
CREATE TABLE recording_audio_chunks (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    public_id VARCHAR(36) NOT NULL,
    recording_id INTEGER NOT NULL,
    sequence_no INTEGER NOT NULL,
    source_kind VARCHAR(32) NOT NULL,
    absolute_start_ms INTEGER NOT NULL,
    absolute_end_ms INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    sample_rate_hz INTEGER NOT NULL,
    channel_count INTEGER NOT NULL,
    byte_size INTEGER NOT NULL,
    sha256 VARCHAR(128) NOT NULL,
    storage_path VARCHAR(1024) NOT NULL,
    upload_status VARCHAR(32) NOT NULL,
    idempotency_key VARCHAR(255),
    received_at DATETIME NOT NULL,
    cleanup_eligible_at DATETIME
)
"""

RECORDING_AUDIO_WINDOW_MANIFESTS_SCHEMA = """
CREATE TABLE recording_audio_window_manifests (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    public_id VARCHAR(36) NOT NULL,
    recording_id INTEGER NOT NULL,
    window_index INTEGER NOT NULL,
    source_kind VARCHAR(32) NOT NULL,
    target_window_ms INTEGER NOT NULL,
    hop_ms INTEGER NOT NULL,
    window_start_ms INTEGER NOT NULL,
    window_end_ms INTEGER NOT NULL,
    chunk_start_sequence INTEGER NOT NULL,
    chunk_end_sequence INTEGER NOT NULL,
    status VARCHAR(32) NOT NULL,
    is_partial BOOLEAN NOT NULL,
    is_sealed BOOLEAN NOT NULL,
    processing_run_id INTEGER,
    last_error TEXT
)
"""


def build_test_user(user_id: int = 1, username: str = "alice", settings: dict | None = None):
    from types import SimpleNamespace

    return SimpleNamespace(
        id=user_id,
        username=username,
        force_password_change=False,
        settings=settings or {},
    )


def _make_wav_bytes(*, duration_s: float = 0.5, sample_rate: int = 16000) -> bytes:
    frame_count = int(duration_s * sample_rate)
    pcm_frames = b"\x00\x00" * frame_count
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_frames)
    return buffer.getvalue()


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
        await connection.execute(text(RECORDING_AUDIO_CHUNKS_SCHEMA))
        await connection.execute(text(RECORDING_AUDIO_WINDOW_MANIFESTS_SCHEMA))

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
    api_app.dependency_overrides[get_current_user] = lambda: build_test_user()

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


@pytest.mark.anyio
async def test_meeting_edge_focus_endpoint_persists_focus_and_dispatches_refresh(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    monkeypatch,
) -> None:
    """PUT /transcripts/{id}/meeting-edge-focus saves focus text and queues a refresh."""
    from backend.api.v1.endpoints import transcripts as transcripts_module

    dispatched: list[tuple[str, list[int]]] = []

    def fake_send_task(task_name: str, args: list[int] | None = None, **_: object) -> None:
        dispatched.append((task_name, list(args or [])))

    monkeypatch.setattr(transcripts_module.celery_app, "send_task", fake_send_task)

    async with test_session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, proxy_path, celery_task_id, duration_seconds,
                    file_size_bytes, status, client_status,
                    upload_progress, processing_progress, processing_step,
                    processing_started_at,
                    processing_completed_at, is_archived, is_deleted,
                    user_id, calendar_event_id
                ) VALUES (
                    1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'Live meeting',
                    'rec-public', 'meeting-public', '/tmp/audio.wav', NULL,
                    NULL, 120.0, NULL, 'UPLOADING', NULL, 0, 0, NULL,
                    NULL, NULL, 0, 0, 1, NULL
                )
                """
            )
        )
        await session.commit()

    response = await client.put(
        "/api/v1/transcripts/rec-public/meeting-edge-focus",
        json={"meeting_edge_focus": "Flag missing owners and timeline risk."},
    )

    assert response.status_code == 200
    assert response.json()["meeting_edge_focus"] == "Flag missing owners and timeline risk."
    assert dispatched == [
        ("backend.worker.tasks.refresh_meeting_edge_task", [1])
    ]

    async with test_session_maker() as session:
        result = await session.execute(
            text(
                "SELECT meeting_edge_focus, meeting_edge_status FROM transcripts WHERE recording_id = 1"
            )
        )
        row = result.one()

    assert row[0] == "Flag missing owners and timeline risk."
    assert row[1] == "idle"


@pytest.mark.anyio
async def test_meeting_edge_focus_endpoint_skips_refresh_when_feature_disabled(
    client: AsyncClient,
    api_app: FastAPI,
    test_session_maker: sessionmaker,
    monkeypatch,
) -> None:
    """PUT /transcripts/{id}/meeting-edge-focus saves focus text without queuing refresh when disabled."""
    from backend.api.v1.endpoints import transcripts as transcripts_module

    dispatched: list[tuple[str, list[int]]] = []

    def fake_send_task(task_name: str, args: list[int] | None = None, **_: object) -> None:
        dispatched.append((task_name, list(args or [])))

    monkeypatch.setattr(transcripts_module.celery_app, "send_task", fake_send_task)
    api_app.dependency_overrides[get_current_user] = lambda: build_test_user(
        settings={"enable_meeting_edge": False}
    )

    async with test_session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, proxy_path, celery_task_id, duration_seconds,
                    file_size_bytes, status, client_status,
                    upload_progress, processing_progress, processing_step,
                    processing_started_at,
                    processing_completed_at, is_archived, is_deleted,
                    user_id, calendar_event_id
                ) VALUES (
                    2, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'Live meeting',
                    'rec-public-disabled', 'meeting-public-disabled', '/tmp/audio.wav', NULL,
                    NULL, 120.0, NULL, 'UPLOADING', NULL, 0, 0, NULL,
                    NULL, NULL, 0, 0, 1, NULL
                )
                """
            )
        )
        await session.commit()

    response = await client.put(
        "/api/v1/transcripts/rec-public-disabled/meeting-edge-focus",
        json={"meeting_edge_focus": "Keep this saved but do not refresh."},
    )

    assert response.status_code == 200
    assert response.json()["meeting_edge_focus"] == "Keep this saved but do not refresh."
    assert dispatched == []


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


def test_detect_speech_segments_speech_pad_override(monkeypatch):
    """An explicit speech_pad_ms overrides the config value."""
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
        torch.zeros(16000), sample_rate=16000, speech_pad_ms=300
    )

    assert captured["speech_pad_ms"] == 300


def test_detect_speech_segments_speech_pad_default(monkeypatch):
    """Without an override, the config/default speech_pad value (30 ms) is used.

    Guards the batch path: an unpadded call must behave exactly as before.
    """
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

    assert captured["speech_pad_ms"] == 30


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


class _FakeAudioStore:
    def __init__(self):
        self._audio_by_path: dict[str, object] = {}

    def save_audio(self, path: str, tensor, sampling_rate: int = 16000) -> None:
        import torch

        normalized = tensor.detach().clone()
        if normalized.ndim > 1:
            normalized = normalized.squeeze(0)
        self._audio_by_path[str(path)] = normalized
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(b"audio")

    def read_audio(self, path: str, sampling_rate: int = 16000):
        import torch

        tensor = self._audio_by_path.get(str(path))
        if tensor is None:
            raise FileNotFoundError(path)
        return tensor.detach().clone()

    def make_segment(self, temp_dir, sequence: int, seconds: float):
        import torch

        from backend.processing.live_transcribe import LIVE_SAMPLE_RATE

        samples = int(seconds * LIVE_SAMPLE_RATE)
        tensor = torch.zeros(samples)
        path = temp_dir / f"{sequence}.wav"
        self.save_audio(str(path), tensor, sampling_rate=LIVE_SAMPLE_RATE)
        return path


def _make_segment_wav(temp_dir, sequence: int, seconds: float, audio_store: _FakeAudioStore):
    """Register a synthetic mono segment without invoking torchaudio."""
    return audio_store.make_segment(temp_dir, sequence, seconds)


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
    """A trailing region longer than the default forced max is complete."""
    from backend.processing.live_transcribe import classify_speech

    speech = [{"start": 0.0, "end": 8.1}]
    complete, cut = classify_speech(speech, 8.1)
    assert complete == speech
    assert cut == 8.1


def test_classify_speech_splits_long_complete_region():
    """A long complete region is split into hard-capped emitted segments."""
    from backend.processing.live_transcribe import classify_speech

    speech = [{"start": 0.0, "end": 45.0}]
    complete, cut = classify_speech(speech, 50.0, max_segment_s=20.0)

    assert complete == [
        {"start": 0.0, "end": 20.0},
        {"start": 20.0, "end": 40.0},
        {"start": 40.0, "end": 45.0},
    ]
    assert cut == 45.0


def test_classify_speech_splits_long_forced_trailing_region():
    """A forced trailing flush is still subdivided into smaller provisional chunks."""
    from backend.processing.live_transcribe import classify_speech

    speech = [{"start": 0.0, "end": 31.0}]
    complete, cut = classify_speech(speech, 31.0, max_segment_s=20.0)

    assert complete == [
        {"start": 0.0, "end": 20.0},
        {"start": 20.0, "end": 31.0},
    ]
    assert cut == 31.0


def test_live_state_preserves_last_speaker_label(tmp_path):
    """Live state persists the last stable speaker label across runs."""
    from backend.processing.live_transcribe import read_live_state, write_live_state

    live_dir = tmp_path / "live"
    live_dir.mkdir()

    write_live_state(
        live_dir,
        {"next_expected": 3, "buffer_abs_start": 4.5, "last_speaker_label": "LIVE_02"},
    )

    state = read_live_state(live_dir)

    assert state["next_expected"] == 3
    assert state["buffer_abs_start"] == 4.5
    assert state["last_speaker_label"] == "LIVE_02"


def test_build_audio_window_specs_tracks_overlap_independent_of_chunk_cadence():
    from types import SimpleNamespace

    from backend.utils.audio_windows import build_audio_window_specs

    chunk_rows = [
        SimpleNamespace(sequence_no=1, source_kind="browser", absolute_start_ms=0, absolute_end_ms=1000),
        SimpleNamespace(sequence_no=2, source_kind="browser", absolute_start_ms=1000, absolute_end_ms=2300),
        SimpleNamespace(sequence_no=3, source_kind="browser", absolute_start_ms=2300, absolute_end_ms=3600),
    ]

    specs = build_audio_window_specs(
        chunk_rows,
        target_window_ms=2000,
        hop_ms=1000,
        seal_tail=True,
    )

    assert [(spec.window_start_ms, spec.window_end_ms, spec.is_partial) for spec in specs] == [
        (0, 2000, False),
        (1000, 3000, False),
        (2000, 3600, True),
    ]
    assert [(spec.chunk_start_sequence, spec.chunk_end_sequence) for spec in specs] == [
        (1, 2),
        (2, 3),
        (2, 3),
    ]


def test_collect_pending_chunk_spans_merges_overlapping_pending_windows():
    from types import SimpleNamespace

    from backend.utils.audio_windows import CatchUpChunkSpan, collect_pending_chunk_spans

    chunk_rows = [
        SimpleNamespace(sequence_no=1, absolute_start_ms=0, absolute_end_ms=1000),
        SimpleNamespace(sequence_no=2, absolute_start_ms=1000, absolute_end_ms=2000),
        SimpleNamespace(sequence_no=3, absolute_start_ms=2000, absolute_end_ms=3000),
    ]
    manifest_rows = [
        SimpleNamespace(id=1, chunk_start_sequence=1, chunk_end_sequence=2, status="live_processed"),
        SimpleNamespace(id=2, chunk_start_sequence=2, chunk_end_sequence=3, status="pending"),
        SimpleNamespace(id=3, chunk_start_sequence=3, chunk_end_sequence=3, status="pending"),
    ]

    spans = collect_pending_chunk_spans(manifest_rows, chunk_rows)

    assert spans == [
        CatchUpChunkSpan(start_sequence=2, end_sequence=3, start_ms=1000, end_ms=3000)
    ]


def test_infer_resume_state_from_manifest_rows():
    from types import SimpleNamespace

    from backend.utils.audio_windows import infer_resume_state_from_manifests

    resumed_state = infer_resume_state_from_manifests(
        [
            SimpleNamespace(chunk_end_sequence=2, window_end_ms=3000, status="pending"),
            SimpleNamespace(chunk_end_sequence=4, window_end_ms=5500, status="live_processed"),
        ]
    )

    assert resumed_state == {"next_expected": 5, "buffer_abs_start": 5.5}


def test_select_live_rolling_diarization_manifests_skips_completed_windows_and_unsealed_tail(
    monkeypatch,
):
    from types import SimpleNamespace

    from backend.processing.live_transcribe import _select_live_rolling_diarization_manifests

    manifest_rows = [
        SimpleNamespace(id=1, window_index=0, chunk_end_sequence=4, is_partial=False, is_sealed=False),
        SimpleNamespace(id=2, window_index=1, chunk_end_sequence=5, is_partial=False, is_sealed=False),
        SimpleNamespace(id=3, window_index=2, chunk_end_sequence=6, is_partial=True, is_sealed=False),
        SimpleNamespace(id=4, window_index=3, chunk_end_sequence=6, is_partial=True, is_sealed=True),
    ]

    class _ExecResult:
        def all(self):
            return [1]

    class _Session:
        def exec(self, *args, **kwargs):
            return _ExecResult()

    monkeypatch.setattr(
        "backend.processing.live_transcribe._load_recording_audio_window_manifests",
        lambda session, recording_id: manifest_rows,
    )

    selected_rows = _select_live_rolling_diarization_manifests(
        _Session(),
        recording_id=1,
        up_to_sequence=6,
        config_hash="rolling-cfg-1",
        max_windows_per_pass=3,
    )

    assert [row.window_index for row in selected_rows] == [0, 3]


def test_select_live_rolling_diarization_manifests_skips_active_claims_and_reclaims_failed_runs(
    monkeypatch,
):
    from types import SimpleNamespace

    from backend.processing.live_transcribe import _select_live_rolling_diarization_manifests

    manifest_rows = [
        SimpleNamespace(
            id=1,
            window_index=0,
            chunk_end_sequence=4,
            is_partial=False,
            is_sealed=False,
            status="live_processing",
            processing_run_id=11,
        ),
        SimpleNamespace(
            id=2,
            window_index=1,
            chunk_end_sequence=4,
            is_partial=False,
            is_sealed=False,
            status="live_processing",
            processing_run_id=12,
        ),
        SimpleNamespace(
            id=3,
            window_index=2,
            chunk_end_sequence=4,
            is_partial=False,
            is_sealed=False,
            status="failed",
            processing_run_id=None,
        ),
    ]

    class _ExecResult:
        def all(self):
            return []

    class _Session:
        def exec(self, *args, **kwargs):
            return _ExecResult()

    monkeypatch.setattr(
        "backend.processing.live_transcribe._load_recording_audio_window_manifests",
        lambda session, recording_id: manifest_rows,
    )

    selected_rows = _select_live_rolling_diarization_manifests(
        _Session(),
        recording_id=1,
        up_to_sequence=4,
        config_hash="rolling-cfg-1",
        max_windows_per_pass=3,
        processing_run_status_by_id={11: "running", 12: "failed"},
    )

    assert [row.window_index for row in selected_rows] == [1, 2]


def test_select_live_rolling_diarization_manifests_claims_live_processed_without_window_result(
    monkeypatch,
):
    from types import SimpleNamespace

    from backend.processing.live_transcribe import _select_live_rolling_diarization_manifests

    manifest_rows = [
        SimpleNamespace(
            id=1,
            window_index=0,
            chunk_end_sequence=10,
            is_partial=False,
            is_sealed=False,
            status="live_processed",
            processing_run_id=None,
        )
    ]

    class _ExecResult:
        def all(self):
            return []

    class _Session:
        def exec(self, *args, **kwargs):
            return _ExecResult()

    monkeypatch.setattr(
        "backend.processing.live_transcribe._load_recording_audio_window_manifests",
        lambda session, recording_id: manifest_rows,
    )

    selected_rows = _select_live_rolling_diarization_manifests(
        _Session(),
        recording_id=1,
        up_to_sequence=10,
        config_hash="rolling-cfg-1",
        max_windows_per_pass=3,
    )

    assert [row.window_index for row in selected_rows] == [0]


def test_claim_live_rolling_diarization_manifests_marks_rows_in_flight(monkeypatch):
    from types import SimpleNamespace

    from backend.processing.live_transcribe import _claim_live_rolling_diarization_manifests

    manifest_rows = [
        SimpleNamespace(
            id=10,
            window_index=0,
            chunk_end_sequence=4,
            is_partial=False,
            is_sealed=False,
            status="pending",
            processing_run_id=None,
            last_error="old",
        ),
        SimpleNamespace(
            id=11,
            window_index=1,
            chunk_end_sequence=4,
            is_partial=False,
            is_sealed=False,
            status="pending",
            processing_run_id=None,
            last_error=None,
        ),
    ]

    class _ExecResult:
        def all(self):
            return []

    class _Session:
        def __init__(self):
            self.added = []

        def exec(self, *args, **kwargs):
            return _ExecResult()

        def add(self, row):
            self.added.append(row)

    monkeypatch.setattr(
        "backend.processing.live_transcribe._load_lockable_live_rolling_diarization_manifests",
        lambda session, recording_id: manifest_rows,
    )

    session = _Session()
    claimed_rows = _claim_live_rolling_diarization_manifests(
        session,
        recording_id=1,
        up_to_sequence=4,
        config_hash="rolling-cfg-1",
        max_windows_per_pass=1,
        processing_run_id=77,
    )

    assert [row.window_index for row in claimed_rows] == [0]
    assert manifest_rows[0].status == "live_processing"
    assert manifest_rows[0].processing_run_id == 77
    assert manifest_rows[0].last_error is None
    assert manifest_rows[1].status == "pending"


def test_count_active_live_rolling_diarization_runs_ignores_stale_rows():
    from backend.processing.live_transcribe import _count_active_live_rolling_diarization_runs

    class _ExecResult:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return list(self._rows)

    class _Session:
        def exec(self, *args, **kwargs):
            return _ExecResult([(1,), (2,)])

    assert _count_active_live_rolling_diarization_runs(_Session()) == 2


def test_count_active_live_rolling_diarization_runs_rolls_back_failed_query():
    from backend.processing.live_transcribe import _count_active_live_rolling_diarization_runs

    class _Session:
        def __init__(self):
            self.rolled_back = False

        def exec(self, *args, **kwargs):
            raise RuntimeError("enum value missing")

        def rollback(self):
            self.rolled_back = True

    session = _Session()

    assert _count_active_live_rolling_diarization_runs(session) == 0
    assert session.rolled_back is True


def test_build_diarization_window_payload_offsets_turns_to_absolute_time():
    from backend.worker.tasks import _build_diarization_window_payload

    class _Segment:
        def __init__(self, start: float, end: float):
            self.start = start
            self.end = end

    class _Annotation:
        def itertracks(self, yield_label=False):
            assert yield_label is True
            yield _Segment(0.2, 0.6), "A", "SPEAKER_00"
            yield _Segment(0.8, 1.3), "B", "SPEAKER_01"

    payload, turns = _build_diarization_window_payload(
        _Annotation(),
        window_start_ms=5000,
        window_end_ms=7000,
    )

    assert payload == {
        "window_start_ms": 5000,
        "window_end_ms": 7000,
        "speaker_labels": ["SPEAKER_00", "SPEAKER_01"],
        "turn_count": 2,
        "turns": [
            {
                "local_speaker_key": "SPEAKER_00",
                "start_ms": 5200,
                "end_ms": 5600,
                "track": "A",
            },
            {
                "local_speaker_key": "SPEAKER_01",
                "start_ms": 5800,
                "end_ms": 6300,
                "track": "B",
            },
        ],
    }
    assert turns == payload["turns"]


def test_resolve_live_speaker_reuses_fallback_without_embedding(monkeypatch):
    """Short/embedding-less live regions reuse the last stable speaker."""
    from types import SimpleNamespace

    from backend.processing.live_transcribe import _resolve_live_speaker

    class _ExecResult:
        def all(self):
            return [
                SimpleNamespace(diarization_label="LIVE_01", embedding=[0.1]),
                SimpleNamespace(diarization_label="LIVE_02", embedding=[0.2]),
            ]

    class _Session:
        def exec(self, *a, **k):
            return _ExecResult()

    monkeypatch.setattr(
        "soundfile.info",
        lambda path: SimpleNamespace(frames=1600, samplerate=16000),
    )

    label = _resolve_live_speaker(
        session=_Session(),
        recording_id=42,
        user_id=None,
        audio_path="/tmp/short.wav",
        merged_config={},
        fallback_label="LIVE_02",
    )

    assert label == "LIVE_02"


def test_resolve_live_speaker_soft_matches_existing_label(monkeypatch):
    """Medium-confidence live embeddings reuse an existing speaker label."""
    from types import SimpleNamespace

    from backend.processing.live_transcribe import _resolve_live_speaker

    live_speaker = SimpleNamespace(
        diarization_label="LIVE_01",
        embedding=[0.1, 0.2],
        global_speaker_id=None,
    )

    class _ExecResult:
        def all(self):
            return [live_speaker]

    class _Session:
        def __init__(self):
            self.added = []

        def exec(self, *a, **k):
            return _ExecResult()

        def add(self, obj):
            self.added.append(obj)

        def flush(self):
            pass

    monkeypatch.setattr(
        "soundfile.info",
        lambda path: SimpleNamespace(frames=48000, samplerate=16000),
    )
    monkeypatch.setattr(
        "backend.processing.embedding_core.extract_embedding_for_segments",
        lambda *a, **k: [0.3, 0.4],
    )
    monkeypatch.setattr(
        "backend.processing.embedding.cosine_similarity",
        lambda *a, **k: 0.5,
    )
    monkeypatch.setattr(
        "backend.processing.embedding.merge_embeddings",
        lambda *a, **k: [0.2, 0.3],
    )

    session = _Session()
    label = _resolve_live_speaker(
        session=session,
        recording_id=42,
        user_id=None,
        audio_path="/tmp/voice.wav",
        merged_config={},
    )

    assert label == "LIVE_01"
    assert live_speaker.embedding == [0.2, 0.3]
    assert session.added == [live_speaker]


def test_analyze_window_speakers_prefers_clean_non_overlapping_spans(monkeypatch):
    from types import SimpleNamespace

    from backend.utils.rolling_diarization import analyze_window_speakers

    captured_segments = []

    class _DiarizationResult:
        def itertracks(self, yield_label=False):
            turns = [
                (SimpleNamespace(start=0.0, end=0.8), "A", "SPEAKER_00"),
                (SimpleNamespace(start=0.3, end=0.9), "B", "SPEAKER_01"),
                (SimpleNamespace(start=1.0, end=2.2), "A", "SPEAKER_00"),
            ]
            return iter(turns)

    monkeypatch.setattr(
        "backend.processing.embedding_core.extract_embedding_for_segments",
        lambda _audio_path, segments, **_kwargs: (
            captured_segments.append(list(segments)) or [0.3, 0.4]
        ),
    )

    metadata_by_key, embeddings_by_key = analyze_window_speakers(
        diarization_result=_DiarizationResult(),
        audio_path="/tmp/window.wav",
        device_str="cpu",
        hf_token=None,
        recording_speakers=[],
        global_speakers=[],
        window_start_ms=5_000,
    )

    assert captured_segments == [[(1.0, 2.2)]]
    assert embeddings_by_key["SPEAKER_00"] == [0.3, 0.4]
    assert metadata_by_key["SPEAKER_00"]["clean_segment_count"] == 1
    assert metadata_by_key["SPEAKER_00"]["source_spans_ms"] == [
        {"start_ms": 6_000, "end_ms": 7_200}
    ]
    assert metadata_by_key["SPEAKER_01"]["embedding_available"] is False
    assert metadata_by_key["SPEAKER_01"]["clean_segment_count"] == 0


def test_apply_live_voiceprint_learning_respects_locked_global_voiceprints(monkeypatch):
    from types import SimpleNamespace

    from backend.processing.live_transcribe import _apply_live_voiceprint_learning

    recording_speaker = SimpleNamespace(
        id=7,
        embedding=[0.1, 0.2],
        global_speaker_id=9,
        merged_into_id=None,
        identity_confidence=0.2,
    )
    global_speaker = SimpleNamespace(
        id=9,
        embedding=[0.6, 0.7],
        is_voiceprint_locked=True,
    )
    window_result = SimpleNamespace(
        id=12,
        raw_payload={
            "speaker_metadata": {
                "SPEAKER_00": {
                    "matched_recording_speaker_id": 7,
                    "best_global_speaker_id": 9,
                    "best_global_speaker_score": 0.96,
                    "match_confidence": 0.82,
                    "clean_duration_ms": 2_200,
                    "clean_segment_count": 2,
                    "source_spans_ms": [{"start_ms": 1_000, "end_ms": 3_200}],
                }
            }
        },
    )

    class _Session:
        def __init__(self):
            self.added = []

        def get(self, model, entity_id):
            if model.__name__ == "RecordingSpeaker":
                return recording_speaker if entity_id == 7 else None
            if model.__name__ == "GlobalSpeaker":
                return global_speaker if entity_id == 9 else None
            return None

        def add(self, obj):
            self.added.append(obj)

    recorded_metrics = []
    monkeypatch.setattr(
        "backend.processing.live_transcribe.record_pipeline_metric",
        lambda *args, **kwargs: recorded_metrics.append((args, kwargs)),
    )
    monkeypatch.setattr(
        "backend.processing.embedding.cosine_similarity",
        lambda *args, **kwargs: 0.9,
    )
    monkeypatch.setattr(
        "backend.processing.embedding.merge_embeddings",
        lambda *_args, **_kwargs: [0.2, 0.3],
    )

    summary = _apply_live_voiceprint_learning(
        session=_Session(),
        recording_id=42,
        window_result=window_result,
        speaker_embeddings_by_key={"SPEAKER_00": [0.3, 0.4]},
    )

    assert summary == {
        "recording_speaker_update_count": 1,
        "global_speaker_update_count": 0,
    }
    assert recording_speaker.embedding == [0.2, 0.3]
    assert recording_speaker.identity_confidence == 0.96
    assert global_speaker.embedding == [0.6, 0.7]

    voiceprint_update = window_result.raw_payload["speaker_metadata"]["SPEAKER_00"][
        "voiceprint_update"
    ]
    assert voiceprint_update["applied"] is True
    assert voiceprint_update["global_applied"] is False
    assert voiceprint_update["global_reason"] == "global_voiceprint_locked"
    assert voiceprint_update["source_spans_ms"] == [{"start_ms": 1_000, "end_ms": 3_200}]
    assert recorded_metrics


def test_apply_live_voiceprint_learning_rejects_low_similarity_updates(monkeypatch):
    from types import SimpleNamespace

    from backend.processing.live_transcribe import _apply_live_voiceprint_learning

    recording_speaker = SimpleNamespace(
        id=7,
        embedding=[0.1, 0.2],
        global_speaker_id=None,
        merged_into_id=None,
        identity_confidence=0.3,
    )
    window_result = SimpleNamespace(
        id=13,
        raw_payload={
            "speaker_metadata": {
                "SPEAKER_00": {
                    "matched_recording_speaker_id": 7,
                    "match_confidence": 0.76,
                    "clean_duration_ms": 2_400,
                    "clean_segment_count": 2,
                    "source_spans_ms": [{"start_ms": 2_000, "end_ms": 4_400}],
                }
            }
        },
    )

    class _Session:
        def get(self, model, entity_id):
            if model.__name__ == "RecordingSpeaker" and entity_id == 7:
                return recording_speaker
            return None

        def add(self, _obj):
            return None

    monkeypatch.setattr(
        "backend.processing.live_transcribe.record_pipeline_metric",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "backend.processing.embedding.cosine_similarity",
        lambda *args, **kwargs: 0.2,
    )

    summary = _apply_live_voiceprint_learning(
        session=_Session(),
        recording_id=99,
        window_result=window_result,
        speaker_embeddings_by_key={"SPEAKER_00": [0.4, 0.5]},
    )

    assert summary == {
        "recording_speaker_update_count": 0,
        "global_speaker_update_count": 0,
    }
    assert recording_speaker.embedding == [0.1, 0.2]
    assert recording_speaker.identity_confidence == 0.3
    assert window_result.raw_payload["speaker_metadata"]["SPEAKER_00"][
        "voiceprint_update"
    ]["reason"] == "recording_drift_guard_rejected"


def _patch_live_deps(
    monkeypatch,
    *,
    speech_map,
    transcribe_text="hello",
    speaker_label="LIVE_01",
):
    """Patch torch-free fakes for vad / transcribe / DB used by the live task.

    speech_map: callable(combined_tensor) -> list[{"start", "end"}].
    """
    import silero_vad

    from backend.processing import live_transcribe as lt
    from backend.processing import vad as vad_module
    from backend.processing import transcribe as transcribe_module

    audio_store = _FakeAudioStore()

    monkeypatch.setattr(vad_module, "detect_speech_segments", lambda audio, *a, **k: speech_map(audio))
    monkeypatch.setattr(vad_module, "safe_read_audio", audio_store.read_audio)
    monkeypatch.setattr(silero_vad, "save_audio", audio_store.save_audio)
    monkeypatch.setattr(lt, "_resolve_live_speaker", lambda **kwargs: speaker_label)
    monkeypatch.setattr(lt, "is_meeting_edge_enabled", lambda user_settings=None: False)

    def fake_transcribe_audio(path, config=None):
        # A single segment spanning the whole clip: with no `words`, the
        # midpoint heuristic in _extract_region_text keeps it for any
        # prefix_s, so context-window runs still emit text.
        return {
            "text": transcribe_text,
            "language": None,
            "segments": [
                {"start": 0.0, "end": 1.0e9, "text": transcribe_text}
            ],
        }

    monkeypatch.setattr(transcribe_module, "transcribe_audio", fake_transcribe_audio)
    return audio_store


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

    from backend.processing import live_transcribe as lt
    from backend.processing.live_transcribe import transcribe_segment_live_task

    monkeypatch.setattr(db_module, "get_sync_session", lambda: fake_session)
    monkeypatch.setattr(attributes, "flag_modified", lambda obj, key: None)
    monkeypatch.setattr(lt.config_manager, "reload", lambda: None)
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

    audio_store = _patch_live_deps(monkeypatch, speech_map=speech_map)

    transcript = _FakeTranscript()
    recording = _FakeRecording(RecordingStatus.UPLOADING, transcript)
    session = _FakeSession(recording)

    # Create each segment just-in-time so each run drains a single segment.
    for seq in (1, 2, 3):
        _make_segment_wav(temp_dir, seq, 2.0, audio_store)
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

    audio_store = _patch_live_deps(monkeypatch, speech_map=speech_map)

    transcript = _FakeTranscript()
    recording = _FakeRecording(RecordingStatus.UPLOADING, transcript)
    session = _FakeSession(recording)

    # seq=2 arrives first; only its file exists.
    _make_segment_wav(temp_dir, 2, 2.0, audio_store)
    _run_live_task(monkeypatch, 7, 2, session)
    assert transcript.segments == []
    assert lt.read_live_state(temp_dir / "live")["next_expected"] == 1

    # seq=1 arrives; both files now present -> drains [1, 2].
    _make_segment_wav(temp_dir, 1, 2.0, audio_store)
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

    audio_store = _patch_live_deps(monkeypatch, speech_map=speech_map)

    transcript = _FakeTranscript()
    recording = _FakeRecording(RecordingStatus.UPLOADING, transcript)
    session = _FakeSession(recording)

    _make_segment_wav(temp_dir, 1, 3.0, audio_store)
    _run_live_task(monkeypatch, 9, 1, session)
    # Run 1 carried the trailing utterance: no completed segment yet.
    assert transcript.segments == []
    assert (temp_dir / "live" / "buffer.wav").exists()

    _make_segment_wav(temp_dir, 2, 3.0, audio_store)
    _run_live_task(monkeypatch, 9, 2, session)
    # Run 2 completes exactly one provisional segment.
    assert len(transcript.segments) == 1
    assert transcript.segments[0]["provisional"] is True


def test_live_forced_cut(monkeypatch, tmp_path):
    """A >30 s continuous speech region is emitted as hard-capped provisional chunks."""
    from backend.models.recording import RecordingStatus
    from backend.processing import live_transcribe as lt

    temp_dir = tmp_path / "11"
    temp_dir.mkdir()
    audio_store = _patch_live_deps(monkeypatch, speech_map=lambda audio: [{"start": 0.0, "end": 31.0}])
    _make_segment_wav(temp_dir, 1, 31.0, audio_store)

    monkeypatch.setattr(lt, "recording_upload_temp_dir", lambda rid, create=False: temp_dir)

    transcript = _FakeTranscript()
    recording = _FakeRecording(RecordingStatus.UPLOADING, transcript)
    session = _FakeSession(recording)

    _run_live_task(monkeypatch, 11, 1, session)

    assert len(transcript.segments) == 2
    assert transcript.segments[0]["start"] == 0.0
    assert transcript.segments[0]["end"] == 20.0
    assert transcript.segments[1]["start"] == 20.0
    assert transcript.segments[1]["end"] == 31.0


def test_live_task_skips_meeting_edge_dispatch_when_disabled(monkeypatch, tmp_path):
    """Live provisional transcript writes do not enqueue Meeting Edge when disabled."""
    from types import SimpleNamespace

    import backend.worker.tasks as tasks_module
    from backend.models.recording import RecordingStatus
    from backend.processing import live_transcribe as lt

    temp_dir = tmp_path / "15"
    temp_dir.mkdir()

    monkeypatch.setattr(lt, "recording_upload_temp_dir", lambda rid, create=False: temp_dir)

    def speech_map(audio):
        return [{"start": 0.0, "end": 1.0}]

    audio_store = _patch_live_deps(monkeypatch, speech_map=speech_map)

    dispatched: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        tasks_module,
        "refresh_meeting_edge_task",
        SimpleNamespace(delay=lambda *args, **kwargs: dispatched.append((args, kwargs))),
    )

    transcript = _FakeTranscript()
    recording = _FakeRecording(RecordingStatus.UPLOADING, transcript)
    recording.user = SimpleNamespace(settings={"enable_meeting_edge": False})
    session = _FakeSession(recording)

    _make_segment_wav(temp_dir, 1, 2.0, audio_store)
    _run_live_task(monkeypatch, 15, 1, session)

    assert len(transcript.segments) == 1
    assert dispatched == []


def test_live_entry_guard_bails_when_not_uploading(monkeypatch, tmp_path):
    """A task whose recording is no longer UPLOADING stops at entry: no DB
    write, no work, and no orphan live dir recreated after finalize()."""
    from backend.models.recording import RecordingStatus
    from backend.processing import live_transcribe as lt

    temp_dir = tmp_path / "13"
    temp_dir.mkdir()
    audio_store = _patch_live_deps(monkeypatch, speech_map=lambda audio: [{"start": 0.0, "end": 1.0}])
    _make_segment_wav(temp_dir, 1, 2.0, audio_store)

    monkeypatch.setattr(lt, "recording_upload_temp_dir", lambda rid, create=False: temp_dir)

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
    audio_store = _patch_live_deps(monkeypatch, speech_map=lambda audio: [{"start": 0.0, "end": 1.0}])
    _make_segment_wav(temp_dir, 1, 2.0, audio_store)

    monkeypatch.setattr(lt, "recording_upload_temp_dir", lambda rid, create=False: temp_dir)

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
    audio_store = _FakeAudioStore()
    _make_segment_wav(temp_dir, 1, 2.0, audio_store)

    monkeypatch.setattr(lt, "recording_upload_temp_dir", lambda rid, create=False: temp_dir)
    monkeypatch.setattr(
        vad_module, "detect_speech_segments", lambda audio, *a, **k: [{"start": 0.0, "end": 1.0}]
    )

    import silero_vad

    monkeypatch.setattr(vad_module, "safe_read_audio", audio_store.read_audio)
    monkeypatch.setattr(silero_vad, "save_audio", audio_store.save_audio)
    monkeypatch.setattr(lt, "_resolve_live_speaker", lambda **kwargs: "LIVE_01")

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


# --- _extract_region_text unit tests ----------------------------------------


def test_extract_region_text_drops_pure_context_segment():
    """A segment entirely within the prefix run-up is dropped."""
    from backend.processing.live_transcribe import _extract_region_text

    result = {
        "segments": [
            {"start": 0.0, "end": 1.5, "text": "context only"},
            {"start": 2.1, "end": 3.0, "text": "region words"},
        ]
    }
    assert _extract_region_text(result, prefix_s=2.0) == "region words"


def test_extract_region_text_keeps_region_segment():
    """A segment entirely after the prefix is kept whole."""
    from backend.processing.live_transcribe import _extract_region_text

    result = {"segments": [{"start": 2.5, "end": 4.0, "text": "the region"}]}
    assert _extract_region_text(result, prefix_s=2.0) == "the region"


def test_extract_region_text_straddle_with_words():
    """A boundary-straddling segment with words keeps only post-prefix words."""
    from backend.processing.live_transcribe import _extract_region_text

    result = {
        "segments": [
            {
                "start": 1.0,
                "end": 3.0,
                "text": "before before after after",
                "words": [
                    {"start": 1.0, "end": 1.5, "word": "before"},
                    {"start": 1.5, "end": 2.0, "word": "before"},
                    {"start": 2.2, "end": 2.6, "word": "after"},
                    {"start": 2.6, "end": 3.0, "word": "after"},
                ],
            }
        ]
    }
    assert _extract_region_text(result, prefix_s=2.0) == "after after"


def test_extract_region_segment_payloads_and_confidence_payload_keep_word_timestamps():
    from backend.processing.live_transcribe import (
        _build_live_confidence_payload,
        _extract_region_segment_payloads,
    )

    result = {
        "segments": [
            {
                "start": 1.0,
                "end": 3.0,
                "text": "before before after after",
                "words": [
                    {"start": 1.0, "end": 1.5, "word": "before"},
                    {"start": 1.5, "end": 2.0, "word": "before"},
                    {"start": 2.2, "end": 2.6, "word": "after"},
                    {"start": 2.6, "end": 3.0, "word": "after"},
                ],
            },
            {
                "start": 3.2,
                "end": 4.0,
                "text": "tail",
                "words": [
                    {"start": 3.2, "end": 3.5, "word": "tail"},
                ],
            },
        ]
    }

    region_segments = _extract_region_segment_payloads(result, prefix_s=2.0)

    assert len(region_segments) == 2
    assert region_segments[0]["text"] == "after after"
    assert region_segments[0]["start"] == pytest.approx(0.2)
    assert region_segments[0]["end"] == pytest.approx(1.0)
    assert region_segments[0]["words"] == [
        {"start": pytest.approx(0.2), "end": pytest.approx(0.6), "word": "after"},
        {"start": pytest.approx(0.6), "end": pytest.approx(1.0), "word": "after"},
    ]
    assert region_segments[1]["text"] == "tail"
    assert region_segments[1]["start"] == pytest.approx(1.2)
    assert region_segments[1]["end"] == pytest.approx(2.0)
    assert region_segments[1]["words"] == [
        {"start": pytest.approx(1.2), "end": pytest.approx(1.5), "word": "tail"},
    ]

    confidence_payload = _build_live_confidence_payload(
        region_segment_payloads=region_segments,
        region_start_ms=5_000,
        region_end_ms=7_000,
    )

    assert confidence_payload == {
        "utterance_start_ms": 5_000,
        "utterance_end_ms": 7_000,
        "asr_segments": [
            {
                "start_ms": 5_200,
                "end_ms": 6_000,
                "text": "after after",
                "words": [
                    {"start_ms": 5_200, "end_ms": 5_600, "word": "after"},
                    {"start_ms": 5_600, "end_ms": 6_000, "word": "after"},
                ],
            },
            {
                "start_ms": 6_200,
                "end_ms": 7_000,
                "text": "tail",
                "words": [
                    {"start_ms": 6_200, "end_ms": 6_500, "word": "tail"},
                ],
            },
        ],
        "asr_word_timestamps_available": True,
    }


def test_extract_region_text_straddle_without_words_midpoint():
    """A boundary-straddling segment without words uses the midpoint heuristic."""
    from backend.processing.live_transcribe import _extract_region_text

    # Midpoint 2.5 >= prefix 2.0 -> kept.
    kept = {"segments": [{"start": 1.0, "end": 4.0, "text": "kept"}]}
    assert _extract_region_text(kept, prefix_s=2.0) == "kept"

    # Midpoint 1.5 < prefix 2.0 -> dropped.
    dropped = {"segments": [{"start": 1.0, "end": 2.0, "text": "dropped"}]}
    assert _extract_region_text(dropped, prefix_s=2.0) == ""


def test_extract_region_text_prefix_zero_keeps_all():
    """With prefix_s == 0, all segments belong to the region."""
    from backend.processing.live_transcribe import _extract_region_text

    result = {
        "segments": [
            {"start": 0.0, "end": 1.0, "text": "one"},
            {"start": 1.0, "end": 2.0, "text": "two"},
        ]
    }
    assert _extract_region_text(result, prefix_s=0.0) == "one two"


def test_extract_region_text_no_segments_falls_back_to_text():
    """Without segments, the top-level text is used only when prefix_s <= 0."""
    from backend.processing.live_transcribe import _extract_region_text

    assert _extract_region_text({"text": "plain"}, prefix_s=0.0) == "plain"
    assert _extract_region_text({"text": "plain"}, prefix_s=1.5) == ""


# --- _strip_repetition unit tests --------------------------------------------


def test_strip_repetition_collapses_consecutive_words():
    """Three or more identical consecutive words collapse to one."""
    from backend.processing.live_transcribe import _strip_repetition

    assert _strip_repetition("yes yes yes please") == "yes please"


def test_strip_repetition_collapses_repeated_phrase():
    """A short phrase repeated three or more times collapses to one."""
    from backend.processing.live_transcribe import _strip_repetition

    assert (
        _strip_repetition("thank you thank you thank you bye")
        == "thank you bye"
    )


def test_strip_repetition_leaves_legitimate_text_untouched():
    """Ordinary text, including a single doubled word, is preserved."""
    from backend.processing.live_transcribe import _strip_repetition

    text = "the meeting starts at noon and we will review the budget"
    assert _strip_repetition(text) == text
    # A word repeated only twice is not collapsed.
    assert _strip_repetition("very very good") == "very very good"


# --- context.wav lifecycle test ---------------------------------------------


def test_live_writes_context_wav_and_excludes_prefix_text(monkeypatch, tmp_path):
    """A run writes live/context.wav, and a region's left-context prefix text
    is excluded from the emitted provisional segment."""
    from backend.models.recording import RecordingStatus
    from backend.processing import live_transcribe as lt
    from backend.processing import vad as vad_module
    from backend.processing import transcribe as transcribe_module

    temp_dir = tmp_path / "21"
    temp_dir.mkdir()

    monkeypatch.setattr(lt, "recording_upload_temp_dir", lambda rid, create=False: temp_dir)
    monkeypatch.setattr(
        vad_module, "detect_speech_segments",
        lambda audio, *a, **k: [{"start": 1.0, "end": 2.0}],
    )

    # The clip handed to the engine is `left_context ++ region`. A 1.0 s
    # context.wav plus the 1.0 s of `combined` preceding the region gives
    # prefix_s == 2.0. The fake result carries a context-prefixed segment
    # (PREFIX, ending inside the prefix) and a region segment (REGION); only
    # the region text must survive _extract_region_text.
    def fake_transcribe_audio(path, config=None):
        return {
            "text": "PREFIX REGION",
            "language": None,
            "segments": [
                {"start": 0.0, "end": 0.5, "text": "PREFIX"},
                {"start": 1.5, "end": 2.5, "text": "REGION"},
            ],
        }

    monkeypatch.setattr(transcribe_module, "transcribe_audio", fake_transcribe_audio)

    audio_store = _FakeAudioStore()
    _make_segment_wav(temp_dir, 1, 3.0, audio_store)

    transcript = _FakeTranscript()
    recording = _FakeRecording(RecordingStatus.UPLOADING, transcript)
    session = _FakeSession(recording)

    # Seed a 1.0 s context.wav so prefix_s is non-zero (2.0 s) and prefix
    # exclusion is actually exercised.
    import torch

    ctx = torch.zeros(1, int(1.0 * lt.LIVE_SAMPLE_RATE))
    (temp_dir / "live").mkdir(parents=True, exist_ok=True)
    audio_store.save_audio(
        str(temp_dir / "live" / "context.wav"), ctx, sampling_rate=lt.LIVE_SAMPLE_RATE
    )

    monkeypatch.setattr(vad_module, "safe_read_audio", audio_store.read_audio)
    import silero_vad
    monkeypatch.setattr(silero_vad, "save_audio", audio_store.save_audio)
    monkeypatch.setattr(lt, "_resolve_live_speaker", lambda **kwargs: "LIVE_01")
    monkeypatch.setattr(lt, "is_meeting_edge_enabled", lambda user_settings=None: False)

    _run_live_task(monkeypatch, 21, 1, session)

    # context.wav was rewritten with the consumed audio's tail.
    assert (temp_dir / "live" / "context.wav").exists()
    # One provisional segment emitted; the PREFIX segment (end 0.5 <= prefix
    # 1.0+EPS) is dropped, only REGION survives.
    assert len(transcript.segments) == 1
    assert transcript.segments[0]["text"] == "REGION"
    assert transcript.segments[0]["provisional"] is True


# --- segment endpoint live-task dispatch tests ------------------------------


async def _insert_uploading_recording(
    session_maker: sessionmaker,
    *,
    recording_id: int = 101,
    public_id: str = "live-rec-public-id",
    user_id: int = 1,
    audio_path: str = "/tmp/live.wav",
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
                "audio_path": audio_path,
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

    async with test_session_maker() as session:
        chunk_row = (
            await session.execute(
                text(
                    "SELECT sequence_no, upload_status, byte_size, storage_path FROM recording_audio_chunks WHERE recording_id = 101"
                )
            )
        ).one()
        assert chunk_row[0] == 3
        assert chunk_row[1] == "received"
        assert chunk_row[2] == len(b"fake-wav-bytes")
        assert chunk_row[3] == str(tmp_path / "3.wav")


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


@pytest.mark.anyio
async def test_segment_upload_swallows_metric_failure_after_commit(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    tmp_path,
    monkeypatch,
) -> None:
    """A metrics failure after commit must not roll back or delete a successful upload."""
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

    def boom(*args, **kwargs):
        raise RuntimeError("metrics unavailable")

    monkeypatch.setattr(recordings_module, "record_pipeline_metric", boom)

    response = await client.post(
        "/api/v1/recordings/live-rec-public-id/segment",
        params={"sequence": 7},
        files={"file": ("7.wav", b"fake-wav-bytes", "audio/wav")},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "received", "segment": 7}
    assert (tmp_path / "7.wav").exists()

    async with test_session_maker() as session:
        chunk_row = (
            await session.execute(
                text(
                    "SELECT sequence_no, upload_status, storage_path FROM recording_audio_chunks WHERE recording_id = 101"
                )
            )
        ).one()

        assert chunk_row[0] == 7
        assert chunk_row[1] == "received"
        assert chunk_row[2] == str(tmp_path / "7.wav")


@pytest.mark.anyio
async def test_segment_upload_retry_reuses_existing_chunk_row(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    tmp_path,
    monkeypatch,
) -> None:
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

    first_response = await client.post(
        "/api/v1/recordings/live-rec-public-id/segment",
        params={"sequence": 3},
        files={"file": ("3.wav", b"fake-wav-bytes", "audio/wav")},
    )
    second_response = await client.post(
        "/api/v1/recordings/live-rec-public-id/segment",
        params={"sequence": 3},
        files={"file": ("3.wav", b"fake-wav-bytes", "audio/wav")},
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200

    async with test_session_maker() as session:
        row = (
            await session.execute(
                text(
                    "SELECT COUNT(*), MIN(sequence_no), MAX(sequence_no) FROM recording_audio_chunks WHERE recording_id = 101"
                )
            )
        ).one()

        assert row[0] == 1
        assert row[1] == 3
        assert row[2] == 3


@pytest.mark.anyio
async def test_segment_upload_persists_chunk_metadata_and_finalize_seals_window_manifest_and_defers_cleanup(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    tmp_path,
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    from backend.api.v1.endpoints import recordings as recordings_module

    final_audio_path = tmp_path / "final.wav"
    upload_dir = tmp_path / "upload"
    upload_dir.mkdir(parents=True, exist_ok=True)
    await _insert_uploading_recording(
        test_session_maker,
        recording_id=101,
        audio_path=str(final_audio_path),
    )

    monkeypatch.setattr(
        recordings_module, "recording_upload_temp_dir", lambda *a, **k: upload_dir
    )
    monkeypatch.setattr(
        recordings_module.config_manager,
        "get",
        lambda key, default=None: False if key == "enable_live_transcription" else default,
    )

    concatenated_paths: list[list[str]] = []

    def fake_concatenate_wavs(paths: list[str], destination: str) -> None:
        concatenated_paths.append(list(paths))
        Path(destination).write_bytes(b"joined-audio")

    monkeypatch.setattr(recordings_module, "concatenate_wavs", fake_concatenate_wavs)
    monkeypatch.setattr(recordings_module, "get_audio_duration", lambda path: 1.25)
    monkeypatch.setattr(
        recordings_module.process_recording_task,
        "delay",
        lambda recording_id: SimpleNamespace(id=f"task-{recording_id}"),
    )
    monkeypatch.setattr(recordings_module.generate_proxy_task, "delay", lambda *args, **kwargs: None)

    wav_bytes = _make_wav_bytes(duration_s=0.5, sample_rate=16000)
    upload_response = await client.post(
        "/api/v1/recordings/live-rec-public-id/segment",
        params={"sequence": 0},
        files={"file": ("0.wav", wav_bytes, "audio/wav")},
    )

    assert upload_response.status_code == 200

    finalize_response = await client.post("/api/v1/recordings/live-rec-public-id/finalize")

    assert finalize_response.status_code == 200
    assert concatenated_paths == [[str(upload_dir / "0.wav")]]
    assert upload_dir.exists()
    assert (upload_dir / "0.wav").exists()

    async with test_session_maker() as session:
        chunk_row = (
            await session.execute(
                text(
                    "SELECT sample_rate_hz, channel_count, duration_ms, sha256, upload_status, cleanup_eligible_at "
                    "FROM recording_audio_chunks WHERE recording_id = 101 AND sequence_no = 0"
                )
            )
        ).one()
        manifest_row = (
            await session.execute(
                text(
                    "SELECT window_index, window_start_ms, window_end_ms, status, is_partial, is_sealed "
                    "FROM recording_audio_window_manifests WHERE recording_id = 101 ORDER BY window_index"
                )
            )
        ).one()
        recording_row = (
            await session.execute(
                text(
                    "SELECT status, file_size_bytes, celery_task_id FROM recordings WHERE id = 101"
                )
            )
        ).one()

    assert chunk_row[0] == 16000
    assert chunk_row[1] == 1
    assert chunk_row[2] == 500
    assert chunk_row[3] == hashlib.sha256(wav_bytes).hexdigest()
    assert chunk_row[4] == "received"
    assert chunk_row[5] is None
    assert manifest_row == (0, 0, 500, "pending", 1, 1)
    assert recording_row[0] == "QUEUED"
    assert recording_row[1] == len(b"joined-audio")
    assert recording_row[2] == "task-101"


@pytest.mark.anyio
async def test_finalize_upload_rejects_missing_chunk_sequences(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    tmp_path,
    monkeypatch,
) -> None:
    from backend.api.v1.endpoints import recordings as recordings_module

    final_audio_path = tmp_path / "final.wav"
    upload_dir = tmp_path / "upload"
    upload_dir.mkdir(parents=True, exist_ok=True)
    await _insert_uploading_recording(
        test_session_maker,
        recording_id=101,
        audio_path=str(final_audio_path),
    )

    monkeypatch.setattr(
        recordings_module, "recording_upload_temp_dir", lambda *a, **k: upload_dir
    )
    monkeypatch.setattr(
        recordings_module.config_manager,
        "get",
        lambda key, default=None: False if key == "enable_live_transcription" else default,
    )

    first_response = await client.post(
        "/api/v1/recordings/live-rec-public-id/segment",
        params={"sequence": 0},
        files={"file": ("0.wav", _make_wav_bytes(duration_s=0.5), "audio/wav")},
    )
    second_response = await client.post(
        "/api/v1/recordings/live-rec-public-id/segment",
        params={"sequence": 2},
        files={"file": ("2.wav", _make_wav_bytes(duration_s=0.5), "audio/wav")},
    )
    finalize_response = await client.post("/api/v1/recordings/live-rec-public-id/finalize")

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert finalize_response.status_code == 409
    assert finalize_response.json()["detail"] == (
        "Recording upload is still in progress; finalize after all segment uploads complete."
    )


@pytest.mark.anyio
async def test_finalize_upload_stops_when_recording_is_cancelled_mid_finalize(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    tmp_path,
    monkeypatch,
) -> None:
    from backend.api.v1.endpoints import recordings as recordings_module

    final_audio_path = tmp_path / "final.wav"
    upload_dir = tmp_path / "upload"
    upload_dir.mkdir(parents=True, exist_ok=True)
    await _insert_uploading_recording(
        test_session_maker,
        recording_id=101,
        audio_path=str(final_audio_path),
    )

    monkeypatch.setattr(
        recordings_module, "recording_upload_temp_dir", lambda *a, **k: upload_dir
    )
    monkeypatch.setattr(
        recordings_module.config_manager,
        "get",
        lambda key, default=None: False if key == "enable_live_transcription" else default,
    )

    concatenated_paths: list[list[str]] = []
    queued_recordings: list[int] = []
    cancel_during_finalize = False

    def fake_concatenate_wavs(paths: list[str], destination: str) -> None:
        concatenated_paths.append(list(paths))
        Path(destination).write_bytes(b"joined-audio")

    real_list_recording_audio_chunks = recordings_module._list_recording_audio_chunks

    async def fake_list_recording_audio_chunks(*args, **kwargs):
        db = args[0]
        rows = await real_list_recording_audio_chunks(*args, **kwargs)
        if not cancel_during_finalize:
            return rows
        await db.execute(
            text(
                "UPDATE recordings SET status = 'CANCELLED', processing_step = 'Cancelled by user' WHERE id = :recording_id"
            ),
            {
                "recording_id": (
                    kwargs["recording_id"] if "recording_id" in kwargs else args[1]
                )
            },
        )
        await db.commit()
        return rows

    monkeypatch.setattr(recordings_module, "concatenate_wavs", fake_concatenate_wavs)
    monkeypatch.setattr(recordings_module, "get_audio_duration", lambda path: 1.25)
    monkeypatch.setattr(
        recordings_module,
        "_list_recording_audio_chunks",
        fake_list_recording_audio_chunks,
    )
    monkeypatch.setattr(
        recordings_module.process_recording_task,
        "delay",
        lambda recording_id: queued_recordings.append(recording_id),
    )
    monkeypatch.setattr(recordings_module.generate_proxy_task, "delay", lambda *args, **kwargs: None)

    wav_bytes = _make_wav_bytes(duration_s=0.5, sample_rate=16000)
    upload_response = await client.post(
        "/api/v1/recordings/live-rec-public-id/segment",
        params={"sequence": 0},
        files={"file": ("0.wav", wav_bytes, "audio/wav")},
    )
    cancel_during_finalize = True
    finalize_response = await client.post("/api/v1/recordings/live-rec-public-id/finalize")

    assert upload_response.status_code == 200
    assert finalize_response.status_code == 409
    assert finalize_response.json()["detail"] == (
        "Recording is no longer accepting capture uploads"
    )
    assert queued_recordings == []
    assert concatenated_paths == [[str(upload_dir / "0.wav")]]

    async with test_session_maker() as session:
        recording_row = (
            await session.execute(
                text(
                    "SELECT status, file_size_bytes, celery_task_id FROM recordings WHERE id = 101"
                )
            )
        ).one()

    assert recording_row[0] == "CANCELLED"
    assert recording_row[1] == len(b"joined-audio")
    assert recording_row[2] is None


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
