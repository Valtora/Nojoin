"""Integer parameters stay inside the range Postgres binds them as (#923).

A tag id past the 64-bit maximum answered 500 from ``GET /recordings``: FastAPI
took it as a Python int, and Postgres refused it for bigint once the query ran.
Row ids are BIGINT and LIMIT and OFFSET bind as INTEGER, so ids, paging and the
utterance revision cursor are bounded in validation now, and a value outside
them gets the 422 any other invalid parameter gets. The MCP tools that hand a
caller's value on to a route take the same types, since a direct call skips
FastAPI's validation.

Alice (user 1) is signed in. Every name here is made up.
"""

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator, Iterator, Sequence
from datetime import datetime
from types import SimpleNamespace
from typing import Annotated, Any, get_args, get_origin

import pytest
from annotated_types import Le
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import Session

from backend.api.deps import (
    get_current_active_superuser,
    get_current_admin_user,
    get_current_user,
    get_db,
)
from backend.api.v1.api import api_router
from backend.core.db import driver_urls
from backend.core.security import MCP_READ_SCOPE, MCP_WRITE_SCOPE
from backend.mcp_server import server as mcp_server
from backend.mcp_server.auth import current_mcp_scopes, current_mcp_user
from backend.models.people_tag import PeopleTag, PeopleTagLink
from backend.models.recording import Recording, RecordingStatus
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.tag import RecordingTag, Tag
from backend.models.task import UserTask, UserTaskTag
from backend.models.user import User, UserRole
from backend.tests.postgres_setup import fresh_schema

pytestmark = pytest.mark.anyio

ALICE = 1
# The largest values Postgres binds as BIGINT and as INTEGER, written out here
# rather than imported, so the tests check the bounds and not their names.
BIGINT_MAX = 2**63 - 1
INTEGER_MAX = 2**31 - 1
ID_OVER = BIGINT_MAX + 1
PAGE_OVER = INTEGER_MAX + 1

SIGNED_IN = SimpleNamespace(
    id=ALICE, role=UserRole.OWNER, is_superuser=True, is_active=True
)


