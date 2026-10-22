"""A tag change keeps the user's tags a tree, with each name held once.

Both tag routes refuse a parent that is the tag itself or one of its
sub-tags, and a new tag or a rename with a name the user already has. Alice
(user 1) is signed in. The recording tags and the people tags hold the same rows:
Projects > Website > Launch, Archive at the top level, Draft and Review
nested under each other (a loop saved before the check existed), and Bob's
Budget.
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
    P_TAGS_SCHEMA,
    PEOPLE_TAGS_SCHEMA,
    TAGS_SCHEMA,
)

NOW = "2026-10-22 00:00:00"
PROJECTS, WEBSITE, LAUNCH, ARCHIVE, DRAFT, REVIEW, BOBS_BUDGET = range(1, 8)
SEED = (
    (PROJECTS, "Projects", 1, None),
    (WEBSITE, "Website", 1, PROJECTS),
    (LAUNCH, "Launch", 1, WEBSITE),
    (ARCHIVE, "Archive", 1, None),
    (DRAFT, "Draft", 1, REVIEW),
    (REVIEW, "Review", 1, DRAFT),
    (BOBS_BUDGET, "Budget", 2, None),
)

OWN_PARENT = {"detail": "Tag cannot be its own parent"}
UNDER_OWN_SUB_TAG = {"detail": "Tag cannot be nested under one of its own sub-tags"}
NAME_TAKEN = {"detail": "Tag with this name already exists"}
TAG_EXISTS = {"detail": "Tag already exists"}

# (table, route) for recording tags and people tags.
ROUTES = pytest.mark.parametrize(
    ("table", "route"),
    [("tags", "/api/v1/tags"), ("p_tags", "/api/v1/people-tags")],
    ids=["recording-tags", "people-tags"],
)


async def _insert_tag(connection, table: str, row: tuple) -> None:
    tag_id, name, owner, parent = row
    await connection.execute(
        text(
            f"INSERT INTO {table} (id, created_at, updated_at, name, user_id,"
            " parent_id) VALUES (:id, :now, :now, :name, :owner, :parent)"
        ),
        {"id": tag_id, "now": NOW, "name": name, "owner": owner, "parent": parent},
    )


@pytest.fixture
async def session_maker():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        for schema in (P_TAGS_SCHEMA, PEOPLE_TAGS_SCHEMA, TAGS_SCHEMA):
            await connection.execute(text(schema))
        for table in ("tags", "p_tags"):
            for row in SEED:
                await _insert_tag(connection, table, row)
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


async def _tags(session_maker, table: str) -> dict[int, tuple]:
    async with session_maker() as session:
        result = await session.execute(
            text(f"SELECT id, name, parent_id FROM {table} ORDER BY id")
        )
        return {row[0]: (row[1], row[2]) for row in result.all()}


@pytest.mark.anyio
@ROUTES
@pytest.mark.parametrize(
    ("parent_id", "refusal"),
    [
        (PROJECTS, OWN_PARENT),
        (WEBSITE, UNDER_OWN_SUB_TAG),
        (LAUNCH, UNDER_OWN_SUB_TAG),
    ],
    ids=["itself", "child", "grandchild"],
)
async def test_a_tag_cannot_move_under_itself_or_its_sub_tags(  # noqa: PLR0913 - two fixtures, two parametrize axes
    client, session_maker, table, route, parent_id, refusal
) -> None:
    before = await _tags(session_maker, table)

    response = await client.patch(
        f"{route}/{PROJECTS}", json={"name": "Renamed", "parent_id": parent_id}
    )

    assert (response.status_code, response.json()) == (400, refusal)
    assert await _tags(session_maker, table) == before


@pytest.mark.anyio
@ROUTES
async def test_a_tag_moves_anywhere_outside_its_sub_tags(
    client, session_maker, table, route
) -> None:
    up_two_levels = await client.patch(
        f"{route}/{LAUNCH}", json={"parent_id": PROJECTS}
    )
    across = await client.patch(f"{route}/{WEBSITE}", json={"parent_id": ARCHIVE})
    to_the_top = await client.patch(f"{route}/{PROJECTS}", json={"parent_id": None})

    assert [up_two_levels.status_code, across.status_code, to_the_top.status_code] == [
        200,
        200,
        200,
    ]
    tags = await _tags(session_maker, table)
    assert tags[LAUNCH] == ("Launch", PROJECTS)
    assert tags[WEBSITE] == ("Website", ARCHIVE)
    assert tags[PROJECTS] == ("Projects", None)


@pytest.mark.anyio
@ROUTES
async def test_an_older_loop_does_not_hold_a_move_and_can_be_broken(
    client, session_maker, table, route
) -> None:
    beside_the_loop = await client.patch(
        f"{route}/{ARCHIVE}", json={"parent_id": DRAFT}
    )
    still_a_loop = await client.patch(f"{route}/{DRAFT}", json={"parent_id": REVIEW})
    broken = await client.patch(f"{route}/{DRAFT}", json={"parent_id": None})

    assert beside_the_loop.status_code == 200
    assert (still_a_loop.status_code, still_a_loop.json()) == (400, UNDER_OWN_SUB_TAG)
    assert broken.status_code == 200
    tags = await _tags(session_maker, table)
    assert tags[DRAFT] == ("Draft", None)
    assert tags[REVIEW] == ("Review", DRAFT)


@pytest.mark.anyio
@ROUTES
async def test_a_tag_cannot_take_a_name_the_user_already_has(
    client, session_maker, table, route
) -> None:
    before = await _tags(session_maker, table)

    response = await client.patch(f"{route}/{WEBSITE}", json={"name": "Archive"})

    assert (response.status_code, response.json()) == (400, NAME_TAKEN)
    assert await _tags(session_maker, table) == before


@pytest.mark.anyio
@ROUTES
@pytest.mark.parametrize(
    ("name", "parent_id"),
    [("Archive", None), ("Archive", PROJECTS), ("Launch", None)],
    ids=["top-level", "as-a-sub-tag", "a-sub-tag-name"],
)
async def test_a_new_tag_cannot_take_a_name_the_user_already_has(  # noqa: PLR0913 - two fixtures, two parametrize axes
    client, session_maker, table, route, name, parent_id
) -> None:
    before = await _tags(session_maker, table)

    response = await client.post(
        f"{route}/", json={"name": name, "color": "red", "parent_id": parent_id}
    )

    assert (response.status_code, response.json()) == (400, TAG_EXISTS)
    assert await _tags(session_maker, table) == before


@pytest.mark.anyio
@ROUTES
async def test_a_new_tag_may_take_a_name_only_another_user_has_or_another_case(
    client, session_maker, table, route
) -> None:
    bobs_name = await client.post(
        f"{route}/", json={"name": "Budget", "parent_id": PROJECTS}
    )
    other_case = await client.post(f"{route}/", json={"name": "archive"})

    assert [bobs_name.status_code, other_case.status_code] == [200, 200]
    tags = await _tags(session_maker, table)
    assert tags[bobs_name.json()["id"]] == ("Budget", PROJECTS)
    assert tags[other_case.json()["id"]] == ("archive", None)


@pytest.mark.anyio
@ROUTES
async def test_a_tag_keeps_its_name_or_takes_one_only_another_user_has(
    client, session_maker, table, route
) -> None:
    unchanged = await client.patch(f"{route}/{WEBSITE}", json={"name": "Website"})
    bobs_name = await client.patch(f"{route}/{WEBSITE}", json={"name": "Budget"})
    cleared = await client.patch(f"{route}/{WEBSITE}", json={"name": None})

    assert [unchanged.status_code, bobs_name.status_code, cleared.status_code] == [
        200,
        200,
        200,
    ]
    assert cleared.json()["name"] == "Budget"
    assert (await _tags(session_maker, table))[WEBSITE] == ("Budget", PROJECTS)
