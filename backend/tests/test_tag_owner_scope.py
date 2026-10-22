"""Tag ids from a request are checked against the user's own tags.

People tags and recording tags each belong to one user. Creating or updating
a person links only the user's own people tags, a tag nests only under one of
the user's own, and another user's tag, a tag with no owner and a missing id
all answer 404 with the same body. Alice (user 1) is signed in; Bob is user 2.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.tests.sqlite_schemas import (
    GLOBAL_SPEAKERS_SCHEMA,
    P_TAGS_SCHEMA,
    PEOPLE_TAGS_SCHEMA,
    RECORDING_SPEAKERS_SCHEMA,
)

NOW = "2026-10-22 00:00:00"
TAGS_SCHEMA = """
CREATE TABLE tags (
    id INTEGER PRIMARY KEY, created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL, name VARCHAR NOT NULL, color VARCHAR,
    user_id INTEGER, parent_id INTEGER
)
"""

# People tags: Alice's, Bob's, and one with no owner.
ALICES_CLIENT, ALICES_LEAD, BOBS_VENDOR, NO_OWNER = 1, 2, 3, 4
# Recording tags: two of Alice's and one of Bob's.
ALICES_BOARD, ALICES_BUDGET, BOBS_HIRING = 10, 11, 20
MISSING = 999
# Alice's person, holding her Client tag.
DANA = 100

NOT_FOUND = {"detail": "Tag not found"}
PARENT_NOT_FOUND = {"detail": "Parent tag not found"}


@pytest.fixture
async def session_maker():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        for schema in (
            GLOBAL_SPEAKERS_SCHEMA,
            RECORDING_SPEAKERS_SCHEMA,
            P_TAGS_SCHEMA,
            PEOPLE_TAGS_SCHEMA,
            TAGS_SCHEMA,
        ):
            await connection.execute(text(schema))
        for tag_id, name, owner in (
            (ALICES_CLIENT, "Client", 1),
            (ALICES_LEAD, "Lead", 1),
            (BOBS_VENDOR, "Vendor", 2),
            (NO_OWNER, "Shared", None),
        ):
            await connection.execute(
                text(
                    "INSERT INTO p_tags (id, created_at, updated_at, name, user_id)"
                    " VALUES (:id, :now, :now, :name, :owner)"
                ),
                {"id": tag_id, "now": NOW, "name": name, "owner": owner},
            )
        for tag_id, name, owner in (
            (ALICES_BOARD, "Board", 1),
            (ALICES_BUDGET, "Budget", 1),
            (BOBS_HIRING, "Hiring", 2),
        ):
            await connection.execute(
                text(
                    "INSERT INTO tags (id, created_at, updated_at, name, user_id)"
                    " VALUES (:id, :now, :now, :name, :owner)"
                ),
                {"id": tag_id, "now": NOW, "name": name, "owner": owner},
            )
        await connection.execute(
            text(
                "INSERT INTO global_speakers (id, created_at, updated_at, name,"
                " user_id, company) VALUES (:id, :now, :now, 'Dana', 1, 'Before')"
            ),
            {"id": DANA, "now": NOW},
        )
        await connection.execute(
            text(
                "INSERT INTO people_tags (id, created_at, updated_at,"
                " global_speaker_id, tag_id) VALUES (1, :now, :now, :person, :tag)"
            ),
            {"now": NOW, "person": DANA, "tag": ALICES_CLIENT},
        )
    try:
        yield sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def client(session_maker):
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    async def override_get_db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=1, username="alice", settings={}, force_password_change=False
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as async_client:
        yield async_client


async def _rows(session_maker, statement: str) -> list[tuple]:
    async with session_maker() as session:
        return [tuple(row) for row in (await session.execute(text(statement))).all()]


async def _people_tags(session_maker) -> list[tuple]:
    return await _rows(
        session_maker,
        "SELECT id, name, user_id, parent_id FROM p_tags ORDER BY id",
    )


async def _recording_tags(session_maker) -> list[tuple]:
    return await _rows(
        session_maker, "SELECT id, name, user_id, parent_id FROM tags ORDER BY id"
    )


@pytest.mark.anyio
async def test_the_people_tag_list_holds_only_the_users_own(client) -> None:
    response = await client.get("/api/v1/people-tags/")

    assert response.status_code == 200
    assert [tag["id"] for tag in response.json()] == [ALICES_CLIENT, ALICES_LEAD]


@pytest.mark.anyio
@pytest.mark.parametrize("tag_id", [BOBS_VENDOR, NO_OWNER, MISSING])
async def test_a_new_person_takes_only_the_users_own_tags(
    client, session_maker, tag_id
) -> None:
    response = await client.post(
        "/api/v1/speakers/",
        json={"name": "Erin", "tag_ids": [ALICES_LEAD, tag_id]},
    )

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    assert await _rows(session_maker, "SELECT name FROM global_speakers") == [("Dana",)]
    assert await _rows(
        session_maker, "SELECT global_speaker_id, tag_id FROM people_tags"
    ) == [(DANA, ALICES_CLIENT)]


@pytest.mark.anyio
async def test_a_new_person_is_linked_once_to_each_of_the_users_own_tags(
    client, session_maker
) -> None:
    response = await client.post(
        "/api/v1/speakers/",
        json={"name": "Erin", "tag_ids": [ALICES_LEAD, ALICES_LEAD]},
    )

    assert response.status_code == 200
    erin = response.json()["id"]
    assert await _rows(
        session_maker,
        f"SELECT tag_id FROM people_tags WHERE global_speaker_id = {erin}",
    ) == [(ALICES_LEAD,)]


@pytest.mark.anyio
@pytest.mark.parametrize("tag_id", [BOBS_VENDOR, NO_OWNER, MISSING])
async def test_updating_a_person_takes_only_the_users_own_tags(
    client, session_maker, tag_id
) -> None:
    response = await client.put(
        f"/api/v1/speakers/{DANA}",
        json={"company": "After", "tag_ids": [ALICES_LEAD, tag_id]},
    )

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    assert await _rows(session_maker, "SELECT company FROM global_speakers") == [
        ("Before",)
    ]
    assert await _rows(
        session_maker, "SELECT global_speaker_id, tag_id FROM people_tags"
    ) == [(DANA, ALICES_CLIENT)]


@pytest.mark.anyio
async def test_updating_a_person_replaces_their_tags_with_the_users_own(
    client, session_maker
) -> None:
    response = await client.put(
        f"/api/v1/speakers/{DANA}", json={"tag_ids": [ALICES_LEAD]}
    )

    assert response.status_code == 200
    assert await _rows(
        session_maker, "SELECT global_speaker_id, tag_id FROM people_tags"
    ) == [(DANA, ALICES_LEAD)]


@pytest.mark.anyio
@pytest.mark.parametrize("parent_id", [BOBS_VENDOR, NO_OWNER, MISSING])
async def test_a_people_tag_nests_only_under_the_users_own(
    client, session_maker, parent_id
) -> None:
    before = await _people_tags(session_maker)

    created = await client.post(
        "/api/v1/people-tags/", json={"name": "Partner", "parent_id": parent_id}
    )
    updated = await client.patch(
        f"/api/v1/people-tags/{ALICES_LEAD}",
        json={"name": "Renamed", "parent_id": parent_id},
    )

    assert (created.status_code, created.json()) == (404, PARENT_NOT_FOUND)
    assert (updated.status_code, updated.json()) == (404, PARENT_NOT_FOUND)
    assert await _people_tags(session_maker) == before


@pytest.mark.anyio
async def test_a_people_tag_nests_under_the_users_own(client, session_maker) -> None:
    created = await client.post(
        "/api/v1/people-tags/", json={"name": "Partner", "parent_id": ALICES_CLIENT}
    )
    updated = await client.patch(
        f"/api/v1/people-tags/{ALICES_LEAD}", json={"parent_id": ALICES_CLIENT}
    )

    assert created.status_code == 200
    assert created.json()["parent_id"] == ALICES_CLIENT
    assert updated.status_code == 200
    assert updated.json()["parent_id"] == ALICES_CLIENT


@pytest.mark.anyio
@pytest.mark.parametrize("tag_id", [BOBS_VENDOR, NO_OWNER, MISSING])
async def test_only_the_users_own_people_tag_can_be_changed(
    client, session_maker, tag_id
) -> None:
    before = await _people_tags(session_maker)

    updated = await client.patch(
        f"/api/v1/people-tags/{tag_id}", json={"name": "Renamed"}
    )
    deleted = await client.delete(f"/api/v1/people-tags/{tag_id}")

    assert (updated.status_code, updated.json()) == (404, NOT_FOUND)
    assert (deleted.status_code, deleted.json()) == (404, NOT_FOUND)
    assert await _people_tags(session_maker) == before


@pytest.mark.anyio
async def test_a_people_tag_name_is_checked_against_the_users_own_only(
    client,
) -> None:
    response = await client.post("/api/v1/people-tags/", json={"name": "Shared"})

    assert response.status_code == 200
    assert response.json()["name"] == "Shared"


@pytest.mark.anyio
@pytest.mark.parametrize("parent_id", [BOBS_HIRING, MISSING])
async def test_a_recording_tag_nests_only_under_the_users_own(
    client, session_maker, parent_id
) -> None:
    before = await _recording_tags(session_maker)

    created = await client.post(
        "/api/v1/tags/", json={"name": "Offsite", "parent_id": parent_id}
    )
    updated = await client.patch(
        f"/api/v1/tags/{ALICES_BUDGET}",
        json={"name": "Renamed", "parent_id": parent_id},
    )

    assert (created.status_code, created.json()) == (404, PARENT_NOT_FOUND)
    assert (updated.status_code, updated.json()) == (404, PARENT_NOT_FOUND)
    assert await _recording_tags(session_maker) == before


@pytest.mark.anyio
async def test_a_recording_tag_nests_under_the_users_own(client) -> None:
    created = await client.post(
        "/api/v1/tags/", json={"name": "Offsite", "parent_id": ALICES_BOARD}
    )
    updated = await client.patch(
        f"/api/v1/tags/{ALICES_BUDGET}", json={"parent_id": ALICES_BOARD}
    )
    cleared = await client.patch(
        f"/api/v1/tags/{ALICES_BUDGET}", json={"parent_id": None}
    )

    assert created.status_code == 200
    assert created.json()["parent_id"] == ALICES_BOARD
    assert updated.json()["parent_id"] == ALICES_BOARD
    assert cleared.status_code == 200
    assert cleared.json()["parent_id"] is None
