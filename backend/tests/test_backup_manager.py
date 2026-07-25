from __future__ import annotations

import json
import os
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import pytest
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Text,
    create_engine,
    event,
)
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import Field, Session, SQLModel, select

import backend.core.backup_manager as backup_manager_module
import backend.core.db as db_module
import backend.utils.version as version_utils
from backend.core.backup_manager import BackupManager
from backend.core.encryption import decrypt_secret, encrypt_secret
from backend.models.chat import ChatMessage  # noqa: F401
from backend.models.context_chunk import ContextChunk  # noqa: F401
from backend.models.document import Document  # noqa: F401
from backend.models.invitation import Invitation  # noqa: F401
from backend.models.speaker import RecordingSpeaker  # noqa: F401
from backend.models.tag import RecordingTag  # noqa: F401
from backend.models.transcript import Transcript  # noqa: F401
from backend.utils.time import utc_now


class TestBase(SQLModel):
    __test__ = False

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(
        default_factory=utc_now, sa_type=DateTime, nullable=False
    )
    updated_at: datetime = Field(
        default_factory=utc_now, sa_type=DateTime, nullable=False
    )


class TestInvitation(TestBase, table=True):
    __tablename__ = "backup_test_invitations"

    code: str


class TestUser(TestBase, table=True):
    __tablename__ = "backup_test_users"

    username: str
    hashed_password: str
    is_active: bool = True
    is_superuser: bool = False
    force_password_change: bool = False
    role: str = "user"
    settings: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    invitation_id: Optional[int] = Field(
        default=None, foreign_key="backup_test_invitations.id"
    )


class TestCalendarProviderConfig(TestBase, table=True):
    __tablename__ = "backup_test_calendar_provider_configs"

    provider: str
    client_id: Optional[str] = None
    client_secret_encrypted: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    tenant_id: Optional[str] = None
    enabled: bool = True


class TestUserTask(TestBase, table=True):
    __tablename__ = "backup_test_user_tasks"

    title: str
    body: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    due_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    archived_at: Optional[datetime] = None
    user_id: int = Field(foreign_key="backup_test_users.id")


class TestPeopleTag(TestBase, table=True):
    __tablename__ = "backup_test_p_tags"

    name: str
    color: Optional[str] = None
    user_id: Optional[int] = Field(default=None, foreign_key="backup_test_users.id")
    parent_id: Optional[int] = Field(default=None, foreign_key="backup_test_p_tags.id")


class TestGlobalSpeaker(TestBase, table=True):
    __tablename__ = "backup_test_global_speakers"

    name: str
    embedding: Optional[list[float]] = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    is_voiceprint_locked: bool = False
    color: Optional[str] = None
    title: Optional[str] = None
    company: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    notes: Optional[str] = None
    user_id: Optional[int] = Field(default=None, foreign_key="backup_test_users.id")


class TestPeopleTagLink(TestBase, table=True):
    __tablename__ = "backup_test_people_tags"

    global_speaker_id: int = Field(foreign_key="backup_test_global_speakers.id")
    tag_id: int = Field(foreign_key="backup_test_p_tags.id")


class TestTag(TestBase, table=True):
    __tablename__ = "backup_test_tags"

    name: str
    color: Optional[str] = None
    user_id: Optional[int] = Field(default=None, foreign_key="backup_test_users.id")
    parent_id: Optional[int] = Field(default=None, foreign_key="backup_test_tags.id")


class TestUserTaskTag(TestBase, table=True):
    __tablename__ = "backup_test_user_task_tags"

    task_id: int = Field(foreign_key="backup_test_user_tasks.id")
    tag_id: int = Field(foreign_key="backup_test_tags.id")


class TestUserTaskRecording(TestBase, table=True):
    __tablename__ = "backup_test_user_task_recordings"

    task_id: int = Field(foreign_key="backup_test_user_tasks.id")
    recording_id: int = Field(foreign_key="backup_test_recordings.id")


class TestRecording(TestBase, table=True):
    __tablename__ = "backup_test_recordings"

    name: str
    meeting_uid: Optional[str] = Field(
        default=None, sa_column=Column(Text, unique=True, nullable=True)
    )
    public_id: Optional[str] = Field(
        default=None, sa_column=Column(Text, unique=True, nullable=True)
    )
    audio_path: str = Field(sa_column=Column(Text, unique=True, nullable=False))
    proxy_path: Optional[str] = None
    file_size_bytes: Optional[int] = None
    status: str = "PROCESSED"
    client_status: Optional[str] = None
    celery_task_id: Optional[str] = None
    processing_step: Optional[str] = None
    processing_progress: int = 0
    upload_progress: int = 0
    # Mirrors the real model: the default lives on the Pydantic field, not the column,
    # so an explicit NULL is inserted as NULL rather than being replaced by a
    # SQLAlchemy column default.
    pipeline_generation: Optional[str] = Field(
        default="unified", sa_column=Column(Text, nullable=True)
    )
    user_id: Optional[int] = Field(default=None, foreign_key="backup_test_users.id")
    calendar_event_id: Optional[int] = Field(
        default=None, foreign_key="backup_test_calendar_events.id"
    )


class TestCalendarConnection(TestBase, table=True):
    __tablename__ = "backup_test_calendar_connections"

    user_id: int = Field(foreign_key="backup_test_users.id")
    provider: str
    provider_account_id: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    access_token_encrypted: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    refresh_token_encrypted: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    granted_scopes: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    token_expires_at: Optional[datetime] = None
    sync_status: str = "idle"
    sync_error: Optional[str] = None
    last_sync_started_at: Optional[datetime] = None
    last_sync_completed_at: Optional[datetime] = None
    last_synced_at: Optional[datetime] = None


class TestCalendarSource(TestBase, table=True):
    __tablename__ = "backup_test_calendar_sources"

    connection_id: int = Field(foreign_key="backup_test_calendar_connections.id")
    provider_calendar_id: str
    name: str
    description: Optional[str] = None
    time_zone: Optional[str] = None
    colour: Optional[str] = None
    user_colour: Optional[str] = None
    is_primary: bool = False
    is_read_only: bool = False
    is_selected: bool = False
    sync_cursor: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    last_synced_at: Optional[datetime] = None
    sync_window_start: Optional[datetime] = None
    sync_window_end: Optional[datetime] = None


class TestCalendarEvent(TestBase, table=True):
    __tablename__ = "backup_test_calendar_events"

    calendar_id: int = Field(foreign_key="backup_test_calendar_sources.id")
    provider_event_id: str
    title: str
    status: str = "confirmed"
    is_all_day: bool = Field(default=False, sa_column=Column(Boolean, nullable=False))
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    start_date: Optional[date] = Field(
        default=None, sa_column=Column(Date, nullable=True)
    )
    end_date: Optional[date] = Field(
        default=None, sa_column=Column(Date, nullable=True)
    )
    location_text: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    meeting_url: Optional[str] = None
    source_url: Optional[str] = None
    external_updated_at: Optional[datetime] = None


