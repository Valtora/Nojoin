"""The timeline notes API: ownership, validation, revisions and retried creates."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.api.v1.endpoints.transcripts.routes_timeline_notes as notes_routes
from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.models.user import User
from backend.tests.sqlite_schemas import (
    GLOBAL_SPEAKERS_SCHEMA,
    RECORDING_AUDIO_CHUNKS_SCHEMA,
    RECORDING_SPEAKERS_SCHEMA,
    RECORDINGS_SCHEMA,
    TIMELINE_NOTES_SCHEMA,
    TRANSCRIPTS_SCHEMA,
    USERS_SCHEMA,
)
from backend.utils.time import utc_now

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


def _user(user_id: int, username: str) -> User:
    return User(
        id=user_id,
        username=username,
        hashed_password="hashed-password",
        role="user",
        is_active=True,
        is_superuser=False,
        force_password_change=False,
    )


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
    async with engine.begin() as connection:
        for schema in (
            USERS_SCHEMA,
            RECORDINGS_SCHEMA,
            TRANSCRIPTS_SCHEMA,
            TIMELINE_NOTES_SCHEMA,
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
    application.dependency_overrides[get_current_user] = lambda: _user(OWNER_ID, "mira")
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
    assert response.status_code == 422
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
    patched = await client.patch(url, json={"expected_revision": 1, "body": "y"})
    assert patched.status_code == 404
    assert (await client.delete(url)).status_code == 404
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
    assert on_another_recording.json()["detail"] == notes_routes.NOTE_ID_TAKEN_DETAIL

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


@pytest.mark.anyio
async def test_the_recording_detail_carries_notes_and_the_live_audio_clock(
    client: AsyncClient, session_maker
) -> None:
    async with session_maker() as session:
        for schema in (
            RECORDING_AUDIO_CHUNKS_SCHEMA,
            RECORDING_SPEAKERS_SCHEMA,
            GLOBAL_SPEAKERS_SCHEMA,
            TAGS_SCHEMA,
            RECORDING_TAGS_SCHEMA,
        ):
            await session.execute(text(schema))
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
    assert [item["id"] for item in detail["timeline_notes"]] == [note["id"]]

    finished = (await client.get(f"/api/v1/recordings/{OTHER_REC}")).json()
    assert finished["live_audio_ms"] is None
    assert finished["timeline_notes"] == []


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
    app.dependency_overrides[get_current_user] = lambda: _user(OTHER_ID, "otto")
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
