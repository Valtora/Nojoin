from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import pytest
from sqlalchemy import Boolean, Column, Date, DateTime, JSON, Text, create_engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import Field, SQLModel, Session, select

import backend.core.backup_manager as backup_manager_module
import backend.core.db as db_module
import backend.utils.version as version_utils
from backend.models.chat import ChatMessage  # noqa: F401
from backend.models.context_chunk import ContextChunk  # noqa: F401
from backend.models.document import Document  # noqa: F401
from backend.models.invitation import Invitation  # noqa: F401
from backend.models.speaker import RecordingSpeaker  # noqa: F401
from backend.models.tag import RecordingTag  # noqa: F401
from backend.models.transcript import Transcript  # noqa: F401
from backend.core.backup_manager import BackupManager
from backend.core.encryption import decrypt_secret, encrypt_secret
from backend.utils.time import utc_now


class TestBase(SQLModel):
    __test__ = False

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utc_now, sa_type=DateTime, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=DateTime, nullable=False)


class TestUser(TestBase, table=True):
    __tablename__ = "backup_test_users"

    username: str
    hashed_password: str
    is_active: bool = True
    is_superuser: bool = False
    force_password_change: bool = False
    role: str = "user"
    settings: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))


class TestCalendarProviderConfig(TestBase, table=True):
    __tablename__ = "backup_test_calendar_provider_configs"

    provider: str
    client_id: Optional[str] = None
    client_secret_encrypted: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    tenant_id: Optional[str] = None
    enabled: bool = True


class TestUserTask(TestBase, table=True):
    __tablename__ = "backup_test_user_tasks"

    title: str
    due_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    user_id: int


class TestPeopleTag(TestBase, table=True):
    __tablename__ = "backup_test_p_tags"

    name: str
    color: Optional[str] = None
    user_id: Optional[int] = None
    parent_id: Optional[int] = None


class TestGlobalSpeaker(TestBase, table=True):
    __tablename__ = "backup_test_global_speakers"

    name: str
    embedding: Optional[list[float]] = Field(default=None, sa_column=Column(JSON, nullable=True))
    is_voiceprint_locked: bool = False
    color: Optional[str] = None
    title: Optional[str] = None
    company: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    notes: Optional[str] = None
    user_id: Optional[int] = None


class TestPeopleTagLink(TestBase, table=True):
    __tablename__ = "backup_test_people_tags"

    global_speaker_id: int
    tag_id: int


class TestTag(TestBase, table=True):
    __tablename__ = "backup_test_tags"

    name: str
    color: Optional[str] = None
    user_id: Optional[int] = None
    parent_id: Optional[int] = None


class TestRecording(TestBase, table=True):
    __tablename__ = "backup_test_recordings"

    name: str
    meeting_uid: Optional[str] = Field(default=None, sa_column=Column(Text, unique=True, nullable=True))
    public_id: Optional[str] = Field(default=None, sa_column=Column(Text, unique=True, nullable=True))
    audio_path: str = Field(sa_column=Column(Text, unique=True, nullable=False))
    proxy_path: Optional[str] = None
    file_size_bytes: Optional[int] = None
    status: str = "PROCESSED"
    user_id: Optional[int] = None


class TestCalendarConnection(TestBase, table=True):
    __tablename__ = "backup_test_calendar_connections"

    user_id: int
    provider: str
    provider_account_id: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    access_token_encrypted: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    refresh_token_encrypted: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    granted_scopes: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    token_expires_at: Optional[datetime] = None
    sync_status: str = "idle"
    sync_error: Optional[str] = None
    last_sync_started_at: Optional[datetime] = None
    last_sync_completed_at: Optional[datetime] = None
    last_synced_at: Optional[datetime] = None


class TestCalendarSource(TestBase, table=True):
    __tablename__ = "backup_test_calendar_sources"

    connection_id: int
    provider_calendar_id: str
    name: str
    description: Optional[str] = None
    time_zone: Optional[str] = None
    colour: Optional[str] = None
    user_colour: Optional[str] = None
    is_primary: bool = False
    is_read_only: bool = False
    is_selected: bool = False
    sync_cursor: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    last_synced_at: Optional[datetime] = None
    sync_window_start: Optional[datetime] = None
    sync_window_end: Optional[datetime] = None