class TestRecordingSpeaker(TestBase, table=True):
    __tablename__ = "backup_test_recording_speakers"

    recording_id: int = Field(foreign_key="backup_test_recordings.id")
    global_speaker_id: Optional[int] = Field(
        default=None, foreign_key="backup_test_global_speakers.id"
    )
    diarization_label: str
    local_name: Optional[str] = None
    name: Optional[str] = None
    snippet_start: Optional[float] = None
    snippet_end: Optional[float] = None
    voice_snippet_path: Optional[str] = None
    embedding: Optional[list[float]] = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    color: Optional[str] = None
    merged_into_id: Optional[int] = Field(
        default=None, foreign_key="backup_test_recording_speakers.id"
    )


class TestRecordingTag(TestBase, table=True):
    __tablename__ = "backup_test_recording_tags"

    recording_id: int = Field(foreign_key="backup_test_recordings.id")
    tag_id: int = Field(foreign_key="backup_test_tags.id")


class TestTranscript(TestBase, table=True):
    __tablename__ = "backup_test_transcripts"

    recording_id: int = Field(foreign_key="backup_test_recordings.id")
    text: Optional[str] = None


class TestChatMessage(TestBase, table=True):
    __tablename__ = "backup_test_chat_messages"

    recording_id: int = Field(foreign_key="backup_test_recordings.id")
    user_id: Optional[int] = Field(default=None, foreign_key="backup_test_users.id")
    role: str = "user"
    content: str = ""


class TestDocument(TestBase, table=True):
    __tablename__ = "backup_test_documents"

    recording_id: int = Field(foreign_key="backup_test_recordings.id")
    title: str
    file_path: str = Field(sa_column=Column(Text, unique=True, nullable=False))
    file_type: str = "text/plain"
    status: str = "READY"
    error_message: Optional[str] = None


TEST_MODELS = [
    ("users", TestUser),
    ("calendar_provider_configs", TestCalendarProviderConfig),
    ("calendar_connections", TestCalendarConnection),
    ("calendar_sources", TestCalendarSource),
    ("calendar_events", TestCalendarEvent),
    ("user_tasks", TestUserTask),
    ("p_tags", TestPeopleTag),
    ("global_speakers", TestGlobalSpeaker),
    ("people_tag_links", TestPeopleTagLink),
    ("tags", TestTag),
    ("user_task_tags", TestUserTaskTag),
    ("recordings", TestRecording),
    ("user_task_recordings", TestUserTaskRecording),
    ("recording_speakers", TestRecordingSpeaker),
    ("recording_tags", TestRecordingTag),
    ("transcripts", TestTranscript),
    ("chat_messages", TestChatMessage),
    ("documents", TestDocument),
]


class StubPathManager:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._recordings_directory = root / "recordings"
        self._documents_directory = root / "documents"
        self._config_path = root / "config.json"
        self._executable_directory = root / "app"
        (self._executable_directory / "docs").mkdir(parents=True, exist_ok=True)
        self._recordings_directory.mkdir(parents=True, exist_ok=True)
        self._documents_directory.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(
            json.dumps({"gemini_api_key": "top-secret", "theme": "dark"}),
            encoding="utf-8",
        )
        (self._executable_directory / "docs" / "VERSION").write_text(
            "0.6.0", encoding="utf-8"
        )

    @property
    def user_data_directory(self) -> Path:
        return self._root

    @property
    def recordings_directory(self) -> Path:
        return self._recordings_directory

    @property
    def documents_directory(self) -> Path:
        return self._documents_directory

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


def _enforce_sqlite_foreign_keys(engine) -> None:
    """Turn on SQLite foreign-key enforcement for every connection this engine opens.

    SQLite ignores foreign keys by default. Leaving it off is what allowed the surrogate
    models to declare relationships that were never checked, so restore bugs that violate
    a constraint in production passed cleanly here.
    """

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection, _connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def build_test_context(root: Path) -> TestContext:
    root.mkdir(parents=True, exist_ok=True)
    db_path = root / "backup-test.sqlite"
    sync_engine = create_engine(f"sqlite:///{db_path}", future=True)
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    _enforce_sqlite_foreign_keys(sync_engine)
    _enforce_sqlite_foreign_keys(async_engine.sync_engine)
    async_session_maker = sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )

    TestInvitation.__table__.create(sync_engine)
    for _, model_cls in TEST_MODELS:
        model_cls.__table__.create(sync_engine)

    return TestContext(
        path_manager=StubPathManager(root),
        sync_engine=sync_engine,
        async_session_maker=async_session_maker,
        async_engine=async_engine,
    )


