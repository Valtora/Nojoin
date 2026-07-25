"""Swappable bindings for the backup subsystem.

Every name the tests substitute lives here, in one place, and every other module reaches
them through this module rather than importing them directly. That indirection is
load-bearing: attribute lookup happens at call time, so patching ``runtime.Recording``
or ``runtime.MODELS`` reaches code in every submodule. Importing the names directly
would bind them at import time and silently ignore the patch.

The substitution exists because the production models use Postgres-specific column types
that cannot be created on SQLite, so the tests run against surrogate tables. See
``backend/tests/test_backup_manager.py``.
"""

from typing import Any, List, Tuple, Type

from sqlmodel import SQLModel

from backend.core.db import async_session_maker, sync_engine  # noqa: F401
from backend.models.calendar import (  # noqa: F401
    CalendarConnection,
    CalendarEvent,
    CalendarProvider,
    CalendarProviderConfig,
    CalendarSource,
)
from backend.models.chat import ChatMessage  # noqa: F401
from backend.models.document import Document  # noqa: F401
from backend.models.people_tag import PeopleTag, PeopleTagLink  # noqa: F401
from backend.models.recording import Recording  # noqa: F401
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker  # noqa: F401
from backend.models.tag import RecordingTag, Tag  # noqa: F401
from backend.models.task import UserTask, UserTaskRecording, UserTaskTag  # noqa: F401
from backend.models.transcript import Transcript  # noqa: F401
from backend.models.user import User  # noqa: F401
from backend.utils.path_manager import PathManager  # noqa: F401
from backend.utils.version import get_installed_version

MODELS: List[Tuple[str, Type[SQLModel]]] = [
    ("users", User),
    ("calendar_provider_configs", CalendarProviderConfig),
    ("calendar_connections", CalendarConnection),
    ("calendar_sources", CalendarSource),
    ("calendar_events", CalendarEvent),
    ("user_tasks", UserTask),
    ("p_tags", PeopleTag),
    ("global_speakers", GlobalSpeaker),
    ("people_tag_links", PeopleTagLink),
    ("tags", Tag),
    ("user_task_tags", UserTaskTag),
    ("recordings", Recording),
    ("user_task_recordings", UserTaskRecording),
    ("recording_speakers", RecordingSpeaker),
    ("recording_tags", RecordingTag),
    ("transcripts", Transcript),
    ("chat_messages", ChatMessage),
    ("documents", Document),
]


def get_app_version() -> str:
    return get_installed_version()


def documents_directory(path_manager: Any) -> Any:
    """Resolve the documents directory, tolerating path managers without it."""
    directory = getattr(path_manager, "documents_directory", None)
    if directory is not None:
        return directory
    return path_manager.user_data_directory / "documents"