class TestCalendarEvent(TestBase, table=True):
    __tablename__ = "backup_test_calendar_events"

    calendar_id: int
    provider_event_id: str
    title: str
    status: str = "confirmed"
    is_all_day: bool = Field(default=False, sa_column=Column(Boolean, nullable=False))
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    start_date: Optional[date] = Field(default=None, sa_column=Column(Date, nullable=True))
    end_date: Optional[date] = Field(default=None, sa_column=Column(Date, nullable=True))
    location_text: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    meeting_url: Optional[str] = None
    source_url: Optional[str] = None
    external_updated_at: Optional[datetime] = None


class TestRecordingSpeaker(TestBase, table=True):
    __tablename__ = "backup_test_recording_speakers"

    recording_id: int
    global_speaker_id: Optional[int] = None
    diarization_label: str
    local_name: Optional[str] = None
    name: Optional[str] = None
    snippet_start: Optional[float] = None
    snippet_end: Optional[float] = None
    voice_snippet_path: Optional[str] = None
    embedding: Optional[list[float]] = Field(default=None, sa_column=Column(JSON, nullable=True))
    color: Optional[str] = None
    merged_into_id: Optional[int] = None


class TestRecordingTag(TestBase, table=True):
    __tablename__ = "backup_test_recording_tags"

    recording_id: int
    tag_id: int


class TestTranscript(TestBase, table=True):
    __tablename__ = "backup_test_transcripts"

    recording_id: int
    text: Optional[str] = None


class TestChatMessage(TestBase, table=True):
    __tablename__ = "backup_test_chat_messages"

    recording_id: int
    user_id: Optional[int] = None
    role: str = "user"
    content: str = ""


TEST_MODELS = [
    ("users", TestUser),
    ("calendar_provider_configs", TestCalendarProviderConfig),
    ("user_tasks", TestUserTask),
    ("p_tags", TestPeopleTag),
    ("global_speakers", TestGlobalSpeaker),
    ("people_tag_links", TestPeopleTagLink),
    ("tags", TestTag),
    ("recordings", TestRecording),
    ("calendar_connections", TestCalendarConnection),
    ("calendar_sources", TestCalendarSource),
    ("calendar_events", TestCalendarEvent),
    ("recording_speakers", TestRecordingSpeaker),
    ("recording_tags", TestRecordingTag),
    ("transcripts", TestTranscript),
    ("chat_messages", TestChatMessage),
]


class StubPathManager:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._recordings_directory = root / "recordings"
        self._config_path = root / "config.json"
        self._executable_directory = root / "app"
        (self._executable_directory / "docs").mkdir(parents=True, exist_ok=True)
        self._recordings_directory.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(json.dumps({"gemini_api_key": "top-secret", "theme": "dark"}), encoding="utf-8")
        (self._executable_directory / "docs" / "VERSION").write_text("0.6.0", encoding="utf-8")

    @property
    def user_data_directory(self) -> Path:
        return self._root

    @property
    def recordings_directory(self) -> Path:
        return self._recordings_directory

    @property
    def config_path(self) -> Path:
        return self._config_path

    @property
    def executable_directory(self) -> Path:
        return self._executable_directory


@dataclass
class TestContext:
    __test__ = False

    path_manager: StubPathManager
    sync_engine: Any
    async_session_maker: sessionmaker
    async_engine: Any


def build_test_context(root: Path) -> TestContext:
    root.mkdir(parents=True, exist_ok=True)
    db_path = root / "backup-test.sqlite"
    sync_engine = create_engine(f"sqlite:///{db_path}", future=True)
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async_session_maker = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

    for _, model_cls in TEST_MODELS:
        model_cls.__table__.create(sync_engine)

    return TestContext(
        path_manager=StubPathManager(root),
        sync_engine=sync_engine,
        async_session_maker=async_session_maker,
        async_engine=async_engine,
    )