def patch_backup_manager(monkeypatch: pytest.MonkeyPatch, context: TestContext) -> None:
    monkeypatch.setattr(
        backup_manager_module, "PathManager", lambda: context.path_manager
    )
    monkeypatch.setattr(version_utils, "PathManager", lambda: context.path_manager)
    monkeypatch.setattr(
        backup_manager_module, "async_session_maker", context.async_session_maker
    )
    monkeypatch.setattr(backup_manager_module, "sync_engine", context.sync_engine)
    monkeypatch.setattr(backup_manager_module, "MODELS", TEST_MODELS)
    monkeypatch.setattr(backup_manager_module, "User", TestUser)
    monkeypatch.setattr(
        backup_manager_module, "CalendarProviderConfig", TestCalendarProviderConfig
    )
    monkeypatch.setattr(backup_manager_module, "UserTask", TestUserTask)
    monkeypatch.setattr(backup_manager_module, "PeopleTag", TestPeopleTag)
    monkeypatch.setattr(backup_manager_module, "GlobalSpeaker", TestGlobalSpeaker)
    monkeypatch.setattr(backup_manager_module, "PeopleTagLink", TestPeopleTagLink)
    monkeypatch.setattr(backup_manager_module, "Tag", TestTag)
    monkeypatch.setattr(backup_manager_module, "UserTaskTag", TestUserTaskTag)
    monkeypatch.setattr(backup_manager_module, "Recording", TestRecording)
    monkeypatch.setattr(
        backup_manager_module, "UserTaskRecording", TestUserTaskRecording
    )
    monkeypatch.setattr(
        backup_manager_module, "CalendarConnection", TestCalendarConnection
    )
    monkeypatch.setattr(backup_manager_module, "CalendarSource", TestCalendarSource)
    monkeypatch.setattr(backup_manager_module, "CalendarEvent", TestCalendarEvent)
    monkeypatch.setattr(backup_manager_module, "RecordingSpeaker", TestRecordingSpeaker)
    monkeypatch.setattr(backup_manager_module, "RecordingTag", TestRecordingTag)
    monkeypatch.setattr(backup_manager_module, "Transcript", TestTranscript)
    monkeypatch.setattr(backup_manager_module, "ChatMessage", TestChatMessage)
    monkeypatch.setattr(backup_manager_module, "Document", TestDocument)
    monkeypatch.setattr(
        BackupManager,
        "_enqueue_recording_finalization",
        staticmethod(lambda recording_id, needs_proxy=True: None),
    )
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
        # Flush each row as it is added: SQLite foreign keys are enforced in
        # these tests, so a parent must exist before its child is inserted.
        async def _add(row):
            session.add(row)
            await session.flush()

        await _add(
            TestUser(
                id=1,
                username="alice",
                hashed_password="hashed-password",
                role="user",
                settings={"gemini_api_key": "user-secret", "theme": "light"},
            )
        )
        await _add(
            TestCalendarProviderConfig(
                id=10,
                provider="microsoft",
                client_id="microsoft-client-id",
                client_secret_encrypted=encrypt_secret("microsoft-client-secret"),
                tenant_id="common",
                enabled=True,
            )
        )
        await _add(
            TestUserTask(
                id=20,
                title="Follow up with supplier",
                body="Confirm shipment timeline.",
                due_at=datetime(2026, 4, 18, 9, 30),
                user_id=1,
            )
        )
        await _add(
            TestTag(
                id=25,
                name="Follow-up",
                color="orange",
                user_id=1,
            )
        )
        await _add(
            TestUserTaskTag(
                id=26,
                task_id=20,
                tag_id=25,
            )
        )
        await _add(
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
        await _add(
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
        await _add(
            TestUserTaskRecording(
                id=27,
                task_id=20,
                recording_id=40,
            )
        )
        await _add(
            TestCalendarConnection(
                id=50,
                user_id=1,
                provider="google",
                provider_account_id="acct-1",
                email="alice@example.com",
                display_name="Alice",
                access_token_encrypted=encrypt_secret("google-access-token"),
                refresh_token_encrypted=encrypt_secret("google-refresh-token"),
                granted_scopes=[
                    "openid",
                    "email",
                    "https://www.googleapis.com/auth/calendar.readonly",
                ],
                token_expires_at=datetime(2026, 4, 20, 10, 0),
                sync_status="success",
                last_sync_completed_at=datetime(2026, 4, 12, 10, 0),
                last_synced_at=datetime(2026, 4, 12, 10, 0),
            )
        )
        await _add(
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
        await _add(
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
        await _add(
            TestRecordingSpeaker(
                id=80,
                recording_id=40,
                global_speaker_id=30,
                diarization_label="SPEAKER_00",
                local_name="Dana Mercer",
                embedding=[0.11, 0.22, 0.33],
            )
        )
        await _add(
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
        # Flush each row as it is added: SQLite foreign keys are enforced in
        # these tests, so a parent must exist before its child is inserted.
        async def _add(row):
            session.add(row)
            await session.flush()

        await _add(
            TestUser(
                id=101,
                username="alice",
                hashed_password="existing-hash",
                role="user",
                settings={"theme": "dark"},
            )
        )
        await _add(
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

    zip_path, _ = await BackupManager.create_backup(include_audio=False)

    with zipfile.ZipFile(zip_path, "r") as archive:
        backup_info = json.loads(archive.read("backup_info.json"))
        provider_configs = json.loads(archive.read("calendar_provider_configs.json"))
        calendar_connections = json.loads(archive.read("calendar_connections.json"))
        recordings = json.loads(archive.read("recordings.json"))
        user_tasks = json.loads(archive.read("user_tasks.json"))
        user_task_tags = json.loads(archive.read("user_task_tags.json"))
        user_task_recordings = json.loads(archive.read("user_task_recordings.json"))
        global_speakers = json.loads(archive.read("global_speakers.json"))
        users = json.loads(archive.read("users.json"))

    assert backup_info["contains_restorable_calendar_credentials"] is True
    assert backup_info["version"] == "0.6.0"

    google_provider = next(
        item for item in provider_configs if item["provider"] == "google"
    )
    microsoft_provider = next(
        item for item in provider_configs if item["provider"] == "microsoft"
    )
    assert google_provider["client_id"] == "google-client-id"
    assert google_provider["client_secret"] == "google-client-secret"
    assert microsoft_provider["client_secret"] == "microsoft-client-secret"

    assert recordings[0]["meeting_uid"] == "meeting-uid-round-trip"
    # A metadata-only archive keeps the source extension: the row then points at where
    # the audio actually lives, rather than at a .opus file that was never written.
    assert recordings[0]["audio_path"] == "recordings/quarterly-planning.wav"
    assert recordings[0]["proxy_path"] is None
    assert calendar_connections[0]["access_token"] == "google-access-token"
    assert calendar_connections[0]["refresh_token"] == "google-refresh-token"
    assert user_tasks[0]["title"] == "Follow up with supplier"
    assert user_tasks[0]["body"] == "Confirm shipment timeline."
    assert user_task_tags[0]["task_id"] == 20
    assert user_task_tags[0]["tag_id"] == 25
    assert user_task_recordings[0]["task_id"] == 20
    assert user_task_recordings[0]["recording_id"] == 40
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

    await BackupManager.restore_backup(
        job_id, zip_path, clear_existing=False, overwrite_existing=False
    )

    assert BackupManager.restore_jobs[job_id]["status"] == "completed"

    with Session(target_context.sync_engine) as session:
        restored_google = session.exec(
            select(TestCalendarProviderConfig).where(
                TestCalendarProviderConfig.provider == "google"
            )
        ).one()
        restored_microsoft = session.exec(
            select(TestCalendarProviderConfig).where(
                TestCalendarProviderConfig.provider == "microsoft"
            )
        ).one()
        restored_connection = session.exec(select(TestCalendarConnection)).one()
        restored_recording = session.exec(select(TestRecording)).one()
        restored_source = session.exec(select(TestCalendarSource)).one()
        restored_event = session.exec(select(TestCalendarEvent)).one()
        restored_task = session.exec(select(TestUserTask)).one()
        restored_task_tag = session.exec(select(TestUserTaskTag)).one()
        restored_task_recording = session.exec(select(TestUserTaskRecording)).one()
        restored_user = session.exec(select(TestUser)).one()
        restored_global_speaker = session.exec(select(TestGlobalSpeaker)).one()
        restored_speakers = session.exec(
            select(TestRecordingSpeaker).order_by(
                TestRecordingSpeaker.diarization_label
            )
        ).all()

    assert restored_google.client_id == "google-client-id"
    assert (
        decrypt_secret(restored_google.client_secret_encrypted)
        == "google-client-secret"
    )
    assert (
        decrypt_secret(restored_microsoft.client_secret_encrypted)
        == "microsoft-client-secret"
    )

    assert (
        decrypt_secret(restored_connection.access_token_encrypted)
        == "google-access-token"
    )
    assert (
        decrypt_secret(restored_connection.refresh_token_encrypted)
        == "google-refresh-token"
    )
    assert restored_recording.meeting_uid == "meeting-uid-round-trip"
    assert restored_recording.audio_path.endswith("quarterly-planning.wav")
    assert restored_source.user_colour == "emerald"
    assert restored_source.is_selected is True
    assert restored_source.sync_cursor == "cursor-123"
    assert restored_event.meeting_url == "https://meet.google.com/abc-defg-hij"
    assert restored_task.due_at == datetime(2026, 4, 18, 9, 30)
    assert restored_task.body == "Confirm shipment timeline."
    assert restored_task_tag.task_id == restored_task.id
    assert restored_task_recording.task_id == restored_task.id
    assert restored_task_recording.recording_id == restored_recording.id
    assert restored_user.settings["gemini_api_key"] is None
    assert restored_global_speaker.embedding == [0.11, 0.22, 0.33]
    assert restored_global_speaker.is_voiceprint_locked is True

    merged_speaker = next(
        speaker
        for speaker in restored_speakers
        if speaker.diarization_label == "SPEAKER_01"
    )
    target_speaker = next(
        speaker
        for speaker in restored_speakers
        if speaker.diarization_label == "SPEAKER_00"
    )
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
        session.flush()  # Foreign keys are enforced; the owner must exist first.
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

    zip_path, _ = BackupManager.create_backup_blocking(include_audio=False)

    with zipfile.ZipFile(zip_path, "r") as archive:
        recordings = json.loads(archive.read("recordings.json"))

    assert recordings[0]["meeting_uid"] == "blocking-backup-uid"
    assert recordings[0]["proxy_path"] is None
    assert recordings[0]["audio_path"] == "recordings/blocking-backup.wav"

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
    zip_path, _ = await BackupManager.create_backup(include_audio=False)

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

    await BackupManager.restore_backup(
        job_id, zip_path, clear_existing=False, overwrite_existing=False
    )

    with Session(target_context.sync_engine) as session:
        restored_recordings = session.exec(
            select(TestRecording).order_by(TestRecording.id)
        ).all()
        restored_recording_speakers = session.exec(select(TestRecordingSpeaker)).all()

    assert len(restored_recordings) == 1
    assert restored_recordings[0].name == "Existing quarterly planning"
    assert (
        BackupManager._get_recording_identity(restored_recordings[0].audio_path)
        == "quarterly-planning"
    )
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
    zip_path, _ = await BackupManager.create_backup(include_audio=False)

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

    await BackupManager.restore_backup(
        job_id, zip_path, clear_existing=False, overwrite_existing=False
    )

    with Session(target_context.sync_engine) as session:
        restored_recordings = session.exec(
            select(TestRecording).order_by(TestRecording.id)
        ).all()
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
    zip_path, _ = await BackupManager.create_backup(include_audio=False)

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

    await BackupManager.restore_backup(
        job_id, zip_path, clear_existing=False, overwrite_existing=True
    )

    with Session(target_context.sync_engine) as session:
        restored_recordings = session.exec(
            select(TestRecording).order_by(TestRecording.id)
        ).all()
        restored_recording_speakers = session.exec(select(TestRecordingSpeaker)).all()

    assert len(restored_recordings) == 1
    assert restored_recordings[0].name == "Quarterly planning"
    assert (
        BackupManager._get_recording_identity(restored_recordings[0].audio_path)
        == "quarterly-planning"
    )
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
    zip_path, _ = await BackupManager.create_backup(include_audio=False)

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

    await BackupManager.restore_backup(
        job_id, zip_path, clear_existing=False, overwrite_existing=True
    )

    with Session(target_context.sync_engine) as session:
        restored_recordings = session.exec(
            select(TestRecording).order_by(TestRecording.id)
        ).all()
        restored_recording_speakers = session.exec(select(TestRecordingSpeaker)).all()

    assert len(restored_recordings) == 1
    assert restored_recordings[0].name == "Quarterly planning"
    assert restored_recordings[0].meeting_uid == "meeting-uid-shared"
    assert restored_recordings[0].audio_path.endswith("source-quarterly.wav")
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

    restored_audio = (
        target_context.path_manager.recordings_directory / "imported-meeting.opus"
    )
    finalized: list[tuple[int, bool]] = []

    monkeypatch.setattr(
        BackupManager,
        "_enqueue_recording_finalization",
        staticmethod(
            lambda recording_id, needs_proxy=True: finalized.append(
                (recording_id, needs_proxy)
            )
        ),
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

    await BackupManager.restore_backup(
        job_id, str(backup_zip), clear_existing=False, overwrite_existing=False
    )

    with Session(target_context.sync_engine) as session:
        restored_recording = session.exec(select(TestRecording)).one()

    # One finalization task per restored recording, flagged for a proxy rebuild because
    # the audio landed on disk.
    assert finalized == [(restored_recording.id, True)]
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
    zip_path, _ = await BackupManager.create_backup(include_audio=False)

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

    await BackupManager.restore_backup(
        job_id, zip_path, clear_existing=False, overwrite_existing=False
    )

    with Session(target_context.sync_engine) as session:
        restored_recordings = session.exec(
            select(TestRecording).order_by(TestRecording.id)
        ).all()

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
    zip_path, _ = await BackupManager.create_backup(include_audio=False)

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
    target_runtime_path = str(
        target_context.path_manager.recordings_directory / "shared-name.wav"
    )
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

    await BackupManager.restore_backup(
        job_id, zip_path, clear_existing=False, overwrite_existing=False
    )

    assert BackupManager.restore_jobs[job_id]["status"] == "completed"

    with Session(target_context.sync_engine) as session:
        restored_recordings = session.exec(
            select(TestRecording).order_by(TestRecording.id)
        ).all()

    assert len(restored_recordings) == 2
    inserted = next(
        row for row in restored_recordings if row.meeting_uid == "meeting-uid-incoming"
    )
    # Suffixed with the incoming meeting_uid to dodge the unique-constraint.
    assert inserted.audio_path != target_runtime_path
    assert "meeting-uid-incoming" in inserted.audio_path
    assert inserted.audio_path.endswith(".wav")

    await source_context.async_engine.dispose()
    await target_context.async_engine.dispose()


async def seed_unrelated_target_user(
    session_maker: sessionmaker,
    *,
    user_id: int,
    username: str,
) -> None:
    async with session_maker() as session:
        # Flush each row as it is added: SQLite foreign keys are enforced in
        # these tests, so a parent must exist before its child is inserted.
        async def _add(row):
            session.add(row)
            await session.flush()

        await _add(
            TestUser(
                id=user_id,
                username=username,
                hashed_password="local-hash",
                role="user",
                settings={"theme": "dark"},
            )
        )
        await session.commit()


@pytest.mark.anyio
async def test_clear_existing_wipes_non_user_data_but_preserves_existing_users(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Characterizes the clear/preflight conflict mode (clear_existing=True): every
    # non-user table is emptied and the recordings directory is recreated before the
    # restore, but the users table is intentionally left intact to prevent lockout.
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
        recording_meeting_uid="meeting-uid-clear",
        recording_audio_path="data/recordings/quarterly-planning.wav",
    )
    zip_path, _ = await BackupManager.create_backup(include_audio=False)

    target_context = build_test_context(tmp_path / "target")
    patch_backup_manager(monkeypatch, target_context)

    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "target-encryption-key")

    # A local-only user (absent from the backup) must survive the clear; a local-only
    # recording must not.
    await seed_unrelated_target_user(
        target_context.async_session_maker, user_id=900, username="local-only-admin"
    )
    async with target_context.async_session_maker() as session:
        session.add(
            TestRecording(
                id=901,
                name="Local only meeting",
                meeting_uid="meeting-uid-local-only",
                audio_path="data/recordings/local-only.wav",
                status="PROCESSED",
                user_id=900,
            )
        )
        await session.commit()

    job_id = "clear-existing-job"
    BackupManager.restore_jobs[job_id] = {
        "status": "pending",
        "progress": "Queued",
        "error": None,
    }

    await BackupManager.restore_backup(
        job_id, zip_path, clear_existing=True, overwrite_existing=False
    )

    assert BackupManager.restore_jobs[job_id]["status"] == "completed"

    with Session(target_context.sync_engine) as session:
        usernames = sorted(row.username for row in session.exec(select(TestUser)).all())
        recordings = session.exec(
            select(TestRecording).order_by(TestRecording.name)
        ).all()

    # The local-only user is preserved (lockout prevention); the backup user is added.
    assert "local-only-admin" in usernames
    assert "alice" in usernames

    # The local-only recording was wiped by the clear; only the backup recording remains.
    recording_names = [row.name for row in recordings]
    assert "Local only meeting" not in recording_names
    assert recording_names == ["Quarterly planning"]

    await source_context.async_engine.dispose()
    await target_context.async_engine.dispose()


@pytest.mark.anyio
async def test_restore_rejects_zip_slip_path_traversal_and_fails_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Characterizes the extraction-stage security boundary: a recordings/ archive entry
    # whose resolved path escapes the user data directory must abort the restore with a
    # "Zip Slip" error and must not write the file outside the tree.
    target_context = build_test_context(tmp_path / "target")
    patch_backup_manager(monkeypatch, target_context)

    escape_target = tmp_path / "zip-slip-escape.txt"
    assert not escape_target.exists()

    backup_zip = tmp_path / "malicious-backup.zip"
    with zipfile.ZipFile(backup_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("recordings/../../zip-slip-escape.txt", b"malicious-payload")
        for table_name, _ in TEST_MODELS:
            archive.writestr(f"{table_name}.json", "[]")

    job_id = "zip-slip-job"
    BackupManager.restore_jobs[job_id] = {
        "status": "pending",
        "progress": "Queued",
        "error": None,
    }

    await BackupManager.restore_backup(
        job_id, str(backup_zip), clear_existing=False, overwrite_existing=False
    )

    assert BackupManager.restore_jobs[job_id]["status"] == "failed"
    assert "Zip Slip" in (BackupManager.restore_jobs[job_id]["error"] or "")
    assert not escape_target.exists()

    await target_context.async_engine.dispose()


@pytest.mark.anyio
async def test_identity_remap_assigns_new_ids_and_remaps_child_foreign_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Characterizes the identity-remap stage: when a backup primary key collides with an
    # unrelated existing row, the restored row is assigned a fresh id and every dependent
    # foreign key follows the new id rather than the pre-existing occupant of that id.
    source_context = build_test_context(tmp_path / "source")
    patch_backup_manager(monkeypatch, source_context)

    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "source-encryption-key")
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MICROSOFT_OAUTH_TENANT_ID", raising=False)

    # The backup's user, recording and task all use id=1 / user_id=1.
    await seed_source_data(
        source_context.async_session_maker,
        recording_meeting_uid="meeting-uid-remap",
        recording_audio_path="data/recordings/quarterly-planning.wav",
    )
    zip_path, _ = await BackupManager.create_backup(include_audio=False)

    target_context = build_test_context(tmp_path / "target")
    patch_backup_manager(monkeypatch, target_context)

    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "target-encryption-key")

    # An unrelated local user already occupies id=1, forcing the backup's alice onto a
    # new id during restore.
    await seed_unrelated_target_user(
        target_context.async_session_maker, user_id=1, username="local-bob"
    )

    job_id = "identity-remap-job"
    BackupManager.restore_jobs[job_id] = {
        "status": "pending",
        "progress": "Queued",
        "error": None,
    }

    await BackupManager.restore_backup(
        job_id, zip_path, clear_existing=False, overwrite_existing=False
    )

    assert BackupManager.restore_jobs[job_id]["status"] == "completed"

    with Session(target_context.sync_engine) as session:
        local_bob = session.exec(
            select(TestUser).where(TestUser.username == "local-bob")
        ).one()
        alice = session.exec(select(TestUser).where(TestUser.username == "alice")).one()
        restored_recording = session.exec(select(TestRecording)).one()
        restored_task = session.exec(select(TestUserTask)).one()
        restored_global_speaker = session.exec(select(TestGlobalSpeaker)).one()

    # The pre-existing occupant of id=1 is untouched; alice landed on a fresh id.
    assert local_bob.id == 1
    assert alice.id != 1

    # Every child foreign key points at alice's new id, never at local-bob.
    assert restored_recording.user_id == alice.id
    assert restored_task.user_id == alice.id
    assert restored_global_speaker.user_id == alice.id

    await source_context.async_engine.dispose()
    await target_context.async_engine.dispose()


def test_apply_foreign_keys_remaps_resolvable_links() -> None:
    # A link whose target was restored is rewritten to the target database's id.
    id_map = {"users": {7: 42}, "calendar_events": {3: 99}}
    item = {"user_id": 7, "calendar_event_id": 3}

    assert (
        BackupManager._apply_foreign_keys("recordings", item, id_map) is None
    )
    assert item == {"user_id": 42, "calendar_event_id": 99}


def test_apply_foreign_keys_nulls_unresolvable_enrichment_link() -> None:
    # calendar_event_id is enrichment: losing the calendar link must not lose the meeting.
    # This is the finding-1 regression guard. Before the fix the source system's id was
    # carried through verbatim and the insert failed the foreign-key constraint, silently
    # dropping the recording and every transcript, speaker and tag hanging off it.
    id_map = {"users": {7: 42}, "calendar_events": {}}
    item = {"user_id": 7, "calendar_event_id": 3}

    assert BackupManager._apply_foreign_keys("recordings", item, id_map) is None
    assert item == {"user_id": 42, "calendar_event_id": None}


def test_apply_foreign_keys_nulls_invitation_id_because_invitations_are_not_archived() -> (
    None
):
    # Users reference an invitation that no backup ever carries, so the link can never
    # resolve. Nulling it keeps the user; the old behaviour lost the user and, with them,
    # everything they owned.
    item = {"username": "alice", "invitation_id": 11}

    assert BackupManager._apply_foreign_keys("users", item, {}) is None
    assert item["invitation_id"] is None


@pytest.mark.parametrize(
    ("table_name", "item"),
    [
        ("p_tags", {"user_id": 7, "name": "Colleagues"}),
        ("chat_messages", {"recording_id": 1, "user_id": 7}),
        ("recordings", {"user_id": 7}),
        ("tags", {"user_id": 7, "name": "Weekly"}),
    ],
)
def test_apply_foreign_keys_skips_row_with_unresolvable_owner(
    table_name: str, item: dict[str, Any]
) -> None:
    # Ownership links are skipped rather than nulled. These columns are nullable in the
    # database, but every read path filters by user_id, so a null owner yields a row that
    # no user can ever see. p_tags and chat_messages previously fell through with the
    # source system's raw id, attaching the row to whichever unrelated user held that id.
    id_map = {"users": {}, "recordings": {1: 1}}

    assert (
        BackupManager._apply_foreign_keys(table_name, item, id_map)
        == backup_manager_module.SKIP_REASON_UNRESOLVED_OWNER
    )


def test_apply_foreign_keys_skips_row_whose_owner_was_never_set() -> None:
    # A backup row that never had an owner cannot gain one during restore.
    assert (
        BackupManager._apply_foreign_keys("recordings", {"user_id": None}, {"users": {}})
        == backup_manager_module.SKIP_REASON_UNRESOLVED_OWNER
    )


def test_models_are_ordered_so_foreign_key_targets_are_restored_first() -> None:
    # The restore resolves foreign keys in a single forward pass, so every table must be
    # listed after the tables it references. This is what makes Recording.calendar_event_id
    # resolvable at all; deferred self-references are exempt by definition.
    position = {
        name: index for index, (name, _) in enumerate(backup_manager_module.MODELS)
    }

    for table_name, specs in backup_manager_module.RESTORE_FOREIGN_KEYS.items():
        for spec in specs:
            if spec.target_table not in position:
                # Targets outside MODELS (e.g. invitations) never resolve by design.
                assert not spec.ownership, (
                    f"{table_name}.{spec.column} is an ownership link to "
                    f"{spec.target_table}, which is not restored at all"
                )
                continue

            if spec.target_table == table_name:
                continue  # Self-reference, ordered by the topological sort.

            assert position[spec.target_table] < position[table_name], (
                f"{table_name}.{spec.column} references {spec.target_table}, "
                "which must be restored first"
            )


@pytest.mark.anyio
async def test_backup_archives_the_master_audio_not_the_playback_proxy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression guard for the proxy-shadowing bug. Every processed recording has a
    # master <uuid>.wav and a playback proxy <uuid>.mp3 sitting next to it, sharing a
    # filename stem. The old stem-keyed directory walk had to guess between them and
    # frequently archived the proxy, which is mono and doubly lossy, as the master.
    context = build_test_context(tmp_path / "source")
    patch_backup_manager(monkeypatch, context)
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "source-encryption-key")

    recordings_dir = context.path_manager.recordings_directory
    master = recordings_dir / "quarterly-planning.wav"
    proxy = recordings_dir / "quarterly-planning.mp3"
    master.write_bytes(b"MASTER-AUDIO")
    proxy.write_bytes(b"PROXY-AUDIO")

    await seed_source_data(
        context.async_session_maker,
        recording_meeting_uid="meeting-uid-proxy",
        recording_audio_path=str(master),
        recording_proxy_path=str(proxy),
    )

    zip_path, warnings = await BackupManager.create_backup(
        include_audio=True,
        archive_quality=backup_manager_module.ARCHIVE_QUALITY_ORIGINAL,
    )

    with zipfile.ZipFile(zip_path, "r") as archive:
        members = [name for name in archive.namelist() if name.startswith("recordings/")]
        assert members == ["recordings/quarterly-planning.wav"]
        assert archive.read("recordings/quarterly-planning.wav") == b"MASTER-AUDIO"

    assert warnings["recordings_without_audio"] == 0

    await context.async_engine.dispose()


@pytest.mark.anyio
async def test_compressed_quality_reencodes_while_original_stores_bytes_verbatim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_test_context(tmp_path / "source")
    patch_backup_manager(monkeypatch, context)
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "source-encryption-key")

    master = context.path_manager.recordings_directory / "quarterly-planning.wav"
    master.write_bytes(b"MASTER-AUDIO")

    encoded = tmp_path / "encoded.opus"
    encoded.write_bytes(b"OPUS-AUDIO")
    monkeypatch.setattr(
        BackupManager,
        "_compress_to_opus",
        staticmethod(lambda source: str(encoded)),
    )

    await seed_source_data(
        context.async_session_maker,
        recording_meeting_uid="meeting-uid-quality",
        recording_audio_path=str(master),
        recording_proxy_path=None,
    )

    compressed_zip, _ = await BackupManager.create_backup(
        include_audio=True,
        archive_quality=backup_manager_module.ARCHIVE_QUALITY_COMPRESSED,
    )

    with zipfile.ZipFile(compressed_zip, "r") as archive:
        # The member carries the re-encoded extension, and the recording row's
        # audio_path agrees with it so the restore can find the file again.
        assert archive.read("recordings/quarterly-planning.opus") == b"OPUS-AUDIO"
        rows = json.loads(archive.read("recordings.json"))
        assert rows[0]["audio_path"] == "recordings/quarterly-planning.opus"
        info = json.loads(archive.read("backup_info.json"))
        assert info["archive_quality"] == "compressed"
        assert info["format_version"] == backup_manager_module.BACKUP_FORMAT_VERSION

    await context.async_engine.dispose()


@pytest.mark.anyio
async def test_backup_counts_recordings_whose_audio_is_missing_from_disk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The metadata is still worth preserving, but the operator has to be told that the
    # archive will not restore playable audio for these recordings.
    context = build_test_context(tmp_path / "source")
    patch_backup_manager(monkeypatch, context)
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "source-encryption-key")

    await seed_source_data(
        context.async_session_maker,
        recording_meeting_uid="meeting-uid-missing",
        recording_audio_path="data/recordings/never-written.wav",
        recording_proxy_path=None,
    )

    zip_path, warnings = await BackupManager.create_backup(include_audio=True)

    assert warnings["recordings_without_audio"] == 1
    with zipfile.ZipFile(zip_path, "r") as archive:
        assert not [n for n in archive.namelist() if n.startswith("recordings/")]
        # The row survives regardless.
        assert len(json.loads(archive.read("recordings.json"))) == 1
        assert json.loads(archive.read("backup_info.json"))["warnings"][
            "recordings_without_audio"
        ] == 1

    await context.async_engine.dispose()


@pytest.mark.anyio
async def test_restore_refuses_an_archive_from_a_newer_format_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Failing at the door beats failing deep in the insert loop with a confusing error.
    context = build_test_context(tmp_path / "target")
    patch_backup_manager(monkeypatch, context)

    backup_zip = tmp_path / "future.zip"
    with zipfile.ZipFile(backup_zip, "w") as archive:
        archive.writestr(
            "backup_info.json",
            json.dumps(
                {
                    "format_version": backup_manager_module.BACKUP_FORMAT_VERSION + 1,
                    "version": "99.0.0",
                }
            ),
        )

    job_id = "future-format-job"
    BackupManager.restore_jobs[job_id] = {
        "status": "pending",
        "progress": "Queued",
        "error": None,
    }

    await BackupManager.restore_backup(job_id, str(backup_zip))

    assert BackupManager.restore_jobs[job_id]["status"] == "failed"
    assert "archive format version" in BackupManager.restore_jobs[job_id]["error"]

    await context.async_engine.dispose()


@pytest.mark.anyio
async def test_restore_accepts_a_legacy_archive_without_a_format_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Archives written before format_version existed must keep restoring.
    context = build_test_context(tmp_path / "target")
    patch_backup_manager(monkeypatch, context)

    backup_zip = tmp_path / "legacy.zip"
    with zipfile.ZipFile(backup_zip, "w") as archive:
        archive.writestr("backup_info.json", json.dumps({"version": "0.5.0"}))
        archive.writestr("users.json", json.dumps([]))

    job_id = "legacy-format-job"
    BackupManager.restore_jobs[job_id] = {
        "status": "pending",
        "progress": "Queued",
        "error": None,
    }

    await BackupManager.restore_backup(job_id, str(backup_zip))

    assert BackupManager.restore_jobs[job_id]["status"] == "completed"

    await context.async_engine.dispose()


@pytest.mark.parametrize(
    ("older", "newer"),
    [("0.9.0", "0.10.0"), ("1.2.3", "1.10.0"), ("0.6.0", "1.0.0")],
)
def test_version_parsing_orders_releases_numerically(older: str, newer: str) -> None:
    # String comparison put 0.10.0 below 0.9.0, so the newer-backup warning misfired on
    # most real version bumps.
    assert BackupManager._parse_version(older) < BackupManager._parse_version(newer)


@pytest.mark.anyio
async def test_documents_round_trip_with_their_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Attached documents were previously absent from the archive entirely: neither the
    # table nor the files on disk were carried, so every restore lost them silently.
    source_context = build_test_context(tmp_path / "source")
    patch_backup_manager(monkeypatch, source_context)
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "source-encryption-key")

    attachment = source_context.path_manager.documents_directory / "agenda.pdf"
    attachment.write_bytes(b"%PDF-1.4 agenda")

    await seed_source_data(
        source_context.async_session_maker,
        recording_meeting_uid="meeting-uid-docs",
        recording_audio_path="data/recordings/quarterly-planning.wav",
        recording_proxy_path=None,
    )
    async with source_context.async_session_maker() as session:
        session.add(
            TestDocument(
                id=1,
                recording_id=40,
                title="Agenda.pdf",
                file_path=str(attachment),
                file_type="application/pdf",
            )
        )
        await session.commit()

    zip_path, warnings = await BackupManager.create_backup(include_audio=False)

    with zipfile.ZipFile(zip_path, "r") as archive:
        assert archive.read("documents/agenda.pdf") == b"%PDF-1.4 agenda"
        rows = json.loads(archive.read("documents.json"))
        # The archived member path is what the row carries inside the backup.
        assert rows[0]["file_path"] == "documents/agenda.pdf"
    assert warnings["documents_without_files"] == 0

    target_context = build_test_context(tmp_path / "target")
    patch_backup_manager(monkeypatch, target_context)
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "target-encryption-key")

    job_id = "documents-round-trip-job"
    BackupManager.restore_jobs[job_id] = {
        "status": "pending",
        "progress": "Queued",
        "error": None,
    }

    await BackupManager.restore_backup(job_id, zip_path)

    assert BackupManager.restore_jobs[job_id]["status"] == "completed"

    with Session(target_context.sync_engine) as session:
        restored = session.exec(select(TestDocument)).one()
        restored_recording = session.exec(select(TestRecording)).one()

    assert restored.recording_id == restored_recording.id
    assert restored.title == "Agenda.pdf"
    # The file landed under the target's documents directory and survived the orphan
    # sweep, which now understands document paths as well as audio paths.
    assert os.path.exists(restored.file_path)
    assert Path(restored.file_path).read_bytes() == b"%PDF-1.4 agenda"

    await source_context.async_engine.dispose()
    await target_context.async_engine.dispose()


