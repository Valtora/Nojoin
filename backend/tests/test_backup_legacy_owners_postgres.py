"""An archive made before 7cee4b4388a7 restores as one made after upgrading.

The source install is seeded at the revision before 7cee4b4388a7 with the cases
of ``test_backup_legacy_owners.py`` and backed up. It is then upgraded to head,
which runs 7cee4b4388a7 and f704bbd87f98, and backed up again. Each archive is
restored into an empty database at head, and the two must hold the same people,
people tags, recording tags and links. Real models, the real migration chain,
and the real export and restore.

Skips unless NOJOIN_TEST_POSTGRES_URL names a server; CI sets it for the backend
suite.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlmodel import SQLModel

import backend.core.backup.restore.stages as backup_stages
from backend.core.backup import BackupManager, runtime
from backend.core.backup import export as backup_export
from backend.core.db import driver_urls
from backend.tests.postgres_setup import migrate_empty_database
from backend.tests.test_backup_legacy_owners import (
    PEOPLE,
    PEOPLE_TAGS,
    RECORDING_TAGS,
    RECORDINGS,
    SPEAKERS,
    TAG_LINKS,
    USERS,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BEFORE = "7ff0bbdadad8"

# Today's export selects every column the models name, and BEFORE predates some
# of them. Each column a later revision adds is listed with the DDL that adds
# it, so the old archive can be made, and is dropped again before the upgrade.
COLUMNS_ADDED_AFTER_BEFORE = {
    ("recordings", "capture_epoch"): "INTEGER NOT NULL DEFAULT 0",
    ("recordings", "transcription_language"): "VARCHAR(16)",
    ("transcripts", "language_detection"): "JSONB",
    ("recording_speakers", "name_source"): "VARCHAR(16)",
    ("documents", "note_id"): "BIGINT",
    ("documents", "use_in_notes"): "BOOLEAN NOT NULL DEFAULT false",
    ("timeline_notes", "file_removed"): "BOOLEAN NOT NULL DEFAULT false",
}


def _alembic(database_url: str, *args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _psycopg_url(url: str) -> str:
    return (
        make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
    )


@pytest.fixture
def databases(postgres_test_url: str) -> Iterator[SimpleNamespace]:
    """The source install, and an empty database at head for each archive."""
    admin_url = _psycopg_url(postgres_test_url)
    suffix = uuid4().hex[:12]
    names = {role: f"nojoin_legacy_{role}_{suffix}" for role in ("src", "old", "new")}

    def url(name: str) -> str:
        return (
            make_url(admin_url).set(database=name).render_as_string(hide_password=False)
        )

    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{names["src"]}"')
        admin.execute(f'CREATE DATABASE "{names["old"]}"')
    try:
        migrate_empty_database(url(names["old"]), "head")
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'CREATE DATABASE "{names["new"]}" TEMPLATE "{names["old"]}"')
        migrate_empty_database(url(names["src"]), BEFORE)
        yield SimpleNamespace(**{role: url(name) for role, name in names.items()})
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            for name in names.values():
                admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    importlib.import_module("backend.models.registry")
    for directory in ("recordings", "documents"):
        (tmp_path / directory).mkdir()
    path_manager = SimpleNamespace(
        user_data_directory=tmp_path,
        recordings_directory=tmp_path / "recordings",
        documents_directory=tmp_path / "documents",
        config_path=tmp_path / "config.json",
    )
    monkeypatch.setattr(runtime, "PathManager", lambda: path_manager)
    monkeypatch.setattr(runtime, "get_app_version", lambda: "2.10.0")
    monkeypatch.setattr(
        backup_stages,
        "_enqueue_recording_finalization",
        lambda recording_id, needs_proxy=True: None,
    )
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "legacy-owner-key")


def _seed_before(conn: psycopg.Connection) -> None:
    for user_id, username, role in USERS:
        conn.execute(
            "INSERT INTO users (id, created_at, updated_at, username, hashed_password,"
            " is_active, is_superuser, role, force_password_change, settings)"
            " VALUES (%s, now(), now(), %s, 'x', true, %s, %s, false, '{}')",
            (user_id, username, role == "owner", role),
        )
    for recording_id, owner in RECORDINGS.items():
        conn.execute(
            "INSERT INTO recordings (id, created_at, updated_at, name, public_id,"
            " meeting_uid, audio_path, status, upload_progress, processing_progress,"
            " is_archived, is_deleted, user_id) VALUES (%s, now(), now(), 'm', %s,"
            " %s, %s, 'PROCESSED', 0, 0, false, false, %s)",
            (
                recording_id,
                f"pub-{recording_id}",
                f"uid-{recording_id}",
                f"/r{recording_id}.wav",
                owner,
            ),
        )
    for person_id, name, owner in PEOPLE:
        conn.execute(
            "INSERT INTO global_speakers (id, created_at, updated_at, name, color,"
            " embedding, embedding_version, user_id, is_voiceprint_locked, email)"
            " VALUES (%s, now(), now(), %s, 'teal', %s, 2, %s, false, %s)",
            (
                person_id,
                name,
                json.dumps([0.6, 0.8]),
                owner,
                f"p{person_id}@example.com",
            ),
        )
    conn.execute("SELECT setval('global_speakers_id_seq', 999)")
    for speaker_id, recording_id, person_id, merged_into in SPEAKERS:
        conn.execute(
            "INSERT INTO recording_speakers (id, created_at, updated_at, public_id,"
            " recording_id, diarization_label, global_speaker_id, merged_into_id,"
            " speaker_status, speaker_kind, identity_locked)"
            " VALUES (%s, now(), now(), %s, %s, %s, %s, %s, 'active', 'automated',"
            " false)",
            (
                speaker_id,
                f"rs-{speaker_id}",
                recording_id,
                f"SPEAKER_{speaker_id:02d}",
                person_id,
                merged_into,
            ),
        )
    for speaker_id, person_id in ((1, 101), (6, 104)):
        conn.execute(
            "INSERT INTO recording_speaker_drift_refusals"
            " (recording_speaker_id, global_speaker_id) VALUES (%s, %s)",
            (speaker_id, person_id),
        )
    _seed_tags(conn)


def _seed_tags(conn: psycopg.Connection) -> None:
    for table, rows in (("p_tags", PEOPLE_TAGS), ("tags", RECORDING_TAGS)):
        for tag_id, name, owner, _ in rows:
            conn.execute(
                f"INSERT INTO {table} (id, created_at, updated_at, name, color,"
                " user_id) VALUES (%s, now(), now(), %s, 'blue', %s)",
                (tag_id, name, owner),
            )
        for tag_id, _, _, parent in rows:
            conn.execute(
                f"UPDATE {table} SET parent_id = %s WHERE id = %s", (parent, tag_id)
            )
        conn.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), 999)")
    for link_id, person_id, tag_id in TAG_LINKS:
        conn.execute(
            "INSERT INTO people_tags (id, created_at, updated_at, global_speaker_id,"
            " tag_id) VALUES (%s, now(), now(), %s, %s)",
            (link_id, person_id, tag_id),
        )


def _add_columns_added_after_before(conn: psycopg.Connection) -> None:
    existing = set(
        conn.execute(
            "SELECT table_name, column_name FROM information_schema.columns"
            " WHERE table_schema = current_schema()"
        ).fetchall()
    )
    tables = {table for table, _ in existing}
    missing = {
        (table.name, column.name)
        for table in SQLModel.metadata.sorted_tables
        if table.name in tables
        for column in table.columns
        if (table.name, column.name) not in existing
    }
    assert missing == set(COLUMNS_ADDED_AFTER_BEFORE), (
        "List each column a revision after BEFORE adds in COLUMNS_ADDED_AFTER_BEFORE: "
        f"{sorted(missing)}"
    )
    for (table, column), ddl in COLUMNS_ADDED_AFTER_BEFORE.items():
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {ddl}')


def _drop_columns_added_after_before(conn: psycopg.Connection) -> None:
    for table, column in COLUMNS_ADDED_AFTER_BEFORE:
        conn.execute(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')


def _back_up(database_url: str, monkeypatch: pytest.MonkeyPatch) -> str:
    engine = create_engine(driver_urls(database_url)[1])
    monkeypatch.setattr(runtime, "sync_engine", engine)
    try:
        zip_path, _ = backup_export.create_backup_blocking(include_audio=False)
    finally:
        engine.dispose()
    return zip_path


def _restore(
    database_url: str, zip_path: str, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    engine = create_engine(driver_urls(database_url)[1])
    monkeypatch.setattr(runtime, "sync_engine", engine)
    try:
        return BackupManager.restore_backup_blocking(uuid4().hex, zip_path)
    finally:
        engine.dispose()


def _library(database_url: str) -> dict[str, Any]:
    """People, tags and links by name and owner, never by id."""
    with psycopg.connect(database_url) as conn:
        people = conn.execute(
            "SELECT u.username, p.name, p.color, p.email, p.embedding::text"
            " FROM global_speakers p JOIN users u ON u.id = p.user_id"
            " ORDER BY 1, 2"
        ).fetchall()
        speakers = conn.execute(
            "SELECT r.meeting_uid, s.diarization_label, u.username, p.name"
            " FROM recording_speakers s JOIN recordings r ON r.id = s.recording_id"
            " LEFT JOIN global_speakers p ON p.id = s.global_speaker_id"
            " LEFT JOIN users u ON u.id = p.user_id ORDER BY 1, 2"
        ).fetchall()
        refusals = conn.execute(
            "SELECT s.diarization_label, u.username, p.name"
            " FROM recording_speaker_drift_refusals f"
            " JOIN recording_speakers s ON s.id = f.recording_speaker_id"
            " JOIN global_speakers p ON p.id = f.global_speaker_id"
            " JOIN users u ON u.id = p.user_id ORDER BY 1, 2, 3"
        ).fetchall()
        tag_links = conn.execute(
            "SELECT pu.username, p.name, tu.username, t.name FROM people_tags l"
            " JOIN global_speakers p ON p.id = l.global_speaker_id"
            " JOIN users pu ON pu.id = p.user_id"
            " JOIN p_tags t ON t.id = l.tag_id JOIN users tu ON tu.id = t.user_id"
            " ORDER BY 1, 2, 3, 4"
        ).fetchall()
        tags = {
            table: conn.execute(
                f"SELECT u.username, t.name, t.color, pu.username, parent.name"
                f" FROM {table} t JOIN users u ON u.id = t.user_id"
                f" LEFT JOIN {table} parent ON parent.id = t.parent_id"
                " LEFT JOIN users pu ON pu.id = parent.user_id ORDER BY 1, 2"
            ).fetchall()
            for table in ("p_tags", "tags")
        }
    return {
        "people": people,
        "speakers": speakers,
        "refusals": refusals,
        "tag_links": tag_links,
        **tags,
    }


def test_an_old_archive_restores_as_the_upgraded_install_would(
    databases: SimpleNamespace, install: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    with psycopg.connect(databases.src) as conn:
        _seed_before(conn)
        _add_columns_added_after_before(conn)
    old_archive = _back_up(databases.src, monkeypatch)
    with psycopg.connect(databases.src) as conn:
        _drop_columns_added_after_before(conn)
    _alembic(databases.src, "upgrade", "head")
    new_archive = _back_up(databases.src, monkeypatch)

    old_job = _restore(databases.old, old_archive, monkeypatch)
    new_job = _restore(databases.new, new_archive, monkeypatch)

    assert old_job["status"] == new_job["status"]
    assert old_job["warnings"] == new_job["warnings"]
    old, new = _library(databases.old), _library(databases.new)
    assert old == new
    # Not two empty libraries: the upgrade's own decisions came across.
    assert ("cara", "Fay Example (2)") in [row[:2] for row in new["people"]]
    assert ("uid-20", "SPEAKER_04", "bob", "Ben Example") in new["speakers"]
    assert ("bob", "Key accounts", "blue", "bob", "Client") in new["p_tags"]