def patch_backup_manager(monkeypatch: pytest.MonkeyPatch, context: TestContext) -> None:
    monkeypatch.setattr(backup_manager_module, "PathManager", lambda: context.path_manager)
    monkeypatch.setattr(version_utils, "PathManager", lambda: context.path_manager)
    monkeypatch.setattr(backup_manager_module, "async_session_maker", context.async_session_maker)
    monkeypatch.setattr(backup_manager_module, "sync_engine", context.sync_engine)
    monkeypatch.setattr(backup_manager_module, "MODELS", TEST_MODELS)
    monkeypatch.setattr(backup_manager_module, "User", TestUser)
    monkeypatch.setattr(backup_manager_module, "CalendarProviderConfig", TestCalendarProviderConfig)
    monkeypatch.setattr(backup_manager_module, "UserTask", TestUserTask)
    monkeypatch.setattr(backup_manager_module, "PeopleTag", TestPeopleTag)
    monkeypatch.setattr(backup_manager_module, "GlobalSpeaker", TestGlobalSpeaker)
    monkeypatch.setattr(backup_manager_module, "PeopleTagLink", TestPeopleTagLink)
    monkeypatch.setattr(backup_manager_module, "Tag", TestTag)
    monkeypatch.setattr(backup_manager_module, "Recording", TestRecording)
    monkeypatch.setattr(backup_manager_module, "CalendarConnection", TestCalendarConnection)
    monkeypatch.setattr(backup_manager_module, "CalendarSource", TestCalendarSource)
    monkeypatch.setattr(backup_manager_module, "CalendarEvent", TestCalendarEvent)
    monkeypatch.setattr(backup_manager_module, "RecordingSpeaker", TestRecordingSpeaker)
    monkeypatch.setattr(backup_manager_module, "RecordingTag", TestRecordingTag)
    monkeypatch.setattr(backup_manager_module, "Transcript", TestTranscript)
    monkeypatch.setattr(backup_manager_module, "ChatMessage", TestChatMessage)
    monkeypatch.setattr(db_module, "sync_engine", context.sync_engine)
    version_utils.reset_installed_version_cache()
    BackupManager.restore_jobs.clear()


async def seed_source_data(
    session_maker: sessionmaker,
    *,
    recording_meeting_uid: Optional[str] = None,
    recording_public_id: Optional[str] = None,
    recording_audio_path: str = "data/recordings/quarterly-planning.wav",
    recording_proxy_path: Optional[str] = "data/recordings/quarterly-planning.mp3",
) -> None:
    async with session_maker() as session:
        session.add(
            TestUser(
                id=1,
                username="alice",
                hashed_password="hashed-password",
                role="user",
                settings={"gemini_api_key": "user-secret", "theme": "light"},
            )
        )
        session.add(
            TestCalendarProviderConfig(
                id=10,
                provider="microsoft",
                client_id="microsoft-client-id",
                client_secret_encrypted=encrypt_secret("microsoft-client-secret"),
                tenant_id="common",
                enabled=True,
            )
        )
        session.add(
            TestUserTask(
                id=20,
                title="Follow up with supplier",
                due_at=datetime(2026, 4, 18, 9, 30),
                user_id=1,
            )
        )
        session.add(
            TestGlobalSpeaker(
                id=30,
                name="Dana Mercer",
                embedding=[0.11, 0.22, 0.33],
                is_voiceprint_locked=True,
                color="orange",
                notes="Restored voiceprint",
                user_id=1,
            )
        )
        session.add(
            TestRecording(
                id=40,
                name="Quarterly planning",
                meeting_uid=recording_meeting_uid,
                public_id=recording_public_id,
                audio_path=recording_audio_path,
                proxy_path=recording_proxy_path,
                file_size_bytes=1024,
                status="PROCESSED",
                user_id=1,
            )
        )
        session.add(
            TestCalendarConnection(
                id=50,
                user_id=1,
                provider="google",
                provider_account_id="acct-1",
                email="alice@example.com",
                display_name="Alice",
                access_token_encrypted=encrypt_secret("google-access-token"),
                refresh_token_encrypted=encrypt_secret("google-refresh-token"),
                granted_scopes=["openid", "email", "https://www.googleapis.com/auth/calendar.readonly"],
                token_expires_at=datetime(2026, 4, 20, 10, 0),
                sync_status="success",
                last_sync_completed_at=datetime(2026, 4, 12, 10, 0),
                last_synced_at=datetime(2026, 4, 12, 10, 0),
            )
        )
        session.add(
            TestCalendarSource(
                id=60,
                connection_id=50,
                provider_calendar_id="primary",
                name="Work",
                description="Primary work calendar",
                time_zone="Europe/London",
                colour="#4285f4",
                user_colour="emerald",
                is_primary=True,
                is_read_only=False,
                is_selected=True,
                sync_cursor="cursor-123",
                last_synced_at=datetime(2026, 4, 12, 10, 0),
                sync_window_start=datetime(2026, 4, 1, 0, 0),
                sync_window_end=datetime(2026, 5, 1, 0, 0),
            )
        )
        session.add(
            TestCalendarEvent(
                id=70,
                calendar_id=60,
                provider_event_id="evt-1",
                title="Planning review",
                status="confirmed",
                is_all_day=False,
                starts_at=datetime(2026, 4, 13, 14, 0),
                ends_at=datetime(2026, 4, 13, 15, 0),
                location_text="Boardroom A",
                meeting_url="https://meet.google.com/abc-defg-hij",
                source_url="https://calendar.google.com/calendar/event?eid=1",
                external_updated_at=datetime(2026, 4, 12, 9, 45),
            )
        )
        session.add(
            TestRecordingSpeaker(
                id=80,
                recording_id=40,
                global_speaker_id=30,
                diarization_label="SPEAKER_00",
                local_name="Dana Mercer",
                embedding=[0.11, 0.22, 0.33],
            )
        )
        session.add(
            TestRecordingSpeaker(
                id=81,
                recording_id=40,
                global_speaker_id=None,
                diarization_label="SPEAKER_01",
                local_name="Unknown",
                embedding=[0.91, 0.92],
                merged_into_id=80,
            )
        )
        await session.commit()