@pytest.mark.anyio
async def test_documents_directory_follows_the_data_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # DOCUMENTS_DIR used to be a CWD-relative literal, so documents and recordings ended
    # up in different roots on any install that moved its data directory via
    # NOJOIN_DATA_DIR, and document files fell outside the restore's extraction guard.
    from backend.utils.path_manager import PathManager as RealPathManager

    monkeypatch.delenv("DOCUMENTS_DIR", raising=False)
    monkeypatch.setenv("NOJOIN_DATA_DIR", str(tmp_path / "relocated"))

    manager = RealPathManager()

    assert manager.documents_directory.parent == manager.recordings_directory.parent
    assert manager.documents_directory == manager.user_data_directory / "documents"


@pytest.mark.anyio
async def test_restore_settles_in_flight_recordings_and_drops_foreign_task_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A recording backed up mid-processing used to restore as permanently processing,
    # carrying a celery_task_id that names a task on the source system's broker. That
    # status also blocks the canonical backfill, so the recording could never recover.
    source_context = build_test_context(tmp_path / "source")
    patch_backup_manager(monkeypatch, source_context)
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "source-encryption-key")

    await seed_source_data(
        source_context.async_session_maker,
        recording_meeting_uid="meeting-uid-inflight",
        recording_audio_path="data/recordings/in-flight.wav",
        recording_proxy_path=None,
    )
    async with source_context.async_session_maker() as session:
        recording = await session.get(TestRecording, 40)
        recording.status = "PROCESSING"
        recording.client_status = "RECORDING"
        recording.celery_task_id = "source-broker-task-id"
        recording.processing_step = "Diarizing"
        recording.processing_progress = 45
        recording.upload_progress = 100
        session.add(recording)
        session.add(TestTranscript(id=5, recording_id=40, text="Partial transcript"))
        await session.commit()

    zip_path, _ = await BackupManager.create_backup(include_audio=False)

    target_context = build_test_context(tmp_path / "target")
    patch_backup_manager(monkeypatch, target_context)
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "target-encryption-key")

    job_id = "in-flight-job"
    BackupManager.restore_jobs[job_id] = {
        "status": "pending",
        "progress": "Queued",
        "error": None,
    }

    await BackupManager.restore_backup(job_id, zip_path)

    with Session(target_context.sync_engine) as session:
        restored = session.exec(select(TestRecording)).one()

    # The transcript came across, so the recording settles as processed rather than
    # sitting in a state nothing on this installation can advance.
    assert restored.status == "PROCESSED"
    assert restored.celery_task_id is None
    assert restored.client_status is None
    assert restored.processing_step is None
    assert restored.processing_progress == 0
    assert restored.upload_progress == 0
    # Un-classified, so the existing cutover machinery treats it exactly like a legacy
    # row and rebuilds the canonical utterances from the transcript projection.
    assert restored.pipeline_generation is None

    await source_context.async_engine.dispose()
    await target_context.async_engine.dispose()


