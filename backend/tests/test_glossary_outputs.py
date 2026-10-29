"""Glossary suggestions use owned outputs and the existing personal settings save."""

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from backend.api.deps import get_current_user, get_db
from backend.api.v1.endpoints import settings as settings_ep
from backend.models.notes_output import NotesOutput
from backend.models.recording import Recording
from backend.models.timeline_note import TimelineNote
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.tests.test_notes_outputs import (
    PATH,
)
from backend.tests.test_notes_outputs import (
    configured_ai as configured_ai,
)
from backend.tests.test_notes_outputs import (
    meeting as meeting,
)
from backend.tests.test_notes_outputs import (
    reader_client as reader_client,
)
from backend.tests.test_notes_outputs import (
    reader_sessions as reader_sessions,
)
from backend.tests.test_notes_outputs import (
    worker as worker,
)

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.usefixtures("stub_text_embedding", "stub_celery_dispatch"),
]


@pytest.fixture
async def settings_client(
    reader_sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(settings_ep.router, prefix="/api/v1/settings")

    async def database() -> AsyncIterator[AsyncSession]:
        async with reader_sessions() as session:
            yield session

    async def authenticated(db: AsyncSession = Depends(get_db)) -> User:
        user = await db.get(User, 1)
        assert user is not None
        return user

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = authenticated
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def _create_glossary(client: AsyncClient) -> int:
    response = await client.post(
        f"{PATH}/generate", json={"kind": "glossary", "create_output": True}
    )
    assert response.status_code == 200, response.text
    output = response.json()["output"]
    assert output["kind"] == "glossary"
    assert output["notes_template_id"] is None
    return int(output["id"])


@pytest.mark.usefixtures("meeting")
async def test_glossary_extract_appends_unique_terms_and_regeneration_replaces(
    reader_client: AsyncClient,
    reader_sessions: async_sessionmaker[AsyncSession],
    worker: Callable[[Any], None],
    stub_celery_dispatch: list[tuple[str, Any, Any]],
) -> None:
    async with reader_sessions() as db:
        user = await db.get(User, 1)
        assert user is not None
        user.settings = {"glossary_suggestions_limit": 5}
        db.add(user)
        recording_id = (await db.execute(select(Recording.id))).scalar_one()
        db.add(
            TimelineNote(
                recording_id=recording_id, kind="head", body="Remember the follow-up."
            )
        )
        await db.commit()
    responses = iter(
        [
            json.dumps(
                ["ARR: annual recurring revenue", None, "arr: duplicate", "", 4]
                + [f"Term {index}: meaning" for index in range(8)]
            ),
            json.dumps(["ARR: duplicate", "SLA: service-level agreement"]),
            json.dumps(["DRI: directly responsible individual"]),
        ]
    )
    prompts: list[str] = []

    class LLM:
        def generate_meeting_notes(self, *args: Any, **kwargs: Any) -> str:
            prompts.append(str(args) + str(kwargs))
            return next(responses)

    output_id = await _create_glossary(reader_client)
    worker(LLM())
    first = (await reader_client.get(f"{PATH}/outputs")).json()[0]
    assert first["notes_status"] == "completed"
    assert len(first["notes"].splitlines()) == 5
    assert first["notes"].splitlines()[0] == "ARR: annual recurring revenue"
    assert "duplicate" not in first["notes"]
    response = await reader_client.post(
        f"{PATH}/generate", json={"output_id": output_id, "extract_more": True}
    )
    assert response.status_code == 200, response.text
    assert stub_celery_dispatch[-1][2] == {"extract_more": True}
    worker(LLM())
    extracted = (await reader_client.get(f"{PATH}/outputs")).json()[0]
    assert extracted["id"] == output_id
    assert "SLA: service-level agreement" in extracted["notes"]
    assert extracted["notes"].lower().count("arr:") == 1
    assert "ARR" in prompts[-1] and "Term 0" in prompts[-1]
    response = await reader_client.post(
        f"{PATH}/generate", json={"output_id": output_id}
    )
    assert response.status_code == 200, response.text
    assert not stub_celery_dispatch[-1][2]
    worker(LLM())
    outputs = (await reader_client.get(f"{PATH}/outputs")).json()
    assert len(outputs) == 1
    assert outputs[0]["notes"] == "DRI: directly responsible individual"
    async with reader_sessions() as db:
        transcript = (await db.execute(select(Transcript))).scalar_one()
        assert transcript.notes == "## Minutes\nPrimary decision."
        assert transcript.notes_status == "completed"


async def test_glossary_routes_reject_other_owners_and_cross_recording_outputs(
    reader_client: AsyncClient,
    reader_sessions: async_sessionmaker[AsyncSession],
    meeting: tuple[int, int],
    stub_celery_dispatch: list[tuple[str, Any, Any]],
) -> None:
    async with reader_sessions() as db:
        private = Recording(
            name="Private", public_id="private", user_id=2, audio_path="private.wav"
        )
        own = Recording(name="Own", public_id="own", user_id=1, audio_path="own.wav")
        db.add_all([private, own])
        await db.flush()
        output = NotesOutput(
            recording_id=private.id,
            kind="glossary",
            name="Glossary",
            notes="ARR: private meaning",
            notes_status="completed",
        )
        db.add(output)
        await db.commit()
        output_id = output.id
    response = await reader_client.post(
        "/api/v1/transcripts/private/notes/generate",
        json={"kind": "glossary", "create_output": True},
    )
    assert response.status_code == 404, response.text
    for public_id in ("private", "meeting", "own"):
        response = await reader_client.post(
            f"/api/v1/transcripts/{public_id}/notes/generate",
            json={"output_id": output_id, "extract_more": True},
        )
        assert response.status_code == 404, response.text
    assert stub_celery_dispatch == []


async def test_personal_glossary_partial_saves_preserve_other_settings_and_users(
    settings_client: AsyncClient,
    reader_sessions: async_sessionmaker[AsyncSession],
) -> None:
    defaults = await settings_client.get("/api/v1/settings")
    assert defaults.status_code == 200, defaults.text
    assert defaults.json()["glossary_suggestions_enabled"] is False
    assert defaults.json()["glossary_suggestions_limit"] == 15
    async with reader_sessions() as db:
        user = await db.get(User, 1)
        assert user is not None
        user.settings = {"theme": "light", "glossary_terms": "ARR: existing"}
        db.add(user)
        await db.commit()
    for glossary in ("ARR: existing\nSLA: service-level agreement", "ARR: existing"):
        response = await settings_client.post(
            "/api/v1/settings", json={"glossary_terms": glossary}
        )
        assert response.status_code == 200, response.text
        assert response.json()["glossary_terms"] == glossary
        async with reader_sessions() as db:
            user = await db.get(User, 1)
            other = await db.get(User, 2)
            assert user is not None and other is not None
            assert user.settings["glossary_terms"] == glossary
            assert user.settings["theme"] == "light"
            assert not (other.settings or {}).get("glossary_terms")


@pytest.mark.parametrize("limit", [5, 50, 4, 51, 5.5, True])
async def test_glossary_limit_settings_bounds(
    settings_client: AsyncClient,
    reader_sessions: async_sessionmaker[AsyncSession],
    limit: int | float | bool,
) -> None:
    response = await settings_client.post(
        "/api/v1/settings",
        json={
            "glossary_suggestions_limit": limit,
            "glossary_suggestions_enabled": True,
        },
    )
    accepted = type(limit) is int and 5 <= limit <= 50
    assert response.status_code == (200 if accepted else 422), response.text
    async with reader_sessions() as db:
        user = await db.get(User, 1)
        assert user is not None
        if accepted:
            assert user.settings["glossary_suggestions_limit"] == limit
            assert user.settings["glossary_suggestions_enabled"] is True
        else:
            assert "glossary_suggestions_limit" not in (user.settings or {})


@pytest.mark.parametrize(
    "request_case",
    [
        ({"extract_more": True}, 400),
        ({"output_id": 0, "extract_more": True}, 400),
        ({"kind": "glossary", "create_output": True, "notes_template_id": 0}, 400),
        ({"kind": "unknown", "create_output": True}, 422),
        ({"kind": "glossary", "create_output": True, "primary_busy": True}, 409),
    ],
)
async def test_glossary_generation_guards_do_not_dispatch(
    reader_client: AsyncClient,
    reader_sessions: async_sessionmaker[AsyncSession],
    meeting: tuple[int, int],
    stub_celery_dispatch: list[tuple[str, Any, Any]],
    request_case: tuple[dict[str, Any], int],
) -> None:
    original_body, expected_status = request_case
    body = dict(original_body)
    async with reader_sessions() as db:
        output = NotesOutput(
            recording_id=meeting[0],
            name="Ordinary notes",
            notes="Keep notes",
            notes_status="completed",
        )
        db.add(output)
        if body.pop("primary_busy", False):
            transcript = (await db.execute(select(Transcript))).scalar_one()
            transcript.notes_status = "generating"
            db.add(transcript)
        await db.commit()
        if "output_id" in body:
            body["output_id"] = output.id
    if "notes_template_id" in body:
        body["notes_template_id"] = meeting[1]
    response = await reader_client.post(f"{PATH}/generate", json=body)
    assert response.status_code == expected_status, response.text
    assert stub_celery_dispatch == []
    outputs = (await reader_client.get(f"{PATH}/outputs")).json()
    assert len(outputs) == 1 and outputs[0]["notes"] == "Keep notes"


async def test_glossary_output_rejects_prose_update_without_changing_lines(
    reader_client: AsyncClient,
    reader_sessions: async_sessionmaker[AsyncSession],
    meeting: tuple[int, int],
) -> None:
    original = "ARR: annual recurring revenue\nSLA: service-level agreement"
    async with reader_sessions() as db:
        output = NotesOutput(
            recording_id=meeting[0],
            kind="glossary",
            name="Glossary",
            notes=original,
            notes_status="completed",
        )
        db.add(output)
        await db.commit()
        output_id = output.id
    response = await reader_client.put(
        f"{PATH}/outputs/{output_id}", json={"notes": "## Prose replacement"}
    )
    assert response.status_code == 400, response.text
    async with reader_sessions() as db:
        preserved = await db.get(NotesOutput, output_id)
        assert preserved is not None
        assert preserved.notes == original
        assert preserved.notes_status == "completed"