async def seed_existing_target_recording(
    session_maker: sessionmaker,
    *,
    meeting_uid: Optional[str] = None,
    public_id: Optional[str] = None,
    audio_path: str = "data/recordings/quarterly-planning.wav",
    proxy_path: Optional[str] = None,
    name: str = "Existing quarterly planning",
) -> None:
    async with session_maker() as session:
        session.add(
            TestUser(
                id=101,
                username="alice",
                hashed_password="existing-hash",
                role="user",
                settings={"theme": "dark"},
            )
        )
        session.add(
            TestRecording(
                id=102,
                name=name,
                meeting_uid=meeting_uid,
                public_id=public_id,
                audio_path=audio_path,
                proxy_path=proxy_path,
                file_size_bytes=2048,
                status="PROCESSED",
                user_id=101,
            )
        )
        await session.commit()


@pytest.mark.anyio
async def test_backup_restore_round_trip_includes_calendar_dashboard_and_voiceprints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_context = build_test_context(tmp_path / "source")
    patch_backup_manager(monkeypatch, source_context)

    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "source-encryption-key")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "google-client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "google-client-secret")
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_TENANT_ID", raising=False)

    await seed_source_data(
        source_context.async_session_maker,
        recording_meeting_uid="meeting-uid-round-trip",
    )

    zip_path = await BackupManager.create_backup(include_audio=False)

    with zipfile.ZipFile(zip_path, "r") as archive:
        backup_info = json.loads(archive.read("backup_info.json"))
        provider_configs = json.loads(archive.read("calendar_provider_configs.json"))
        calendar_connections = json.loads(archive.read("calendar_connections.json"))
        recordings = json.loads(archive.read("recordings.json"))
        user_tasks = json.loads(archive.read("user_tasks.json"))
        global_speakers = json.loads(archive.read("global_speakers.json"))
        users = json.loads(archive.read("users.json"))

    assert backup_info["contains_restorable_calendar_credentials"] is True
    assert backup_info["version"] == "0.6.0"

    google_provider = next(item for item in provider_configs if item["provider"] == "google")
    microsoft_provider = next(item for item in provider_configs if item["provider"] == "microsoft")
    assert google_provider["client_id"] == "google-client-id"
    assert google_provider["client_secret"] == "google-client-secret"
    assert microsoft_provider["client_secret"] == "microsoft-client-secret"

    assert recordings[0]["meeting_uid"] == "meeting-uid-round-trip"
    assert recordings[0]["audio_path"] == "recordings/quarterly-planning.opus"
    assert recordings[0]["proxy_path"] is None
    assert calendar_connections[0]["access_token"] == "google-access-token"
    assert calendar_connections[0]["refresh_token"] == "google-refresh-token"
    assert user_tasks[0]["title"] == "Follow up with supplier"
    assert global_speakers[0]["embedding"] == [0.11, 0.22, 0.33]
    assert users[0]["settings"]["gemini_api_key"] == "REDACTED"

    target_context = build_test_context(tmp_path / "target")
    patch_backup_manager(monkeypatch, target_context)

    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "target-encryption-key")
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_TENANT_ID", raising=False)

    job_id = "restore-job"
    BackupManager.restore_jobs[job_id] = {
        "status": "pending",
        "progress": "Queued",
        "error": None,
    }

    await BackupManager.restore_backup(job_id, zip_path, clear_existing=False, overwrite_existing=False)

    assert BackupManager.restore_jobs[job_id]["status"] == "completed"

    with Session(target_context.sync_engine) as session:
        restored_google = session.exec(
            select(TestCalendarProviderConfig).where(TestCalendarProviderConfig.provider == "google")
        ).one()
        restored_microsoft = session.exec(
            select(TestCalendarProviderConfig).where(TestCalendarProviderConfig.provider == "microsoft")
        ).one()
        restored_connection = session.exec(select(TestCalendarConnection)).one()
        restored_recording = session.exec(select(TestRecording)).one()
        restored_source = session.exec(select(TestCalendarSource)).one()
        restored_event = session.exec(select(TestCalendarEvent)).one()
        restored_task = session.exec(select(TestUserTask)).one()
        restored_user = session.exec(select(TestUser)).one()
        restored_global_speaker = session.exec(select(TestGlobalSpeaker)).one()
        restored_speakers = session.exec(
            select(TestRecordingSpeaker).order_by(TestRecordingSpeaker.diarization_label)
        ).all()

    assert restored_google.client_id == "google-client-id"
    assert decrypt_secret(restored_google.client_secret_encrypted) == "google-client-secret"
    assert decrypt_secret(restored_microsoft.client_secret_encrypted) == "microsoft-client-secret"

    assert decrypt_secret(restored_connection.access_token_encrypted) == "google-access-token"
    assert decrypt_secret(restored_connection.refresh_token_encrypted) == "google-refresh-token"
    assert restored_recording.meeting_uid == "meeting-uid-round-trip"
    assert restored_recording.audio_path.endswith("quarterly-planning.opus")
    assert restored_source.user_colour == "emerald"
    assert restored_source.is_selected is True
    assert restored_source.sync_cursor == "cursor-123"
    assert restored_event.meeting_url == "https://meet.google.com/abc-defg-hij"
    assert restored_task.due_at == datetime(2026, 4, 18, 9, 30)
    assert restored_user.settings["gemini_api_key"] is None
    assert restored_global_speaker.embedding == [0.11, 0.22, 0.33]
    assert restored_global_speaker.is_voiceprint_locked is True

    merged_speaker = next(speaker for speaker in restored_speakers if speaker.diarization_label == "SPEAKER_01")
    target_speaker = next(speaker for speaker in restored_speakers if speaker.diarization_label == "SPEAKER_00")
    assert merged_speaker.merged_into_id == target_speaker.id

    await source_context.async_engine.dispose()
    await target_context.async_engine.dispose()