@pytest.mark.anyio
async def test_restore_marks_in_flight_recording_without_transcript_as_errored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_context = build_test_context(tmp_path / "source")
    patch_backup_manager(monkeypatch, source_context)
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "source-encryption-key")

    await seed_source_data(
        source_context.async_session_maker,
        recording_meeting_uid="meeting-uid-interrupted",
        recording_audio_path="data/recordings/interrupted.wav",
        recording_proxy_path=None,
    )
    async with source_context.async_session_maker() as session:
        recording = await session.get(TestRecording, 40)
        recording.status = "QUEUED"
        session.add(recording)
        await session.commit()

    zip_path, _ = await BackupManager.create_backup(include_audio=False)

    target_context = build_test_context(tmp_path / "target")
    patch_backup_manager(monkeypatch, target_context)
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "target-encryption-key")

    job_id = "interrupted-job"
    BackupManager.restore_jobs[job_id] = {
        "status": "pending",
        "progress": "Queued",
        "error": None,
    }

    await BackupManager.restore_backup(job_id, zip_path)

    with Session(target_context.sync_engine) as session:
        restored = session.exec(select(TestRecording)).one()

    # No transcript came across, so the operator is told to reprocess rather than being
    # shown a meeting that silently has nothing in it.
    assert restored.status == "ERROR"

    await source_context.async_engine.dispose()
    await target_context.async_engine.dispose()


