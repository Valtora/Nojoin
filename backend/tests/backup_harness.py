"""Surrogate tables and fixtures shared by the backup tests.

The production models use PostgreSQL column types SQLite cannot create, so the backup
tests run against these stand-ins, swapped in through ``backend.core.backup.runtime``.
They live in a module of their own so that more than one test file can use them:
pytest imports a test file under its bare name, and importing that file again from
another test would define every table a second time.
"""

from __future__ import annotations

import json
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
from sqlmodel import Field, SQLModel

import backend.core.backup.restore.stages as backup_stages
import backend.core.backup.runtime as backup_runtime
import backend.core.db as db_module
import backend.utils.version as version_utils
from backend.core.backup import BackupManager
from backend.core.encryption import encrypt_secret
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
    due_at: Optional[datetime] = Field(default=None, sa_type=DateTime)
    completed_at: Optional[datetime] = Field(default=None, sa_type=DateTime)
    archived_at: Optional[datetime] = Field(default=None, sa_type=DateTime)
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
    token_expires_at: Optional[datetime] = Field(default=None, sa_type=DateTime)
    sync_status: str = "idle"
    sync_error: Optional[str] = None
    last_sync_started_at: Optional[datetime] = Field(default=None, sa_type=DateTime)
    last_sync_completed_at: Optional[datetime] = Field(default=None, sa_type=DateTime)
    last_synced_at: Optional[datetime] = Field(default=None, sa_type=DateTime)


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
    last_synced_at: Optional[datetime] = Field(default=None, sa_type=DateTime)
    sync_window_start: Optional[datetime] = Field(default=None, sa_type=DateTime)
    sync_window_end: Optional[datetime] = Field(default=None, sa_type=DateTime)


class TestCalendarEvent(TestBase, table=True):
    __tablename__ = "backup_test_calendar_events"

    calendar_id: int = Field(foreign_key="backup_test_calendar_sources.id")
    provider_event_id: str
    title: str
    status: str = "confirmed"
    is_all_day: bool = Field(default=False, sa_column=Column(Boolean, nullable=False))
    starts_at: Optional[datetime] = Field(default=None, sa_type=DateTime)
    ends_at: Optional[datetime] = Field(default=None, sa_type=DateTime)
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
    external_updated_at: Optional[datetime] = Field(default=None, sa_type=DateTime)


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

    # Cascades like documents.recording_id, so an overwrite can delete a recording
    # that has documents.
    recording_id: int = Field(
        foreign_key="backup_test_recordings.id", ondelete="CASCADE"
    )
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
    monkeypatch.setattr(backup_runtime, "PathManager", lambda: context.path_manager)
    monkeypatch.setattr(version_utils, "PathManager", lambda: context.path_manager)
    monkeypatch.setattr(
        backup_runtime, "async_session_maker", context.async_session_maker
    )
    monkeypatch.setattr(backup_runtime, "sync_engine", context.sync_engine)
    monkeypatch.setattr(backup_runtime, "MODELS", TEST_MODELS)
    monkeypatch.setattr(backup_runtime, "User", TestUser)
    monkeypatch.setattr(
        backup_runtime, "CalendarProviderConfig", TestCalendarProviderConfig
    )
    monkeypatch.setattr(backup_runtime, "UserTask", TestUserTask)
    monkeypatch.setattr(backup_runtime, "PeopleTag", TestPeopleTag)
    monkeypatch.setattr(backup_runtime, "GlobalSpeaker", TestGlobalSpeaker)
    monkeypatch.setattr(backup_runtime, "PeopleTagLink", TestPeopleTagLink)
    monkeypatch.setattr(backup_runtime, "Tag", TestTag)
    monkeypatch.setattr(backup_runtime, "UserTaskTag", TestUserTaskTag)
    monkeypatch.setattr(backup_runtime, "Recording", TestRecording)
    monkeypatch.setattr(backup_runtime, "UserTaskRecording", TestUserTaskRecording)
    monkeypatch.setattr(backup_runtime, "CalendarConnection", TestCalendarConnection)
    monkeypatch.setattr(backup_runtime, "CalendarSource", TestCalendarSource)
    monkeypatch.setattr(backup_runtime, "CalendarEvent", TestCalendarEvent)
    monkeypatch.setattr(backup_runtime, "RecordingSpeaker", TestRecordingSpeaker)
    monkeypatch.setattr(backup_runtime, "RecordingTag", TestRecordingTag)
    monkeypatch.setattr(backup_runtime, "Transcript", TestTranscript)
    monkeypatch.setattr(backup_runtime, "ChatMessage", TestChatMessage)
    monkeypatch.setattr(backup_runtime, "Document", TestDocument)
    monkeypatch.setattr(
        backup_stages,
        "_enqueue_recording_finalization",
        lambda recording_id, needs_proxy=True: None,
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