def test_create_backup_blocking_uses_sync_path_without_async_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_context = build_test_context(tmp_path / "source")
    patch_backup_manager(monkeypatch, source_context)

    with Session(source_context.sync_engine) as session:
        session.add(
            TestUser(
                id=1,
                username="alice",
                hashed_password="hashed-password",
                role="user",
                settings={"theme": "light"},
            )
        )
        session.add(
            TestRecording(
                id=40,
                name="Blocking backup meeting",
                meeting_uid="blocking-backup-uid",
                audio_path="data/recordings/blocking-backup.wav",
                proxy_path="data/recordings/blocking-backup.mp3",
                status="PROCESSED",
                user_id=1,
            )
        )
        session.commit()

    zip_path = BackupManager.create_backup_blocking(include_audio=False)

    with zipfile.ZipFile(zip_path, "r") as archive:
        recordings = json.loads(archive.read("recordings.json"))

    assert recordings[0]["meeting_uid"] == "blocking-backup-uid"
    assert recordings[0]["proxy_path"] is None
    assert recordings[0]["audio_path"] == "recordings/blocking-backup.opus"

    source_context.sync_engine.dispose()


@pytest.mark.anyio
async def test_safe_merge_skips_existing_recordings_matched_by_recording_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_context = build_test_context(tmp_path / "source")
    patch_backup_manager(monkeypatch, source_context)

    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "source-encryption-key")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "google-client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "google-client-secret")
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_TENANT_ID", raising=False)

    await seed_source_data(source_context.async_session_maker)
    zip_path = await BackupManager.create_backup(include_audio=False)

    target_context = build_test_context(tmp_path / "target")
    patch_backup_manager(monkeypatch, target_context)

    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "target-encryption-key")
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_TENANT_ID", raising=False)

    await seed_existing_target_recording(target_context.async_session_maker)

    job_id = "safe-merge-job"
    BackupManager.restore_jobs[job_id] = {
        "status": "pending",
        "progress": "Queued",
        "error": None,
    }

    await BackupManager.restore_backup(job_id, zip_path, clear_existing=False, overwrite_existing=False)

    with Session(target_context.sync_engine) as session:
        restored_recordings = session.exec(select(TestRecording).order_by(TestRecording.id)).all()
        restored_recording_speakers = session.exec(select(TestRecordingSpeaker)).all()

    assert len(restored_recordings) == 1
    assert restored_recordings[0].name == "Existing quarterly planning"
    assert BackupManager._get_recording_identity(restored_recordings[0].audio_path) == "quarterly-planning"
    assert restored_recording_speakers == []

    await source_context.async_engine.dispose()
    await target_context.async_engine.dispose()