class _UnusedSession:
    """A session no request here may reach: validation refuses it first."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the route ran and used the session ({name})")


def _app(override_get_db: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_get_db
    for dependency in (
        get_current_user,
        get_current_admin_user,
        get_current_active_superuser,
    ):
        app.dependency_overrides[dependency] = lambda: SIGNED_IN
    return app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async def no_session():
        yield _UnusedSession()

    async with AsyncClient(
        transport=ASGITransport(app=_app(no_session)), base_url="http://testserver"
    ) as async_client:
        yield async_client


def _over(method: str, url: str, loc: Sequence[str | int], kind: str) -> Any:
    return pytest.param(method, url, list(loc), kind, id=f"{method} {url}")


TOO_LARGE = "less_than_equal"
TOO_SMALL = "greater_than_equal"

OUT_OF_RANGE = [
    # The issue: a tag id past bigint on the recordings list, at both paths.
    _over(
        "GET",
        f"/recordings?tag_ids=3&tag_ids={ID_OVER}",
        ["query", "tag_ids", 1],
        TOO_LARGE,
    ),
    _over("GET", f"/recordings/?tag_ids={ID_OVER}", ["query", "tag_ids", 0], TOO_LARGE),
    _over(
        "GET",
        f"/recordings?speaker_ids={ID_OVER}",
        ["query", "speaker_ids", 0],
        TOO_LARGE,
    ),
    _over(
        "GET",
        f"/recordings/?speaker_ids={ID_OVER}",
        ["query", "speaker_ids", 0],
        TOO_LARGE,
    ),
    _over("GET", f"/recordings?skip={PAGE_OVER}", ["query", "skip"], TOO_LARGE),
    _over("GET", "/recordings/?skip=-1", ["query", "skip"], TOO_SMALL),
    _over("GET", f"/recordings/?limit={PAGE_OVER}", ["query", "limit"], TOO_LARGE),
    _over("GET", "/recordings?limit=-1", ["query", "limit"], TOO_SMALL),
    # The People list and a person's routes.
    _over("GET", f"/speakers/?tags={ID_OVER}", ["query", "tags", 0], TOO_LARGE),
    _over("GET", f"/speakers/?skip={PAGE_OVER}", ["query", "skip"], TOO_LARGE),
    _over("GET", "/speakers/?limit=-1", ["query", "limit"], TOO_SMALL),
    _over("PUT", f"/speakers/{ID_OVER}", ["path", "speaker_id"], TOO_LARGE),
    _over("DELETE", f"/speakers/{ID_OVER}", ["path", "speaker_id"], TOO_LARGE),
    _over("GET", f"/speakers/{ID_OVER}/segments", ["path", "speaker_id"], TOO_LARGE),
    _over("GET", "/speakers/1/segments?limit=-1", ["query", "limit"], TOO_SMALL),
    _over(
        "POST", f"/speakers/{ID_OVER}/recalibrate", ["path", "speaker_id"], TOO_LARGE
    ),
    _over(
        "DELETE", f"/speakers/{ID_OVER}/embedding", ["path", "speaker_id"], TOO_LARGE
    ),
    _over("POST", f"/speakers/{ID_OVER}/split", ["path", "speaker_id"], TOO_LARGE),
    _over(
        "POST", f"/speakers/{ID_OVER}/scan-matches", ["path", "speaker_id"], TOO_LARGE
    ),
    _over("PATCH", f"/people-tags/{ID_OVER}", ["path", "tag_id"], TOO_LARGE),
    _over("DELETE", f"/people-tags/{ID_OVER}", ["path", "tag_id"], TOO_LARGE),
    # Tags and tasks.
    _over("GET", f"/tags?skip={PAGE_OVER}", ["query", "skip"], TOO_LARGE),
    _over("GET", f"/tags/?limit={PAGE_OVER}", ["query", "limit"], TOO_LARGE),
    _over("PATCH", f"/tags/{ID_OVER}", ["path", "tag_id"], TOO_LARGE),
    _over("DELETE", "/tags/0", ["path", "tag_id"], TOO_SMALL),
    _over("PATCH", f"/tasks/{ID_OVER}", ["path", "task_id"], TOO_LARGE),
    _over("DELETE", f"/tasks/{ID_OVER}", ["path", "task_id"], TOO_LARGE),
    # The task list's tag filter (#799), at both paths.
    _over("GET", f"/tasks?tag_ids={ID_OVER}", ["query", "tag_ids", 0], TOO_LARGE),
    _over("GET", "/tasks/?tag_ids=3&tag_ids=0", ["query", "tag_ids", 1], TOO_SMALL),
    # Users and invitations.
    _over("GET", f"/users?skip={PAGE_OVER}", ["query", "skip"], TOO_LARGE),
    _over("GET", f"/users/?limit={PAGE_OVER}", ["query", "limit"], TOO_LARGE),
    _over("DELETE", f"/users/{ID_OVER}", ["path", "user_id"], TOO_LARGE),
    _over("PATCH", f"/users/{ID_OVER}/role", ["path", "user_id"], TOO_LARGE),
    _over("PATCH", f"/users/{ID_OVER}", ["path", "user_id"], TOO_LARGE),
    _over(
        "POST", f"/users/{ID_OVER}/sessions/revoke-all", ["path", "user_id"], TOO_LARGE
    ),
    _over("GET", f"/invitations/?skip={PAGE_OVER}", ["query", "skip"], TOO_LARGE),
    _over("POST", f"/invitations/{ID_OVER}/revoke", ["path", "id"], TOO_LARGE),
    _over("DELETE", f"/invitations/{ID_OVER}", ["path", "id"], TOO_LARGE),
    # Notes templates and documents.
    _over("PUT", f"/notes-templates/{ID_OVER}", ["path", "template_id"], TOO_LARGE),
    _over(
        "POST", f"/notes-templates/{ID_OVER}/reset", ["path", "template_id"], TOO_LARGE
    ),
    _over(
        "POST", f"/notes-templates/{ID_OVER}/copy", ["path", "template_id"], TOO_LARGE
    ),
    _over("DELETE", f"/notes-templates/{ID_OVER}", ["path", "template_id"], TOO_LARGE),
    _over("GET", f"/documents/{ID_OVER}/download", ["path", "document_id"], TOO_LARGE),
    _over("POST", f"/documents/{ID_OVER}/reparse", ["path", "document_id"], TOO_LARGE),
    _over("DELETE", f"/documents/{ID_OVER}", ["path", "document_id"], TOO_LARGE),
    _over("GET", f"/documents/{ID_OVER}/preview", ["path", "document_id"], TOO_LARGE),
    # Calendar connections.
    _over(
        "PUT",
        f"/calendar/connections/{ID_OVER}/calendars",
        ["path", "connection_id"],
        TOO_LARGE,
    ),
    _over(
        "PUT",
        f"/calendar/connections/1/calendars/{ID_OVER}/color",
        ["path", "calendar_id"],
        TOO_LARGE,
    ),
    _over(
        "PUT",
        f"/calendar/connections/{ID_OVER}/calendars/1/colour",
        ["path", "connection_id"],
        TOO_LARGE,
    ),
    _over(
        "POST",
        f"/calendar/connections/{ID_OVER}/sync",
        ["path", "connection_id"],
        TOO_LARGE,
    ),
    _over(
        "DELETE",
        f"/calendar/connections/{ID_OVER}",
        ["path", "connection_id"],
        TOO_LARGE,
    ),
    # The CLI usage overview and the utterance revision cursor.
    _over(
        "GET", f"/cli-oauth/admin/usage?skip={PAGE_OVER}", ["query", "skip"], TOO_LARGE
    ),
    _over("GET", "/cli-oauth/admin/usage?limit=-1", ["query", "limit"], TOO_SMALL),
    _over(
        "GET",
        f"/transcripts/rec-1/utterances?after_revision={ID_OVER}",
        ["query", "after_revision"],
        TOO_LARGE,
    ),
]


@pytest.mark.parametrize(("method", "url", "loc", "kind"), OUT_OF_RANGE)
async def test_an_out_of_range_integer_is_refused_in_validation(
    client: AsyncClient, method: str, url: str, loc: list[Any], kind: str
) -> None:
    response = await client.request(method, f"/api/v1{url}")

    assert response.status_code == 422, response.text
    refusals = [error for error in response.json()["detail"] if error["loc"] == loc]
    assert len(refusals) == 1, response.json()
    assert refusals[0]["type"] == kind


async def test_the_refusal_reads_like_any_other_invalid_parameter(
    client: AsyncClient,
) -> None:
    """The same 422 body FastAPI gives a value that is not an integer at all."""
    too_large = await client.get(f"/api/v1/recordings?tag_ids={ID_OVER}")
    not_a_number = await client.get("/api/v1/recordings?tag_ids=many")

    assert too_large.status_code == not_a_number.status_code == 422
    [error] = too_large.json()["detail"]
    assert error == {
        "type": "less_than_equal",
        "loc": ["query", "tag_ids", 0],
        "msg": f"Input should be less than or equal to {BIGINT_MAX}",
        "input": str(ID_OVER),
        "ctx": {"le": BIGINT_MAX},
    }
    [other] = not_a_number.json()["detail"]
    assert set(other) <= set(error)


# Integer parameters left unbounded, each with its reason. A new integer
# query or path parameter takes a type from backend/api/params.py, or a bound
# of its own, or an entry here.
UNBOUNDED = {
    # The capture and import upload contract: the tab's own counters, sent
    # back as the server issued them. Left for a capture-aware change.
    "recordings.routes_capture.pause_upload:epoch",
    "recordings.routes_capture.resume_upload:epoch",
    "recordings.routes_capture.resume_upload:reclaim_epoch",
    "recordings.routes_capture.resume_upload:next_sequence",
    "recordings.routes_capture.upload_segment:sequence",
    "recordings.routes_capture.upload_segment:epoch",
    "recordings.routes_capture.upload_segment:resume_mark",
    "recordings.routes_capture.finalize_upload:epoch",
    "recordings.routes_capture.discard_upload:epoch",
    "recordings.routes_capture.update_client_status:upload_progress",
    "recordings.routes_import_upload.upload_chunked_segment:sequence",
    # Compared with the stored revision in Python, never bound to SQL.
    "transcripts.routes_timeline_notes.delete_timeline_note:expected_revision",
    "transcripts.routes_utterances.clear_utterance_locks:expected_revision",
    # An index into the transcript's segments, which the route range-checks.
    "transcripts.routes_segments.update_segment_speaker:segment_index",
    "transcripts.routes_segments.update_transcript_segment_text:segment_index",
    # A backup upload's size and chunk numbers, checked in Python and never
    # written to Postgres.
    "backup.init_upload:file_size",
    "backup.init_upload:total_chunks",
    "backup.upload_chunk:chunk_index",
}


def _api_routes(routes: Sequence[Any]) -> Iterator[APIRoute]:
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        # FastAPI keeps an included router whole, behind a wrapper.
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from _api_routes(included.routes)


def _mentions_int(annotation: Any) -> bool:
    return annotation is int or any(_mentions_int(arg) for arg in get_args(annotation))


def _bounded_above(annotation: Any, metadata: Sequence[Any] = ()) -> bool:
    if any(isinstance(item, Le) for item in metadata):
        return True
    if get_origin(annotation) is Annotated:
        base, *extra = get_args(annotation)
        return _bounded_above(base, extra)
    args = [arg for arg in get_args(annotation) if arg is not type(None)]
    return bool(args) and all(_bounded_above(arg) for arg in args)


def _parameters(dependant: Any) -> Iterator[Any]:
    """A route's query and path parameters, its dependencies' included."""
    yield from dependant.query_params
    yield from dependant.path_params
    for dependency in dependant.dependencies:
        yield from _parameters(dependency)


def _integer_parameters() -> dict[str, bool]:
    """Each integer query and path parameter, and whether it has an upper bound."""
    found: dict[str, bool] = {}
    for route in _api_routes(api_router.routes):
        module = route.endpoint.__module__.removeprefix("backend.api.v1.endpoints.")
        for param in _parameters(route.dependant):
            field = param.field_info
            if _mentions_int(field.annotation):
                key = f"{module}.{route.endpoint.__name__}:{param.name}"
                found[key] = _bounded_above(field.annotation, field.metadata)
    return found


def test_every_integer_parameter_has_an_upper_bound() -> None:
    found = _integer_parameters()
    # The walk found the routes, so an empty result cannot pass by accident.
    assert found["recordings.routes_query.list_recordings:tag_ids"] is True
    assert found["tags.update_tag:tag_id"] is True

    unbounded = {key for key, bounded in found.items() if not bounded}
    assert unbounded - UNBOUNDED == set(), "bound these, or list them in UNBOUNDED"
    assert UNBOUNDED - unbounded == set(), "these are bounded now: drop them"


def _bind_mcp_identity() -> None:
    # Set inside the test: anyio copies the context when the test starts.
    current_mcp_user.set(SimpleNamespace(id=ALICE))
    current_mcp_scopes.set(frozenset({MCP_READ_SCOPE, MCP_WRITE_SCOPE}))


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("list_recordings", {"skip": PAGE_OVER}),
        ("list_people", {"skip": -1}),
        ("get_person", {"person_id": ID_OVER}),
        ("search_context", {"query": "budget", "tag_ids": [ID_OVER]}),
        ("create_task", {"title": "Send the notes", "tag_ids": [ID_OVER]}),
        ("update_task", {"task_id": ID_OVER, "title": "Send the notes"}),
        ("update_task", {"task_id": 1, "tag_ids": [0]}),
        (
            "get_transcript_utterances",
            {"recording_id": "rec-1", "after_revision": ID_OVER},
        ),
    ],
)
async def test_an_mcp_tool_refuses_an_out_of_range_integer(
    tool: str, arguments: dict[str, Any]
) -> None:
    """Refused by the SDK's argument validation, before the tool runs.

    These tools pass the value to a route function or a query directly, where
    FastAPI's validation never sees it.
    """
    _bind_mcp_identity()

    with pytest.raises(ToolError) as refused:
        await mcp_server.mcp.call_tool(tool, arguments)

    assert not isinstance(refused.value, UnexpectedToolError)
    assert f"Error executing tool {tool}: 1 validation error" in str(refused.value)


# Against a real server: the bounds are exactly what Postgres binds, so a row
# at the very top of the BIGINT range is still found, and paging to the top of
# INTEGER runs, while one more is refused before any query.


async def _postgres_engine(url: str) -> AsyncEngine:
    importlib.import_module("backend.models.registry")
    async_url, sync_url = driver_urls(url)
    schema = "bounded_int_params_test"
    sync_engine = create_engine(
        sync_url, connect_args={"options": f"-csearch_path={schema},public"}
    )
    with sync_engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    fresh_schema(sync_engine, schema)
    with Session(sync_engine) as session:
        session.add(User(id=ALICE, username="alice", hashed_password="x"))
        session.flush()
        session.add_all(
            [
                Recording(
                    id=1,
                    public_id="rec-1",
                    meeting_uid="meeting-1",
                    name="Quarterly planning",
                    audio_path="/nonexistent/1.webm",
                    status=RecordingStatus.PROCESSED,
                    user_id=ALICE,
                    created_at=datetime(2026, 10, 1, 9, 0),
                ),
                Tag(id=BIGINT_MAX, name="Planning", color="blue", user_id=ALICE),
                GlobalSpeaker(id=BIGINT_MAX, name="Alice Example", user_id=ALICE),
                PeopleTag(id=BIGINT_MAX, name="Finance", user_id=ALICE),
                UserTask(id=1, title="Send the notes", user_id=ALICE),
            ]
        )
        session.flush()
        session.add_all(
            [
                RecordingTag(recording_id=1, tag_id=BIGINT_MAX),
                UserTaskTag(task_id=1, tag_id=BIGINT_MAX),
                RecordingSpeaker(
                    recording_id=1,
                    diarization_label="SPEAKER_00",
                    global_speaker_id=BIGINT_MAX,
                ),
                PeopleTagLink(global_speaker_id=BIGINT_MAX, tag_id=BIGINT_MAX),
            ]
        )
        session.commit()
    sync_engine.dispose()
    return create_async_engine(
        async_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": f"{schema},public"}},
    )


@pytest.fixture
async def postgres_client(postgres_test_url: str) -> AsyncIterator[AsyncClient]:
    engine = await _postgres_engine(postgres_test_url)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def postgres_session():
        async with session_maker() as session:
            yield session

    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app(postgres_session)),
            base_url="http://testserver",
        ) as async_client:
            yield async_client
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"/recordings?tag_ids={BIGINT_MAX}", ["rec-1"]),
        (f"/recordings/?speaker_ids={BIGINT_MAX}", ["rec-1"]),
        (f"/recordings?limit={INTEGER_MAX}", ["rec-1"]),
        (f"/recordings?skip={INTEGER_MAX}&limit={INTEGER_MAX}", []),
        (f"/speakers/?tags={BIGINT_MAX}&limit={INTEGER_MAX}", [BIGINT_MAX]),
        (f"/speakers/?skip={INTEGER_MAX}", []),
        (f"/tags?skip={INTEGER_MAX}&limit={INTEGER_MAX}", []),
        (f"/tags/?limit={INTEGER_MAX}", [BIGINT_MAX]),
        (f"/tasks?tag_ids={BIGINT_MAX}", [1]),
        (f"/tasks/?status=all&tag_ids={BIGINT_MAX}", [1]),
        (f"/invitations/?skip={INTEGER_MAX}&limit={INTEGER_MAX}", []),
        (f"/speakers/{BIGINT_MAX}/segments?limit={INTEGER_MAX}", []),
    ],
)
async def test_postgres_accepts_every_value_up_to_the_bound(
    postgres_client: AsyncClient, url: str, expected: list[Any]
) -> None:
    response = await postgres_client.get(f"/api/v1{url}")

    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == expected


async def test_postgres_pages_users_up_to_the_bound(
    postgres_client: AsyncClient,
) -> None:
    response = await postgres_client.get(
        f"/api/v1/users?skip={INTEGER_MAX}&limit={INTEGER_MAX}"
    )

    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


@pytest.mark.parametrize(
    "url",
    [
        f"/recordings?tag_ids={ID_OVER}",
        f"/recordings/?speaker_ids={ID_OVER}",
        f"/recordings?skip={PAGE_OVER}",
        f"/speakers/?tags={ID_OVER}",
        f"/tags?limit={PAGE_OVER}",
        f"/tasks?tag_ids={ID_OVER}",
        f"/tasks/?tag_ids={ID_OVER}",
        f"/users?skip={PAGE_OVER}",
    ],
)
async def test_postgres_never_sees_one_past_the_bound(
    postgres_client: AsyncClient, url: str
) -> None:
    response = await postgres_client.get(f"/api/v1{url}")

    assert response.status_code == 422, response.text
