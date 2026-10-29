"""The timeline notes API: ownership, validation, revisions and retried creates.

The routes run against SQLite with foreign keys enforced, and the caller is
loaded through the request's own session, as ``get_current_user`` does in
production, so a route that reads the user after a rollback fails here too.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.api.v1.endpoints.transcripts.routes_timeline_notes as notes_routes
from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.models.timeline_note_public import TimelineNoteErrorCode
from backend.models.user import User
from backend.services.timeline_note_service import load_recording_timeline_notes_async
from backend.tests.sqlite_schemas import (
    DOCUMENTS_SCHEMA,
    GLOBAL_SPEAKERS_SCHEMA,
    RECORDING_AUDIO_CHUNK_CURSORS_SCHEMA,
    RECORDING_AUDIO_CHUNKS_SCHEMA,
    RECORDING_SPEAKERS_SCHEMA,
    RECORDINGS_SCHEMA,
    TIMELINE_NOTE_TOMBSTONES_SCHEMA,
    TIMELINE_NOTES_SCHEMA,
    TRANSCRIPTS_SCHEMA,
    USERS_SCHEMA,
)
from backend.utils.time import utc_now
from backend.utils.timeline_notes import timeline_notes_signature

OWNER_ID = 1
OTHER_ID = 2
REC = "rec-owned"
OTHER_REC = "rec-also-owned"
FOREIGN_REC = "rec-foreign"
BASE = f"/api/v1/transcripts/{REC}/timeline-notes"

TAGS_SCHEMA = """
CREATE TABLE tags (
    id INTEGER PRIMARY KEY, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
    name VARCHAR(255) NOT NULL, color VARCHAR(64), user_id INTEGER, parent_id INTEGER
)
"""
RECORDING_TAGS_SCHEMA = """
CREATE TABLE recording_tags (
    id INTEGER PRIMARY KEY, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
    recording_id INTEGER NOT NULL, tag_id INTEGER NOT NULL
)
"""


def _session_user(user_id: int):
    """A ``get_current_user`` that loads the user in the request's session."""

    async def _load(db: AsyncSession = Depends(get_db)) -> User:
        return (await db.execute(select(User).where(User.id == user_id))).scalar_one()

    return _load