@pytest.mark.anyio
async def test_safe_merge_skips_existing_recordings_matched_by_meeting_uid_when_paths_differ(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_context = build_test_context(tmp_path / "source")
    patch_backup_manager(monkeypatch, source_context)

    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "source-encryption-key")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "google-client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "google-client-secret")
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_TENANT_ID", raising=False)

    await seed_source_data(
        source_context.async_session_maker,
        recording_meeting_uid="meeting-uid-shared",
        recording_audio_path="data/recordings/source-quarterly.wav",
    )
    zip_path = await BackupManager.create_backup(include_audio=False)

    target_context = build_test_context(tmp_path / "target")
    patch_backup_manager(monkeypatch, target_context)

    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "target-encryption-key")
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_TENANT_ID", raising=False)

    await seed_existing_target_recording(
        target_context.async_session_maker,
        meeting_uid="meeting-uid-shared",
        audio_path="data/recordings/renamed-quarterly.wav",
        name="Existing renamed meeting",
    )

    job_id = "safe-merge-uid-job"
    BackupManager.restore_jobs[job_id] = {
        "status": "pending",
        "progress": "Queued",
        "error": None,
    }

    await BackupManager.restore_backup(job_id, zip_path, clear_existing=False, overwrite_existing=False)

    with Session(target_context.sync_engine) as session:
        restored_recordings = session.exec(select(TestRecording).order_by(TestRecording.id)).all()
        restored_recording_speakers = session.exec(select(TestRecordingSpeaker)).all()

    assert len(restored_recordings) == 1
    assert restored_recordings[0].name == "Existing renamed meeting"
    assert restored_recordings[0].meeting_uid == "meeting-uid-shared"
    assert restored_recordings[0].audio_path.endswith("renamed-quarterly.wav")
    assert restored_recording_speakers == []

    await source_context.async_engine.dispose()
    await target_context.async_engine.dispose()


@pytest.mark.anyio
async def test_overwrite_replaces_existing_recordings_matched_by_recording_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_context = build_test_context(tmp_path / "source")
    patch_backup_manager(monkeypatch, source_context)

    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "source-encryption-key")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "google-client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "google-client-secret")
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_TENANT_ID", raising=False)

    await seed_source_data(source_context.async_session_maker)
    zip_path = await BackupManager.create_backup(include_audio=False)

    target_context = build_test_context(tmp_path / "target")
    patch_backup_manager(monkeypatch, target_context)

    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "target-encryption-key")
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_TENANT_ID", raising=False)

    await seed_existing_target_recording(target_context.async_session_maker)

    job_id = "overwrite-job"
    BackupManager.restore_jobs[job_id] = {
        "status": "pending",
        "progress": "Queued",
        "error": None,
    }

    await BackupManager.restore_backup(job_id, zip_path, clear_existing=False, overwrite_existing=True)

    with Session(target_context.sync_engine) as session:
        restored_recordings = session.exec(select(TestRecording).order_by(TestRecording.id)).all()
        restored_recording_speakers = session.exec(select(TestRecordingSpeaker)).all()

    assert len(restored_recordings) == 1
    assert restored_recordings[0].name == "Quarterly planning"
    assert BackupManager._get_recording_identity(restored_recordings[0].audio_path) == "quarterly-planning"
    assert len(restored_recording_speakers) == 2

    await source_context.async_engine.dispose()
    await target_context.async_engine.dispose()


