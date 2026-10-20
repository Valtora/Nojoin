"""Deciding whether the cached archive still matches the installation.

A fingerprint is a digest of everything a backup would carry: the rows of every archived
table, the size and modification time of each audio and document file, the redacted
``config.json``, the calendar provider settings read from the environment, and the app
and archive format versions. Two fingerprints are equal only when an archive built now
would hold the same data as the cached one, apart from what this module deliberately
leaves out.

What it leaves out is bookkeeping that background work rewrites on a timer. The calendar
sync writes every connected calendar's tokens, cursors and timestamps every fifteen
minutes and replaces its cached events, so counting those would leave a cache that never
survives the next sync. None of it is lost by restoring an older archive: the restored
connection resyncs from the provider and fetches what changed since. Connecting or
disconnecting a calendar, or changing a calendar's selection or colour, still counts.

On PostgreSQL the row digests are computed by the database, so checking a large library
moves no row data and holds none of it in memory. Other dialects, which only the tests
use, hash the rows here instead. The two encodings never meet: a fingerprint is only
compared with one taken by the same installation.
"""

import hashlib
import json
import os
from typing import Any, Dict, List, Type

from sqlalchemy import Text, cast, func, literal_column, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlmodel import Session, SQLModel

from backend.core.backup import runtime
from backend.core.backup.format import BACKUP_FORMAT_VERSION, CALENDAR_PROVIDER_ENV_KEYS
from backend.core.backup.plans import (
    _resolve_document_source_path,
    _resolve_source_audio_path,
)
from backend.core.backup.records import _redact_sensitive_data

FINGERPRINT_IGNORED_TABLES: Dict[str, str] = {
    "calendar_events": (
        "A copy of each provider's calendar. The sync replaces it on a timer, and a "
        "restored connection fetches it again."
    ),
}

# Columns left out of a table's digest. "*" applies to every table: updated_at moves
# whenever a row is written, so it adds nothing a real column change does not.
FINGERPRINT_IGNORED_COLUMNS: Dict[str, frozenset[str]] = {
    "*": frozenset({"updated_at"}),
    "calendar_connections": frozenset(
        {
            "access_token_encrypted",
            "refresh_token_encrypted",
            "token_expires_at",
            "sync_status",
            "sync_error",
            "last_sync_started_at",
            "last_sync_completed_at",
            "last_synced_at",
        }
    ),
    "calendar_sources": frozenset(
        {"sync_cursor", "last_synced_at", "sync_window_start", "sync_window_end"}
    ),
}


def _digest_columns(table_name: str, model_cls: Type[SQLModel]) -> List[Any]:
    ignored = FINGERPRINT_IGNORED_COLUMNS["*"] | FINGERPRINT_IGNORED_COLUMNS.get(
        table_name, frozenset()
    )
    table = model_cls.__table__  # type: ignore[attr-defined]
    return [column for column in table.columns if column.name not in ignored]


def _table_digest(
    session: Session, table_name: str, model_cls: Type[SQLModel], dialect: str
) -> List[Any]:
    """Return ``[row count, digest]`` for one table, in primary-key order."""
    table = model_cls.__table__  # type: ignore[attr-defined]
    columns = _digest_columns(table_name, model_cls)
    primary_key = list(table.primary_key.columns)

    if dialect == "postgresql":
        row_digest = func.md5(cast(func.row(*columns), Text))
        statement = select(
            func.count(),
            func.md5(
                func.coalesce(
                    func.string_agg(
                        row_digest,
                        aggregate_order_by(literal_column("','"), *primary_key),
                    ),
                    "",
                )
            ),
        ).select_from(table)
        count, digest = session.execute(statement).one()
        return [int(count), digest]

    hasher = hashlib.sha256()
    count = 0
    for row in session.execute(select(*columns).order_by(*primary_key)):
        hasher.update(json.dumps(list(row), default=str).encode())
        hasher.update(b"\n")
        count += 1
    return [count, hasher.hexdigest()]


def _file_state(path: str | None) -> List[Any] | None:
    if path is None:
        return None
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return [stat.st_size, stat.st_mtime_ns]


def _audio_file_states(session: Session, recordings_dir: Any) -> List[Any]:
    recording = runtime.Recording
    rows = session.execute(
        select(recording.audio_path).order_by(recording.id)  # type: ignore[attr-defined]
    )
    return [
        [
            audio_path,
            _file_state(_resolve_source_audio_path(audio_path, recordings_dir)),
        ]
        for (audio_path,) in rows
        if audio_path
    ]


def _document_file_states(session: Session, documents_dir: Any) -> List[Any]:
    document = runtime.Document
    rows = session.execute(
        select(document.file_path).order_by(document.id)  # type: ignore[attr-defined]
    )
    return [
        [
            file_path,
            _file_state(_resolve_document_source_path(file_path, documents_dir)),
        ]
        for (file_path,) in rows
        if file_path
    ]


def _config_digest(config_path: Any) -> str | None:
    """Digest the config as the archive stores it: redacted, so a changed key is unseen."""
    try:
        raw = config_path.read_text()
    except OSError:
        return None
    try:
        redacted = _redact_sensitive_data(json.loads(raw))
    except (ValueError, AttributeError):
        # The export skips an unparseable config, so its bytes are all there is to compare.
        return hashlib.sha256(raw.encode()).hexdigest()
    return hashlib.sha256(json.dumps(redacted, sort_keys=True).encode()).hexdigest()


def _calendar_env_digest() -> str:
    """The export writes calendar provider credentials from the environment when set."""
    names = sorted(
        name
        for keys in CALENDAR_PROVIDER_ENV_KEYS.values()
        for name in keys.values()
        if name
    )
    values = [[name, os.getenv(name) or ""] for name in names]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def compute_fingerprint(
    session: Session,
    *,
    include_audio: bool,
    archive_quality: str,
    path_manager: Any,
) -> str:
    """Fingerprint what a backup with these options would contain.

    Run it inside the same snapshot as the rows it describes: the export calls it in the
    transaction that reads the tables, so the fingerprint stored with an archive is the
    archive's own.
    """
    recordings_dir = path_manager.recordings_directory
    documents_dir = runtime.documents_directory(path_manager)
    dialect = session.get_bind().dialect.name
    tables = {
        table_name: _table_digest(session, table_name, model_cls, dialect)
        for table_name, model_cls in runtime.MODELS
        if table_name not in FINGERPRINT_IGNORED_TABLES
    }
    payload = {
        "format_version": BACKUP_FORMAT_VERSION,
        "app_version": runtime.get_app_version(),
        "include_audio": include_audio,
        "archive_quality": archive_quality,
        "tables": tables,
        "audio": _audio_file_states(session, recordings_dir) if include_audio else None,
        "documents": _document_file_states(session, documents_dir),
        "config": _config_digest(path_manager.config_path),
        "calendar_env": _calendar_env_digest(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