@pytest.mark.anyio
async def test_recording_linked_to_a_calendar_event_survives_a_cross_system_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The headline regression. Recording.calendar_event_id was carried across verbatim
    # and never remapped, and recordings were restored before calendar_events, so on any
    # fresh target the insert violated the constraint. The savepoint swallowed it, the
    # recording vanished, and its transcript, speakers and tags went with it.
    #
    # Foreign keys are enforced in these tests, so this fails loudly without the fix.
    source_context = build_test_context(tmp_path / "source")
    patch_backup_manager(monkeypatch, source_context)
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "source-encryption-key")

    await seed_source_data(
        source_context.async_session_maker,
        recording_meeting_uid="meeting-uid-calendar-linked",
        recording_audio_path="data/recordings/calendar-linked.wav",
        recording_proxy_path=None,
    )
    async with source_context.async_session_maker() as session:
        recording = await session.get(TestRecording, 40)
        recording.calendar_event_id = 70
        session.add(recording)
        session.add(TestTranscript(id=90, recording_id=40, text="Full transcript"))
        await session.commit()

    zip_path, _ = await BackupManager.create_backup(include_audio=False)

    target_context = build_test_context(tmp_path / "target")
    patch_backup_manager(monkeypatch, target_context)
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "target-encryption-key")

    # Force every id to be reassigned, so a carried-over calendar_event_id could not
    # accidentally land on the right row.
    await seed_unrelated_target_user(
        target_context.async_session_maker, user_id=1, username="local-bob"
    )

    job_id = "calendar-linked-job"
    BackupManager.restore_jobs[job_id] = {
        "status": "pending",
        "progress": "Queued",
        "error": None,
    }

    await BackupManager.restore_backup(job_id, zip_path)

    assert BackupManager.restore_jobs[job_id]["status"] == "completed"

    with Session(target_context.sync_engine) as session:
        restored = session.exec(select(TestRecording)).one()
        restored_event = session.exec(select(TestCalendarEvent)).one()
        # The children came across too, which is what the old behaviour lost.
        assert session.exec(select(TestTranscript)).all()
        assert session.exec(select(TestRecordingSpeaker)).all()

    # The link is preserved and points at the event's new id, not the source system's.
    assert restored.calendar_event_id == restored_event.id
    assert restored_event.id != 70 or restored.calendar_event_id == restored_event.id

    await source_context.async_engine.dispose()
    await target_context.async_engine.dispose()