@pytest.mark.anyio
async def test_overwrite_replaces_existing_recordings_matched_by_meeting_uid_when_paths_differ(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_context = build_test_context(tmp_path / "source")
    patch_backup_manager(monkeypatch, source_context)

    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "source-encryption-key")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "google-client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "google-client-secret")
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_TENANT_ID", raising=False)

    await seed_source_data(
        source_context.async_session_maker,
        recording_meeting_uid="meeting-uid-shared",
        recording_audio_path="data/recordings/source-quarterly.wav",
    )
    zip_path = await BackupManager.create_backup(include_audio=False)

    target_context = build_test_context(tmp_path / "target")
    patch_backup_manager(monkeypatch, target_context)

    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "target-encryption-key")
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_TENANT_ID", raising=False)

    await seed_existing_target_recording(
        target_context.async_session_maker,
        meeting_uid="meeting-uid-shared",
        audio_path="data/recordings/renamed-quarterly.wav",
        name="Existing renamed meeting",
    )

    job_id = "overwrite-uid-job"
    BackupManager.restore_jobs[job_id] = {
        "status": "pending",
        "progress": "Queued",
        "error": None,
    }

    await BackupManager.restore_backup(job_id, zip_path, clear_existing=False, overwrite_existing=True)

    with Session(target_context.sync_engine) as session:
        restored_recordings = session.exec(select(TestRecording).order_by(TestRecording.id)).all()
        restored_recording_speakers = session.exec(select(TestRecordingSpeaker)).all()

    assert len(restored_recordings) == 1
    assert restored_recordings[0].name == "Quarterly planning"
    assert restored_recordings[0].meeting_uid == "meeting-uid-shared"
    assert restored_recordings[0].audio_path.endswith("source-quarterly.opus")
    assert len(restored_recording_speakers) == 2

    await source_context.async_engine.dispose()
    await target_context.async_engine.dispose()


@pytest.mark.anyio
async def test_restore_clears_stale_proxy_path_and_enqueues_proxy_generation_when_audio_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_context = build_test_context(tmp_path / "target")
    patch_backup_manager(monkeypatch, target_context)

    restored_audio = target_context.path_manager.recordings_directory / "imported-meeting.opus"
    queued_proxy_ids: list[int] = []

    monkeypatch.setattr(
        BackupManager,
        "_enqueue_proxy_generation",
        staticmethod(lambda recording_id: queued_proxy_ids.append(recording_id)),
    )

    backup_zip = tmp_path / "restore-proxy.zip"
    with zipfile.ZipFile(backup_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "users.json",
            json.dumps(
                [
                    {
                        "id": 1,
                        "created_at": utc_now().isoformat(),
                        "updated_at": utc_now().isoformat(),
                        "username": "alice",
                        "hashed_password": "hashed-password",
                        "is_active": True,
                        "is_superuser": False,
                        "force_password_change": False,
                        "role": "user",
                        "settings": {},
                    }
                ]
            ),
        )
        archive.writestr(
            "recordings.json",
            json.dumps(
                [
                    {
                        "id": 40,
                        "created_at": utc_now().isoformat(),
                        "updated_at": utc_now().isoformat(),
                        "name": "Imported meeting",
                        "meeting_uid": "restored-import-meeting-uid",
                        "audio_path": "recordings/imported-meeting.opus",
                        "proxy_path": "data/recordings/imported-meeting.mp3",
                        "status": "PROCESSED",
                        "user_id": 1,
                    }
                ]
            ),
        )
        for table_name, _ in TEST_MODELS:
            if table_name in {"users", "recordings"}:
                continue
            archive.writestr(f"{table_name}.json", "[]")
        archive.writestr("recordings/imported-meeting.opus", b"fake-opus-audio")

    job_id = "restore-proxy-job"
    BackupManager.restore_jobs[job_id] = {
        "status": "pending",
        "progress": "Queued",
        "error": None,
    }

    await BackupManager.restore_backup(job_id, str(backup_zip), clear_existing=False, overwrite_existing=False)

    with Session(target_context.sync_engine) as session:
        restored_recording = session.exec(select(TestRecording)).one()

    assert queued_proxy_ids == [restored_recording.id]
    assert restored_audio.exists()

    assert restored_recording.proxy_path is None
    assert restored_recording.audio_path.endswith("imported-meeting.opus")

    await target_context.async_engine.dispose()