def _enforce_foreign_keys(dbapi_connection, _record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


async def _seed(session_maker: sessionmaker) -> None:
    now = utc_now()
    async with session_maker() as session:
        for user_id, username in ((OWNER_ID, "mira"), (OTHER_ID, "otto")):
            await session.execute(
                text(
                    "INSERT INTO users (id, created_at, updated_at, username, "
                    "hashed_password, is_active, is_superuser, force_password_change, "
                    "role, token_version, has_seen_demo_recording) VALUES "
                    "(:id, :now, :now, :username, 'x', 1, 0, 0, 'user', 0, 0)"
                ),
                {"id": user_id, "now": now, "username": username},
            )
        for row_id, public_id, user_id in (
            (10, REC, OWNER_ID),
            (11, OTHER_REC, OWNER_ID),
            (12, FOREIGN_REC, OTHER_ID),
        ):
            await session.execute(
                text(
                    "INSERT INTO recordings (id, created_at, updated_at, name, "
                    "public_id, meeting_uid, audio_path, status, upload_progress, "
                    "processing_progress, is_archived, is_deleted, user_id) VALUES "
                    "(:id, :now, :now, 'Meeting', :public_id, :public_id, :path, "
                    "'PROCESSED', 100, 100, 0, 0, :user_id)"
                ),
                {
                    "id": row_id,
                    "now": now,
                    "public_id": public_id,
                    "path": f"/data/{public_id}.wav",
                    "user_id": user_id,
                },
            )
        await session.commit()


@pytest.fixture
async def session_maker():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    event.listen(engine.sync_engine, "connect", _enforce_foreign_keys)
    async with engine.begin() as connection:
        for schema in (
            USERS_SCHEMA,
            RECORDINGS_SCHEMA,
            TRANSCRIPTS_SCHEMA,
            TIMELINE_NOTES_SCHEMA,
            TIMELINE_NOTE_TOMBSTONES_SCHEMA,
            DOCUMENTS_SCHEMA,
        ):
            await connection.execute(text(schema))
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await _seed(maker)
    try:
        yield maker
    finally:
        await engine.dispose()


@pytest.fixture
def edge_refreshes(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Every Meeting Edge refresh a notes write asked for, by recording row id."""
    refreshed: list[int] = []

    async def _record(recording_id: int, *, enabled: bool = True) -> None:
        refreshed.append(recording_id)

    monkeypatch.setattr(notes_routes, "_dispatch_meeting_edge_refresh", _record)
    return refreshed


@pytest.fixture
async def app(session_maker, edge_refreshes) -> FastAPI:
    application = FastAPI()
    application.include_router(api_router, prefix="/api/v1")

    async def _db():
        async with session_maker() as session:
            yield session

    application.dependency_overrides[get_db] = _db
    application.dependency_overrides[get_current_user] = _session_user(OWNER_ID)
    return application


@pytest.fixture
async def client(app: FastAPI):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as async_client:
        yield async_client


async def _create(client: AsyncClient, **payload) -> dict:
    response = await client.post(BASE, json={"body": "A note", **payload})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.anyio
async def test_each_kind_is_created_and_listed_in_timeline_order(
    client: AsyncClient, edge_refreshes: list[int]
) -> None:
    tail = await _create(client, kind="tail", body="  Follow up.  ")
    late = await _create(client, kind="point", anchor_ms=90_000)
    quote = await _create(
        client,
        kind="selection",
        start_ms=4_000,
        end_ms=6_500,
        quoted_text=" forty thousand ",
    )
    head = await _create(client, kind="head")

    assert tail["body"] == "Follow up."
    assert quote["quoted_text"] == "forty thousand"
    assert head["recording_id"] == REC
    assert head["created_by"] == {"id": OWNER_ID, "username": "mira"}
    assert head["revision"] == 1
    assert (head["anchor_ms"], head["start_ms"], head["end_ms"]) == (None, None, None)

    listed = (await client.get(BASE)).json()
    assert [note["id"] for note in listed] == [
        head["id"],
        quote["id"],
        late["id"],
        tail["id"],
    ]
    assert edge_refreshes == [10, 10, 10, 10]


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "point", "body": "x"},
        {"kind": "point", "body": "x", "anchor_ms": 1, "start_ms": 0, "end_ms": 2},
        {"kind": "point", "body": "x", "anchor_ms": -1},
        {"kind": "point", "body": "x", "anchor_ms": 2**53},
        {
            "kind": "selection",
            "body": "x",
            "start_ms": 5,
            "end_ms": 5,
            "quoted_text": "q",
        },
        {"kind": "selection", "body": "x", "start_ms": 1, "end_ms": 5},
        {
            "kind": "selection",
            "body": "x",
            "start_ms": 1,
            "end_ms": 5,
            "quoted_text": "  ",
        },
        {"kind": "selection", "body": "x", "anchor_ms": 1, "quoted_text": "q"},
        {"kind": "head", "body": "x", "anchor_ms": 0},
        {"kind": "tail", "body": "x", "start_ms": 0, "end_ms": 1},
        {"kind": "head", "body": "   "},
        {"kind": "head", "body": "x" * 50_001},
        {"kind": "head", "body": "x", "revision": 4},
        {"kind": "margin", "body": "x"},
    ],
)
@pytest.mark.anyio
async def test_a_note_of_the_wrong_shape_is_refused(
    client: AsyncClient, edge_refreshes: list[int], payload: dict
) -> None:
    response = await client.post(BASE, json=payload)
    assert response.status_code == 422, response.text
    assert (await client.get(BASE)).json() == []
    assert edge_refreshes == []


@pytest.mark.anyio
async def test_another_accounts_recording_is_not_found(client: AsyncClient) -> None:
    foreign = f"/api/v1/transcripts/{FOREIGN_REC}/timeline-notes"
    assert (await client.get(foreign)).status_code == 404
    response = await client.post(foreign, json={"kind": "head", "body": "x"})
    assert response.status_code == 404


@pytest.mark.anyio
async def test_a_note_is_only_found_on_its_own_recording(client: AsyncClient) -> None:
    note = await _create(client, kind="head")
    elsewhere = f"/api/v1/transcripts/{OTHER_REC}/timeline-notes/{note['id']}"
    patched = await client.patch(elsewhere, json={"expected_revision": 1, "body": "y"})
    assert patched.status_code == 404
    assert (await client.delete(elsewhere)).status_code == 404
    assert (await client.get(BASE)).json()[0]["body"] == "A note"


@pytest.mark.anyio
async def test_an_edit_bumps_the_revision_and_a_stale_one_gets_the_current_note(
    client: AsyncClient, edge_refreshes: list[int]
) -> None:
    note = await _create(client, kind="point", anchor_ms=1_000)
    url = f"{BASE}/{note['id']}"

    first = await client.patch(
        url, json={"expected_revision": 1, "body": "Edited", "anchor_ms": 2_000}
    )
    assert first.status_code == 200
    edited = first.json()
    assert (edited["body"], edited["anchor_ms"], edited["revision"]) == (
        "Edited",
        2_000,
        2,
    )
    assert edited["updated_at"] >= note["updated_at"]

    # A second device still holding revision 1.
    stale = await client.patch(url, json={"expected_revision": 1, "body": "Mine"})
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["code"] == "revision_conflict"
    assert detail["message"] == notes_routes.NOTE_REVISION_CONFLICT_DETAIL
    assert detail["note"]["body"] == "Edited"
    assert detail["note"]["revision"] == 2
    assert edge_refreshes == [10, 10]


@pytest.mark.anyio
async def test_an_edit_that_changes_nothing_keeps_the_revision(
    client: AsyncClient, edge_refreshes: list[int]
) -> None:
    note = await _create(client, kind="point", anchor_ms=1_000, body="Same")
    url = f"{BASE}/{note['id']}"

    repeat = await client.patch(
        url, json={"expected_revision": 1, "body": "  Same\n", "anchor_ms": 1_000}
    )

    assert repeat.status_code == 200
    assert repeat.json()["revision"] == 1
    assert repeat.json()["updated_at"] == note["updated_at"]
    # Only the create asked for a refresh.
    assert edge_refreshes == [10]
    # Another device still holding revision 1 can save.
    after = await client.patch(url, json={"expected_revision": 1, "body": "New"})
    assert after.json()["revision"] == 2


@pytest.mark.parametrize(
    ("sent", "stored"),
    [
        # What the frontend's stripNote mirrors: Python's str.strip().
        ("\x1c\x85 note \u3000", "note"),
        ("\ufeffnote", "\ufeffnote"),
    ],
)
@pytest.mark.anyio
async def test_a_body_is_trimmed_as_python_strips(
    client: AsyncClient, sent: str, stored: str
) -> None:
    note = await _create(client, kind="head", body=sent)
    assert note["body"] == stored


@pytest.mark.anyio
async def test_only_a_point_note_can_be_moved(client: AsyncClient) -> None:
    note = await _create(
        client, kind="selection", start_ms=0, end_ms=10, quoted_text="q"
    )
    response = await client.patch(
        f"{BASE}/{note['id']}", json={"expected_revision": 1, "anchor_ms": 5}
    )
    assert (response.status_code, _code(response)) == (422, "note_not_movable")
    assert response.json()["detail"]["message"] == "Only a point note can be moved"
    assert (await client.get(BASE)).json()[0]["revision"] == 1


@pytest.mark.parametrize(
    "payload",
    [{"expected_revision": 1}, {"body": "x"}, {"expected_revision": 1, "kind": "tail"}],
)
@pytest.mark.anyio
async def test_an_edit_must_name_its_revision_and_change_something(
    client: AsyncClient, payload: dict
) -> None:
    note = await _create(client, kind="head")
    response = await client.patch(f"{BASE}/{note['id']}", json=payload)
    assert response.status_code == 422


@pytest.mark.anyio
async def test_a_deleted_note_is_gone_for_edits_and_deletes(
    client: AsyncClient, edge_refreshes: list[int]
) -> None:
    note = await _create(client, kind="tail")
    url = f"{BASE}/{note['id']}"
    assert (await client.delete(url)).status_code == 204
    assert (await client.get(BASE)).json() == []
    # Neither names a token, so neither is the delete's own retry.
    patched = await client.patch(url, json={"expected_revision": 1, "body": "y"})
    assert patched.status_code == 410
    assert patched.json()["detail"]["code"] == "note_deleted_elsewhere"
    again = await client.delete(url)
    assert again.status_code == 410
    assert again.json()["detail"]["code"] == "note_deleted_elsewhere"
    assert edge_refreshes == [10, 10]


@pytest.mark.anyio
async def test_a_retried_create_returns_the_first_note(
    client: AsyncClient, edge_refreshes: list[int]
) -> None:
    client_id = str(uuid4())
    first = await _create(client, id=client_id, kind="point", anchor_ms=3_000)
    assert first["id"] == client_id

    retry = await client.post(
        BASE,
        json={"id": client_id, "kind": "point", "anchor_ms": 3_000, "body": "A note"},
    )
    assert retry.status_code == 200
    assert retry.json() == first
    assert len((await client.get(BASE)).json()) == 1
    assert edge_refreshes == [10]


@pytest.mark.anyio
async def test_a_client_id_in_use_elsewhere_is_refused(
    client: AsyncClient, app: FastAPI
) -> None:
    client_id = str(uuid4())
    await _create(client, id=client_id, kind="head")

    on_another_recording = await client.post(
        f"/api/v1/transcripts/{OTHER_REC}/timeline-notes",
        json={"id": client_id, "kind": "head", "body": "x"},
    )
    assert on_another_recording.status_code == 409
    assert on_another_recording.json()["detail"]["code"] == "note_id_in_use"

    malformed = await client.post(
        BASE, json={"id": "not-a-uuid", "kind": "head", "body": "x"}
    )
    assert malformed.status_code == 422


@pytest.mark.anyio
async def test_a_note_whose_author_is_gone_reads_without_an_author(
    client: AsyncClient, session_maker
) -> None:
    async with session_maker() as session:
        await session.execute(
            text(
                "INSERT INTO timeline_notes (created_at, updated_at, public_id, "
                "recording_id, created_by_user_id, kind, body, revision) VALUES "
                "(:at, :at, :pid, 10, NULL, 'head', 'Orphaned', 1)"
            ),
            {"at": datetime(2026, 1, 1) - timedelta(days=1), "pid": str(uuid4())},
        )
        await session.commit()
    listed = (await client.get(BASE)).json()
    assert listed[0]["created_by"] is None
    assert listed[0]["body"] == "Orphaned"


async def _add_detail_tables(session_maker) -> None:
    """The tables the recording detail read joins, beyond the notes fixture's."""
    async with session_maker() as session:
        for schema in (
            RECORDING_AUDIO_CHUNKS_SCHEMA,
            RECORDING_AUDIO_CHUNK_CURSORS_SCHEMA,
            RECORDING_SPEAKERS_SCHEMA,
            GLOBAL_SPEAKERS_SCHEMA,
            TAGS_SCHEMA,
            RECORDING_TAGS_SCHEMA,
        ):
            await session.execute(text(schema))
        await session.commit()


@pytest.mark.anyio
async def test_the_recording_detail_carries_a_notes_signature_and_the_live_audio_clock(
    client: AsyncClient, session_maker
) -> None:
    await _add_detail_tables(session_maker)
    async with session_maker() as session:
        await session.execute(
            text(
                "UPDATE recordings SET status = 'UPLOADING', "
                "client_status = 'PAUSED' WHERE id = 10"
            )
        )
        for sequence_no, (start_ms, end_ms) in enumerate(
            ((0, 2_000), (2_000, 4_000), (4_000, 5_500))
        ):
            await session.execute(
                text(
                    "INSERT INTO recording_audio_chunks (created_at, updated_at, "
                    "public_id, recording_id, sequence_no, source_kind, "
                    "absolute_start_ms, absolute_end_ms, duration_ms, sample_rate_hz, "
                    "channel_count, byte_size, sha256, storage_path, upload_status, "
                    "received_at) VALUES (:at, :at, :pid, 10, :seq, 'mixed', :start, "
                    ":end, :duration, 48000, 1, 1, 'x', :path, 'stored', :at)"
                ),
                {
                    "at": utc_now() - timedelta(seconds=30),
                    "pid": str(uuid4()),
                    "seq": sequence_no,
                    "start": start_ms,
                    "end": end_ms,
                    "duration": end_ms - start_ms,
                    "path": f"/chunks/{sequence_no}",
                },
            )
        await session.commit()
    note = await _create(client, kind="point", anchor_ms=5_000)

    detail = (await client.get(f"/api/v1/recordings/{REC}")).json()
    # Paused: the clock reads exactly the end of the audio it has.
    assert detail["live_audio_ms"] == 5_500
    # The notes come from their own list, fetched when this signature changes.
    assert "timeline_notes" not in detail
    assert detail["timeline_notes_list_signature"].startswith("1:")
    assert [item["id"] for item in (await client.get(BASE)).json()] == [note["id"]]

    finished = (await client.get(f"/api/v1/recordings/{OTHER_REC}")).json()
    assert finished["live_audio_ms"] is None
    assert finished["timeline_notes_list_signature"].startswith("0:")


@pytest.mark.anyio
async def test_just_after_a_resume_the_live_clock_runs_on_from_the_resume(
    client: AsyncClient, session_maker, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _add_detail_tables(session_maker)
    now = utc_now()
    async with session_maker() as session:
        # Its audio ends at 5.5 s, the last segment having arrived 30 s ago,
        # before a pause; capture resumed 300 ms ago, and no segment has landed
        # since.
        await session.execute(
            text(
                "UPDATE recordings SET status = 'UPLOADING', "
                "client_status = 'RECORDING', last_activity_at = :resumed "
                "WHERE id = 10"
            ),
            {"resumed": now - timedelta(milliseconds=300)},
        )
        for sequence_no, (start_ms, end_ms) in enumerate(((0, 2_000), (2_000, 5_500))):
            await session.execute(
                text(
                    "INSERT INTO recording_audio_chunks (created_at, updated_at, "
                    "public_id, recording_id, sequence_no, source_kind, "
                    "absolute_start_ms, absolute_end_ms, duration_ms, sample_rate_hz, "
                    "channel_count, byte_size, sha256, storage_path, upload_status, "
                    "received_at) VALUES (:at, :at, :pid, 10, :seq, 'mixed', :start, "
                    ":end, :duration, 48000, 1, 1, 'x', :path, 'stored', :at)"
                ),
                {
                    "at": now - timedelta(seconds=30),
                    "pid": str(uuid4()),
                    "seq": sequence_no,
                    "start": start_ms,
                    "end": end_ms,
                    "duration": end_ms - start_ms,
                    "path": f"/chunks/{sequence_no}",
                },
            )
        await session.commit()

    # Request/DB latency must not advance the 300 ms this scenario specifies.
    monkeypatch.setattr(
        "backend.api.v1.endpoints.recordings.routes_query.utc_now", lambda: now
    )
    detail = (await client.get(f"/api/v1/recordings/{REC}")).json()

    # Not 8 000: run on from the segment before the pause, the clock would sit
    # at its 2.5 s cap ahead of the audio until the next segment lands.
    assert detail["live_audio_ms"] == 5_800


@pytest.mark.anyio
async def test_an_unchanged_poll_carries_no_note_bodies_and_each_write_changes_it(
    client: AsyncClient, session_maker
) -> None:
    await _add_detail_tables(session_maker)

    async def poll() -> tuple[str, str]:
        response = await client.get(f"/api/v1/recordings/{REC}")
        assert response.status_code == 200, response.text
        return response.text, response.json()["timeline_notes_list_signature"]

    _, empty = await poll()
    note = await _create(
        client, kind="point", anchor_ms=1_000, body="Dana owns the budget"
    )
    first_text, created = await poll()
    assert created != empty
    second_text, unchanged = await poll()
    assert unchanged == created
    for body in (first_text, second_text):
        assert "Dana owns the budget" not in body
        assert note["id"] not in body

    url = f"{BASE}/{note['id']}"
    fenced = await client.patch(
        url,
        json={
            "expected_revision": 1,
            "body": "Dana owns the budget",
            "write_token": str(uuid4()),
        },
    )
    assert fenced.json()["revision"] == 2
    # The list shows the fence's revision and token, so it must be read again,
    # unlike the prompts' signature, which ignores it.
    _, after_fence = await poll()
    assert after_fence != created

    edited = await client.patch(
        url, json={"expected_revision": 2, "body": "Sam owns it"}
    )
    assert edited.status_code == 200
    _, after_edit = await poll()
    assert after_edit not in {created, after_fence}

    assert (await client.delete(url)).status_code == 204
    _, after_delete = await poll()
    assert after_delete == empty


@pytest.mark.anyio
async def test_two_racing_retries_of_one_create_both_get_the_note(
    client: AsyncClient,
    session_maker,
    monkeypatch: pytest.MonkeyPatch,
    edge_refreshes: list[int],
) -> None:
    # The other retry commits between this one's check and its commit, so this
    # one's insert hits the unique index and has to find the note it lost to.
    client_id = str(uuid4())
    original = notes_routes._existing_for_retry
    calls = 0

    async def _racing(db, recording_pk, note_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            async with session_maker() as other:
                await other.execute(
                    text(
                        "INSERT INTO timeline_notes (created_at, updated_at, "
                        "public_id, recording_id, created_by_user_id, kind, body, "
                        "revision) VALUES (:at, :at, :pid, 10, 1, 'head', "
                        "'From the other retry', 1)"
                    ),
                    {"at": utc_now(), "pid": client_id},
                )
                await other.commit()
            return None
        return await original(db, recording_pk, note_id)

    monkeypatch.setattr(notes_routes, "_existing_for_retry", _racing)
    response = await client.post(
        BASE, json={"id": client_id, "kind": "head", "body": "From this retry"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["body"] == "From the other retry"
    assert len((await client.get(BASE)).json()) == 1
    assert edge_refreshes == []


@pytest.mark.anyio
async def test_another_accounts_note_is_not_found_for_edits_and_deletes(
    client: AsyncClient, app: FastAPI
) -> None:
    note = await _create(client, kind="head")
    app.dependency_overrides[get_current_user] = _session_user(OTHER_ID)
    url = f"{BASE}/{note['id']}"
    patched = await client.patch(url, json={"expected_revision": 1, "body": "Mine"})
    assert patched.status_code == 404
    assert (await client.delete(url)).status_code == 404
    # A create reusing the id on one's own recording is refused as taken, by the
    # unique index rather than by reading the other account's note.
    taken = await client.post(
        f"/api/v1/transcripts/{FOREIGN_REC}/timeline-notes",
        json={"id": note["id"], "kind": "head", "body": "x"},
    )
    assert taken.status_code == 409


@pytest.mark.anyio
async def test_a_delete_naming_a_stale_revision_is_refused_with_the_current_note(
    client: AsyncClient, edge_refreshes: list[int]
) -> None:
    note = await _create(client, kind="head", body="Original")
    url = f"{BASE}/{note['id']}"
    await client.patch(url, json={"expected_revision": 1, "body": "Theirs"})

    stale = await client.delete(url, params={"expected_revision": 1})
    assert stale.status_code == 409
    assert stale.json()["detail"]["note"]["body"] == "Theirs"
    assert len((await client.get(BASE)).json()) == 1

    assert (
        await client.delete(url, params={"expected_revision": 2})
    ).status_code == 204
    assert (await client.get(BASE)).json() == []
    assert edge_refreshes == [10, 10, 10]


# Write tokens, tombstones and error codes: a client that loses a response
# learns what happened from the server.

CODES_PATH = Path(__file__).parent / "fixtures" / "timeline_note_error_codes.json"


def _code(response) -> str:
    return response.json()["detail"]["code"]


async def _tombstones(session_maker) -> list[tuple]:
    async with session_maker() as session:
        rows = await session.execute(
            text(
                "SELECT public_id, recording_id, deleted_write_token "
                "FROM timeline_note_tombstones ORDER BY id"
            )
        )
        return [tuple(row) for row in rows]


def test_the_error_codes_are_the_ones_the_frontend_pins() -> None:
    # frontend/src/lib/notes/noteErrorCodes.test.ts reads the same file.
    pinned = json.loads(CODES_PATH.read_text())
    assert sorted(code.value for code in TimelineNoteErrorCode) == sorted(pinned)


@pytest.mark.anyio
async def test_each_write_records_its_token_and_a_tokenless_repeat_keeps_it(
    client: AsyncClient,
) -> None:
    created_by, edited_by = str(uuid4()), str(uuid4())
    note = await _create(client, kind="head", write_token=created_by)
    assert note["last_write_token"] == created_by
    url = f"{BASE}/{note['id']}"

    edited = await client.patch(
        url, json={"expected_revision": 1, "body": "Edited", "write_token": edited_by}
    )
    assert edited.json()["last_write_token"] == edited_by
    assert (await client.get(BASE)).json()[0]["last_write_token"] == edited_by

    # A repeat without a token makes no revision, so the token stays its maker's.
    repeat = await client.patch(url, json={"expected_revision": 2, "body": "Edited"})
    assert (repeat.json()["revision"], repeat.json()["last_write_token"]) == (
        2,
        edited_by,
    )

    stale = await client.patch(
        url, json={"expected_revision": 1, "body": "Late", "write_token": created_by}
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["note"]["last_write_token"] == edited_by


@pytest.mark.anyio
async def test_a_repeat_with_a_token_makes_a_revision_that_fences_off_a_late_edit(
    client: AsyncClient, edge_refreshes: list[int]
) -> None:
    # An edit to "AB" went unanswered and has not landed. The box is typed back
    # to "A", which is resent under the same token, as the store does.
    token = str(uuid4())
    note = await _create(client, kind="head", body="A")
    url = f"{BASE}/{note['id']}"

    resent = await client.patch(
        url, json={"expected_revision": 1, "body": "A", "write_token": token}
    )
    assert resent.status_code == 200
    assert (resent.json()["revision"], resent.json()["last_write_token"]) == (2, token)

    # The edit lands late, and is refused: nobody waits for that answer, and the
    # note keeps the text the box holds.
    late = await client.patch(
        url, json={"expected_revision": 1, "body": "AB", "write_token": token}
    )
    assert (late.status_code, _code(late)) == (409, "revision_conflict")
    assert [(n["body"], n["revision"]) for n in (await client.get(BASE)).json()] == [
        ("A", 2)
    ]

    # Under the token that made the revision too: a write of that token sent
    # against it may still be in transit.
    again = await client.patch(
        url, json={"expected_revision": 2, "body": "A", "write_token": token}
    )
    assert again.json()["revision"] == 3
    # No text changed, so only the create asked for a refresh.
    assert edge_refreshes == [10]


@pytest.mark.anyio
async def test_a_fence_leaves_the_notes_signature_as_it_was_and_an_edit_changes_it(
    client: AsyncClient, session_maker
) -> None:
    async def signature() -> str:
        async with session_maker() as session:
            notes = await load_recording_timeline_notes_async(session, 10)
        return timeline_notes_signature(notes.notes)

    note = await _create(client, kind="head", body="A")
    url = f"{BASE}/{note['id']}"
    before = await signature()

    fenced = await client.patch(
        url, json={"expected_revision": 1, "body": "A", "write_token": str(uuid4())}
    )
    assert fenced.json()["revision"] == 2
    # Meeting Edge compares this signature: unchanged, so no refresh skips its
    # throttle and no LLM call is made for a revision that changed nothing.
    assert await signature() == before

    edited = await client.patch(url, json={"expected_revision": 2, "body": "A, edited"})
    assert edited.status_code == 200
    assert await signature() != before


@pytest.mark.anyio
async def test_a_write_without_a_token_leaves_none(client: AsyncClient) -> None:
    note = await _create(client, kind="head", write_token=str(uuid4()))
    edited = await client.patch(
        f"{BASE}/{note['id']}", json={"expected_revision": 1, "body": "From a script"}
    )
    assert edited.json()["last_write_token"] is None


@pytest.mark.anyio
async def test_a_delete_leaves_a_tombstone_with_its_token(
    client: AsyncClient, session_maker
) -> None:
    token = str(uuid4())
    note = await _create(client, kind="head")
    response = await client.delete(
        f"{BASE}/{note['id']}", params={"expected_revision": 1, "write_token": token}
    )
    assert response.status_code == 204
    assert await _tombstones(session_maker) == [(note["id"], 10, token)]
    assert (await client.get(BASE)).json() == []


@pytest.mark.anyio
async def test_writes_to_a_deleted_note_hear_whose_delete_it_was(
    client: AsyncClient, session_maker, edge_refreshes: list[int]
) -> None:
    mine, theirs = str(uuid4()), str(uuid4())
    note = await _create(client, kind="head", id=str(uuid4()), write_token=mine)
    url = f"{BASE}/{note['id']}"
    assert (await client.delete(url, params={"write_token": mine})).status_code == 204

    def edit(token: str):
        return client.patch(
            url, json={"expected_revision": 1, "body": "y", "write_token": token}
        )

    assert (await edit(mine)).status_code == 410
    assert _code(await edit(mine)) == "note_deleted_by_you"
    assert _code(await edit(theirs)) == "note_deleted_elsewhere"

    # The delete's own retry is done; anyone else's is told it was not theirs.
    assert (await client.delete(url, params={"write_token": mine})).status_code == 204
    elsewhere = await client.delete(url, params={"write_token": theirs})
    assert (elsewhere.status_code, _code(elsewhere)) == (410, "note_deleted_elsewhere")

    # A retried create cannot bring the note back, under any token.
    for token, code in (
        (mine, "note_deleted_by_you"),
        (theirs, "note_deleted_elsewhere"),
    ):
        retry = await client.post(
            BASE,
            json={
                "id": note["id"],
                "kind": "head",
                "body": "A note",
                "write_token": token,
            },
        )
        assert (retry.status_code, _code(retry)) == (410, code)
    assert (await client.get(BASE)).json() == []
    assert len(await _tombstones(session_maker)) == 1
    # Only the create and the delete changed anything.
    assert edge_refreshes == [10, 10]


@pytest.mark.anyio
async def test_a_note_that_never_existed_is_not_found(client: AsyncClient) -> None:
    url = f"{BASE}/{uuid4()}"
    patched = await client.patch(url, json={"expected_revision": 1, "body": "y"})
    assert (patched.status_code, _code(patched)) == (404, "note_not_found")
    deleted = await client.delete(url)
    assert (deleted.status_code, _code(deleted)) == (404, "note_not_found")


@pytest.mark.anyio
async def test_a_tombstone_belongs_to_its_recording(client: AsyncClient) -> None:
    token = str(uuid4())
    note = await _create(client, kind="head", id=str(uuid4()))
    await client.delete(f"{BASE}/{note['id']}", params={"write_token": token})

    elsewhere = f"/api/v1/transcripts/{OTHER_REC}/timeline-notes"
    patched = await client.patch(
        f"{elsewhere}/{note['id']}",
        json={"expected_revision": 1, "body": "y", "write_token": token},
    )
    assert (patched.status_code, _code(patched)) == (404, "note_not_found")
    deleted = await client.delete(
        f"{elsewhere}/{note['id']}", params={"write_token": token}
    )
    assert (deleted.status_code, _code(deleted)) == (404, "note_not_found")
    # A public id names one note ever, deleted or not.
    reused = await client.post(
        elsewhere, json={"id": note["id"], "kind": "head", "body": "x"}
    )
    assert (reused.status_code, _code(reused)) == (409, "note_id_in_use")


@pytest.mark.anyio
async def test_a_tokened_delete_of_an_id_never_used_refuses_its_late_create(
    client: AsyncClient, session_maker, edge_refreshes: list[int]
) -> None:
    # A create's answer was lost and it has not landed yet when the box is
    # cleared: the delete records the id, so the create cannot land later.
    note_id, token = str(uuid4()), str(uuid4())
    deleted = await client.delete(
        f"{BASE}/{note_id}", params={"expected_revision": 1, "write_token": token}
    )
    assert deleted.status_code == 204
    assert await _tombstones(session_maker) == [(note_id, 10, token)]

    late = await client.post(
        BASE,
        json={"id": note_id, "kind": "head", "body": "Secret", "write_token": token},
    )

    assert (late.status_code, _code(late)) == (410, "note_deleted_by_you")
    assert (await client.get(BASE)).json() == []
    # No note changed, so nothing is refreshed.
    assert edge_refreshes == []


@pytest.mark.anyio
async def test_a_tokened_delete_records_no_id_it_cannot_own(
    client: AsyncClient, session_maker
) -> None:
    elsewhere = f"/api/v1/transcripts/{OTHER_REC}/timeline-notes"
    held = (
        await client.post(
            elsewhere, json={"id": str(uuid4()), "kind": "head", "body": "Theirs"}
        )
    ).json()["id"]
    token = str(uuid4())

    # An id another recording's note holds, one not in a create's form, and
    # ones a create would store differently (and so lock differently) are not
    # this recording's to record.
    for note_id in (held, "not-a-uuid", held.upper(), str(uuid4()).upper()):
        response = await client.delete(
            f"{BASE}/{note_id}", params={"write_token": token}
        )
        assert (response.status_code, _code(response)) == (404, "note_not_found")

    assert await _tombstones(session_maker) == []
    assert (await client.delete(f"{elsewhere}/{held}")).status_code == 204


async def _never_held(session_maker) -> dict[str, bool]:
    async with session_maker() as session:
        rows = await session.execute(
            text("SELECT public_id, never_held FROM timeline_note_tombstones")
        )
        return {public_id: bool(never_held) for public_id, never_held in rows}


async def _age_tombstone(session_maker, note_id: str, age: timedelta) -> None:
    async with session_maker() as session:
        await session.execute(
            text(
                "UPDATE timeline_note_tombstones SET deleted_at = :at "
                "WHERE public_id = :note_id"
            ),
            {"at": utc_now() - age, "note_id": note_id},
        )
        await session.commit()


@pytest.mark.anyio
async def test_a_never_held_tombstone_is_purged_once_no_create_can_still_arrive(
    client: AsyncClient, session_maker
) -> None:
    token = str(uuid4())
    held = await _create(client, kind="head", id=str(uuid4()))
    await client.delete(f"{BASE}/{held['id']}", params={"write_token": token})
    expired, recent, fresh = str(uuid4()), str(uuid4()), str(uuid4())
    for note_id in (expired, recent):
        response = await client.delete(
            f"{BASE}/{note_id}", params={"write_token": token}
        )
        assert response.status_code == 204
    assert await _never_held(session_maker) == {
        held["id"]: False,
        expired: True,
        recent: True,
    }
    window = notes_routes.NEVER_HELD_TOMBSTONE_TTL
    await _age_tombstone(session_maker, expired, window + timedelta(minutes=1))
    await _age_tombstone(session_maker, recent, window - timedelta(minutes=1))
    # A deleted note's tombstone is kept however old.
    await _age_tombstone(session_maker, held["id"], timedelta(days=365))

    # The next one recorded purges the recording's expired ones.
    assert (
        await client.delete(f"{BASE}/{fresh}", params={"write_token": token})
    ).status_code == 204

    assert await _never_held(session_maker) == {
        held["id"]: False,
        recent: True,
        fresh: True,
    }


@pytest.mark.anyio
async def test_a_recording_keeps_only_a_few_never_held_tombstones(
    client: AsyncClient, session_maker
) -> None:
    token = str(uuid4())
    cap = notes_routes.NEVER_HELD_TOMBSTONES_PER_RECORDING
    ids = [str(uuid4()) for _ in range(cap + 1)]
    for note_id in ids[:cap]:
        response = await client.delete(
            f"{BASE}/{note_id}", params={"write_token": token}
        )
        assert response.status_code == 204

    # Past the cap, an unknown id is the plain 404, and records nothing.
    over = await client.delete(f"{BASE}/{ids[cap]}", params={"write_token": token})
    assert (over.status_code, _code(over)) == (404, "note_not_found")
    assert len(await _never_held(session_maker)) == cap
    # A deleted note still leaves its tombstone, and another recording has a
    # cap of its own.
    note = await _create(client, kind="head")
    deleted = await client.delete(f"{BASE}/{note['id']}", params={"write_token": token})
    assert deleted.status_code == 204
    elsewhere = await client.delete(
        f"/api/v1/transcripts/{OTHER_REC}/timeline-notes/{uuid4()}",
        params={"write_token": token},
    )
    assert elsewhere.status_code == 204

    # Once one expires, there is room again.
    window = notes_routes.NEVER_HELD_TOMBSTONE_TTL
    await _age_tombstone(session_maker, ids[0], window + timedelta(minutes=1))
    again = await client.delete(f"{BASE}/{ids[cap]}", params={"write_token": token})
    assert again.status_code == 204


@pytest.mark.anyio
async def test_a_missing_recording_is_named_as_such(client: AsyncClient) -> None:
    foreign = f"/api/v1/transcripts/{FOREIGN_REC}/timeline-notes"
    for response in (
        await client.get(foreign),
        await client.post(foreign, json={"kind": "head", "body": "x"}),
        await client.patch(
            f"{foreign}/{uuid4()}", json={"expected_revision": 1, "body": "y"}
        ),
        await client.delete(f"{foreign}/{uuid4()}"),
    ):
        assert (response.status_code, _code(response)) == (404, "recording_not_found")


@pytest.mark.anyio
async def test_deleting_a_recording_removes_its_notes_and_tombstones(
    client: AsyncClient, session_maker
) -> None:
    kept = await client.post(
        f"/api/v1/transcripts/{OTHER_REC}/timeline-notes",
        json={"kind": "head", "body": "Another meeting's"},
    )
    await _create(client, kind="head")
    gone = await _create(client, kind="head")
    await client.delete(f"{BASE}/{gone['id']}")

    async with session_maker() as session:
        await session.execute(text("DELETE FROM recordings WHERE id = 10"))
        await session.commit()
        notes = (
            await session.execute(text("SELECT public_id FROM timeline_notes"))
        ).all()

    assert [row[0] for row in notes] == [kept.json()["id"]]
    assert await _tombstones(session_maker) == []


@pytest.fixture
def transactions_left_open(app: FastAPI, session_maker) -> list[bool]:
    """Whether each request's session still held a transaction as it ended.

    An answer given under a note id's advisory lock must end its transaction
    first, so the lock is released before the client hears back rather than at
    the session's teardown.
    """
    left_open: list[bool] = []

    async def _db():
        async with session_maker() as session:
            try:
                yield session
            finally:
                left_open.append(session.in_transaction())

    app.dependency_overrides[get_db] = _db
    return left_open


async def _deleted_with_token(client: AsyncClient, url: str = BASE) -> tuple[str, str]:
    """A note made under a client id and deleted with a token: its id and the token."""
    token = str(uuid4())
    created = await client.post(
        url,
        json={"kind": "head", "body": "x", "id": str(uuid4()), "write_token": token},
    )
    note_id = created.json()["id"]
    deleted = await client.delete(f"{url}/{note_id}", params={"write_token": token})
    assert deleted.status_code == 204
    return note_id, token


Request = Callable[[], Awaitable[Response]]


async def _replayed_create(client: AsyncClient) -> Request:
    note_id = str(uuid4())
    await _create(client, kind="head", id=note_id)
    return lambda: client.post(BASE, json={"kind": "head", "body": "x", "id": note_id})


async def _create_deleted_here(client: AsyncClient) -> Request:
    note_id, token = await _deleted_with_token(client)
    payload = {"kind": "head", "body": "x", "id": note_id, "write_token": token}
    return lambda: client.post(BASE, json=payload)


async def _create_deleted_elsewhere(client: AsyncClient) -> Request:
    other = f"/api/v1/transcripts/{OTHER_REC}/timeline-notes"
    note_id, _ = await _deleted_with_token(client, other)
    return lambda: client.post(BASE, json={"kind": "head", "body": "x", "id": note_id})


async def _retried_delete(client: AsyncClient) -> Request:
    note_id, token = await _deleted_with_token(client)
    return lambda: client.delete(f"{BASE}/{note_id}", params={"write_token": token})


async def _delete_unknown(client: AsyncClient) -> Request:
    return lambda: client.delete(f"{BASE}/{uuid4()}")


async def _delete_deleted_elsewhere(client: AsyncClient) -> Request:
    note_id, _ = await _deleted_with_token(client)
    other_token = str(uuid4())
    return lambda: client.delete(
        f"{BASE}/{note_id}", params={"write_token": other_token}
    )


async def _delete_past_the_cap(client: AsyncClient) -> Request:
    token = str(uuid4())
    for _ in range(notes_routes.NEVER_HELD_TOMBSTONES_PER_RECORDING):
        await client.delete(f"{BASE}/{uuid4()}", params={"write_token": token})
    return lambda: client.delete(f"{BASE}/{uuid4()}", params={"write_token": token})


async def _note_url(client: AsyncClient) -> str:
    return f"{BASE}/{(await _create(client, kind='head', body='A'))['id']}"


async def _stale_edit(client: AsyncClient) -> Request:
    url = await _note_url(client)
    return lambda: client.patch(url, json={"expected_revision": 2, "body": "B"})


async def _stale_delete(client: AsyncClient) -> Request:
    url = await _note_url(client)
    return lambda: client.delete(url, params={"expected_revision": 2})


async def _move_head_note(client: AsyncClient) -> Request:
    url = await _note_url(client)
    return lambda: client.patch(url, json={"expected_revision": 1, "anchor_ms": 5})


async def _tokenless_repeat(client: AsyncClient) -> Request:
    url = await _note_url(client)
    return lambda: client.patch(url, json={"expected_revision": 1, "body": "A"})


@pytest.mark.parametrize(
    ("arrange", "status_code"),
    [
        (_replayed_create, 200),
        (_create_deleted_here, 410),
        (_create_deleted_elsewhere, 409),
        (_retried_delete, 204),
        (_delete_unknown, 404),
        (_delete_deleted_elsewhere, 410),
        (_delete_past_the_cap, 404),
        (_stale_edit, 409),
        (_stale_delete, 409),
        (_move_head_note, 422),
        (_tokenless_repeat, 200),
    ],
    ids=lambda value: value.__name__.strip("_") if callable(value) else str(value),
)
@pytest.mark.anyio
async def test_every_early_answer_ends_its_transaction(
    client: AsyncClient,
    transactions_left_open: list[bool],
    arrange: Callable[[AsyncClient], Awaitable[Request]],
    status_code: int,
) -> None:
    request = await arrange(client)
    transactions_left_open.clear()

    response = await request()

    assert response.status_code == status_code, response.text
    assert transactions_left_open == [False]