@pytest.mark.anyio
async def test_user_created_from_an_invitation_survives_a_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # invitations are never archived, so User.invitation_id could never resolve. Carried
    # across verbatim it violated the constraint, dropping the user, and every recording,
    # tag and person they owned was then skipped for having an unresolvable owner.
    source_context = build_test_context(tmp_path / "source")
    patch_backup_manager(monkeypatch, source_context)
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "source-encryption-key")

    async with source_context.async_session_maker() as session:
        session.add(TestInvitation(id=900, code="INVITE-900"))
        await session.flush()
        await session.commit()

    await seed_source_data(
        source_context.async_session_maker,
        recording_meeting_uid="meeting-uid-invited",
        recording_audio_path="data/recordings/invited.wav",
        recording_proxy_path=None,
    )
    async with source_context.async_session_maker() as session:
        user = await session.get(TestUser, 1)
        user.invitation_id = 900
        session.add(user)
        await session.commit()

    zip_path, _ = await BackupManager.create_backup(include_audio=False)

    target_context = build_test_context(tmp_path / "target")
    patch_backup_manager(monkeypatch, target_context)
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "target-encryption-key")

    job_id = "invited-user-job"
    BackupManager.restore_jobs[job_id] = {
        "status": "pending",
        "progress": "Queued",
        "error": None,
    }

    await BackupManager.restore_backup(job_id, zip_path)

    assert BackupManager.restore_jobs[job_id]["status"] == "completed"

    with Session(target_context.sync_engine) as session:
        restored_user = session.exec(select(TestUser)).one()
        restored_recording = session.exec(select(TestRecording)).one()

    assert restored_user.username == "alice"
    # Provenance only, and unresolvable by design, so it is dropped rather than the user.
    assert restored_user.invitation_id is None
    assert restored_recording.user_id == restored_user.id

    await source_context.async_engine.dispose()
    await target_context.async_engine.dispose()