@pytest.mark.anyio
async def test_safe_merge_skips_existing_recording_matched_by_public_id_when_meeting_uid_differs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_context = build_test_context(tmp_path / "source")
    patch_backup_manager(monkeypatch, source_context)

    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "source-encryption-key")
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_TENANT_ID", raising=False)

    await seed_source_data(
        source_context.async_session_maker,
        recording_meeting_uid="meeting-uid-source",
        recording_public_id="public-shared",
        recording_audio_path="data/recordings/source-quarterly.wav",
    )
    zip_path = await BackupManager.create_backup(include_audio=False)

    target_context = build_test_context(tmp_path / "target")
    patch_backup_manager(monkeypatch, target_context)

    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "target-encryption-key")
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_TENANT_ID", raising=False)

    await seed_existing_target_recording(
        target_context.async_session_maker,
        meeting_uid="meeting-uid-target-different",
        public_id="public-shared",
        audio_path="data/recordings/target-quarterly.wav",
        name="Existing target meeting",
    )

    job_id = "safe-merge-public-id-job"
    BackupManager.restore_jobs[job_id] = {
        "status": "pending",
        "progress": "Queued",
        "error": None,
    }

    await BackupManager.restore_backup(job_id, zip_path, clear_existing=False, overwrite_existing=False)

    with Session(target_context.sync_engine) as session:
        restored_recordings = session.exec(select(TestRecording).order_by(TestRecording.id)).all()

    assert len(restored_recordings) == 1
    assert restored_recordings[0].name == "Existing target meeting"
    assert restored_recordings[0].public_id == "public-shared"
    assert restored_recordings[0].meeting_uid == "meeting-uid-target-different"

    await source_context.async_engine.dispose()
    await target_context.async_engine.dispose()


@pytest.mark.anyio
async def test_restore_renames_audio_path_on_collision_with_unrelated_recording(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_context = build_test_context(tmp_path / "source")
    patch_backup_manager(monkeypatch, source_context)

    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "source-encryption-key")
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_TENANT_ID", raising=False)

    await seed_source_data(
        source_context.async_session_maker,
        recording_meeting_uid="meeting-uid-incoming",
        recording_public_id="public-incoming",
        recording_audio_path="data/recordings/shared-name.wav",
    )
    zip_path = await BackupManager.create_backup(include_audio=False)

    target_context = build_test_context(tmp_path / "target")
    patch_backup_manager(monkeypatch, target_context)

    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "target-encryption-key")
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_TENANT_ID", raising=False)

    # Target holds a recording whose meeting_uid and public_id differ but whose audio file
    # would collide with the runtime path derived from the incoming backup.
    target_runtime_path = str(target_context.path_manager.recordings_directory / "shared-name.opus")
    await seed_existing_target_recording(
        target_context.async_session_maker,
        meeting_uid="meeting-uid-target",
        public_id="public-target",
        audio_path=target_runtime_path,
        name="Unrelated target meeting",
    )

    job_id = "audio-path-collision-job"
    BackupManager.restore_jobs[job_id] = {
        "status": "pending",
        "progress": "Queued",
        "error": None,
    }

    await BackupManager.restore_backup(job_id, zip_path, clear_existing=False, overwrite_existing=False)

    assert BackupManager.restore_jobs[job_id]["status"] == "completed"

    with Session(target_context.sync_engine) as session:
        restored_recordings = session.exec(
            select(TestRecording).order_by(TestRecording.id)
        ).all()

    assert len(restored_recordings) == 2
    inserted = next(row for row in restored_recordings if row.meeting_uid == "meeting-uid-incoming")
    # Suffixed with the incoming meeting_uid to dodge the unique-constraint.
    assert inserted.audio_path != target_runtime_path
    assert "meeting-uid-incoming" in inserted.audio_path
    assert inserted.audio_path.endswith(".opus")

    await source_context.async_engine.dispose()
    await target_context.async_engine.dispose()