LEGACY_ARCHIVE = Path(__file__).parent / "fixtures" / "legacy_backup_v1.zip"


@pytest.mark.anyio
async def test_legacy_format_archive_still_restores_completely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A committed archive in the pre-format_version layout: no format_version field, audio
    # stored as .opus because that was the only option, no documents.json, and no
    # pipeline_generation column. Backwards compatibility is the promise most likely to
    # break silently as the archive format evolves, so it is pinned to a real file rather
    # than to a builder that would drift alongside the writer it is meant to check.
    target_context = build_test_context(tmp_path / "target")
    patch_backup_manager(monkeypatch, target_context)
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "target-encryption-key")

    job_id = "legacy-archive-job"
    BackupManager.restore_jobs[job_id] = {
        "status": "pending",
        "progress": "Queued",
        "error": None,
    }

    await BackupManager.restore_backup(job_id, str(LEGACY_ARCHIVE))

    assert BackupManager.restore_jobs[job_id]["status"] == "completed"
    assert BackupManager.restore_jobs[job_id]["warnings"] == {"skipped": {}}

    with Session(target_context.sync_engine) as session:
        user = session.exec(select(TestUser)).one()
        recording = session.exec(select(TestRecording)).one()
        transcript = session.exec(select(TestTranscript)).one()
        speaker = session.exec(select(TestRecordingSpeaker)).one()
        tag_link = session.exec(select(TestRecordingTag)).one()

    assert user.username == "legacy-alice"
    assert recording.meeting_uid == "legacy-meeting-uid"
    # The durable identifier is preserved, so recording URLs and later backups from the
    # same source keep lining up.
    assert recording.public_id == "legacy-public-id"
    assert recording.user_id == user.id
    assert transcript.recording_id == recording.id
    assert speaker.recording_id == recording.id
    assert tag_link.recording_id == recording.id

    # The .opus audio was extracted under the target's recordings directory and survived
    # the orphan sweep.
    assert recording.audio_path.endswith("legacy-meeting.opus")
    assert Path(recording.audio_path).read_bytes() == b"LEGACY-OPUS-AUDIO"
    # Recomputed from what actually landed on disk.
    assert recording.file_size_bytes == len(b"LEGACY-OPUS-AUDIO")
    # Restored un-classified so the cutover machinery backfills it like any legacy row.
    assert recording.pipeline_generation is None

    await target_context.async_engine.dispose()
