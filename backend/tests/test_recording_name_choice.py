# ruff: noqa: F811 -- the API fixtures are imported from test_recording_pause_resume and requested by name
"""Whose name a recording carries (issue #837).

A name the user chose (a rename, a name given at import or at the start of a
capture) sets ``title_user_edited``, and meeting intelligence then leaves it
alone. A recording created without one is named automatically and takes the AI
title. The worker side is in test_automatic_meeting_intelligence_worker.py and
the upgrade in test_title_user_edited_migration_postgres.py.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from backend.core.backup.restore.identity import _settle_legacy_name_choice
from backend.models.recording import (
    Recording,
    chosen_recording_name,
    is_default_meeting_name,
)
from backend.tests.test_recording_pause_resume import (  # noqa: F401 -- fixtures
    TRUSTED_ORIGIN,
    api_app,
    client,
    insert_recording,
    set_session_cookie,
    test_session_maker,
)

HEADERS = {"Origin": TRUSTED_ORIGIN}


async def _stored(session_maker: sessionmaker, public_id: str) -> tuple[str, bool]:
    async with session_maker() as session:
        name, chosen = (
            await session.execute(
                text(
                    "SELECT name, title_user_edited FROM recordings"
                    " WHERE public_id = :id"
                ),
                {"id": public_id},
            )
        ).one()
    return name, bool(chosen)


def test_a_blank_name_is_no_choice() -> None:
    assert chosen_recording_name(None) is None
    assert chosen_recording_name("") is None
    assert chosen_recording_name("   ") is None
    assert chosen_recording_name("  Budget review  ") == "Budget review"


@pytest.mark.parametrize(
    "name",
    [
        "Sunday 1st Nov, Morning Meeting",
        "Monday 2nd Feb, Early Afternoon Meeting",
        "Wednesday 23rd Dec, Late Night Meeting",
        "Thursday 31st Oct, Night Meeting",
        "Saturday 11th Apr, Late Evening Meeting",
    ],
)
def test_default_names_are_recognised(name: str) -> None:
    assert is_default_meeting_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "Budget review",
        # The first default format, from before names carried a date, reads as a
        # name someone could type, so it counts as chosen.
        "Monday Morning Meeting",
        "Sunday 1st Nov, Morning Meeting notes",
        "sunday 1st Nov, Morning Meeting",
        "Sunday 1st November, Morning Meeting",
    ],
)
def test_other_names_are_not_default_names(name: str) -> None:
    assert not is_default_meeting_name(name)


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        # An archive from before the column: the upgrade's rule.
        ({"name": "Budget review"}, True),
        ({"name": "Sunday 1st Nov, Morning Meeting"}, False),
        # An archive that carries it keeps its answer.
        ({"name": "Budget review", "title_user_edited": False}, False),
        ({"name": "Sunday 1st Nov, Morning Meeting", "title_user_edited": True}, True),
    ],
)
def test_a_restore_settles_names_as_the_upgrade_did(
    item: dict[str, object], expected: bool
) -> None:
    ctx = SimpleNamespace(item_data=dict(item), model_cls=Recording)

    _settle_legacy_name_choice(ctx)  # type: ignore[arg-type]

    assert ctx.item_data["title_user_edited"] is expected


def test_a_restore_onto_a_model_without_the_column_adds_nothing() -> None:
    ctx = SimpleNamespace(item_data={"name": "Budget review"}, model_cls=object)

    _settle_legacy_name_choice(ctx)  # type: ignore[arg-type]

    assert "title_user_edited" not in ctx.item_data


@pytest.mark.anyio
async def test_a_rename_makes_the_name_the_users(
    client: AsyncClient, test_session_maker: sessionmaker
) -> None:
    set_session_cookie(client)
    await insert_recording(
        test_session_maker, recording_id=7001, public_id="rec-7001", status="PROCESSED"
    )

    response = await client.patch(
        "/api/v1/recordings/rec-7001", json={"name": "Budget review"}, headers=HEADERS
    )

    assert response.status_code == 200
    assert await _stored(test_session_maker, "rec-7001") == ("Budget review", True)


@pytest.mark.anyio
async def test_a_rename_to_the_same_name_is_no_choice(
    client: AsyncClient, test_session_maker: sessionmaker
) -> None:
    """The rail sends the name back when a rename closes with no edit."""
    set_session_cookie(client)
    await insert_recording(
        test_session_maker, recording_id=7003, public_id="rec-7003", status="PROCESSED"
    )

    response = await client.patch(
        "/api/v1/recordings/rec-7003", json={"name": "Recording 7003"}, headers=HEADERS
    )

    assert response.status_code == 200
    assert await _stored(test_session_maker, "rec-7003") == ("Recording 7003", False)


@pytest.mark.anyio
async def test_changing_only_the_language_leaves_the_name_unchosen(
    client: AsyncClient, test_session_maker: sessionmaker
) -> None:
    set_session_cookie(client)
    await insert_recording(
        test_session_maker, recording_id=7002, public_id="rec-7002", status="PROCESSED"
    )

    response = await client.patch(
        "/api/v1/recordings/rec-7002",
        json={"transcription_language": "fr"},
        headers=HEADERS,
    )

    assert response.status_code == 200
    assert await _stored(test_session_maker, "rec-7002") == ("Recording 7002", False)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"name": "  Budget review  "}, ("Budget review", True)),
        # No name, or a blank one: named after the file, free for the AI title.
        ({}, ("board-call", False)),
        ({"name": "   "}, ("board-call", False)),
    ],
)
async def test_a_chunked_import_keeps_a_name_given_to_it(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    params: dict[str, str],
    expected: tuple[str, bool],
) -> None:
    set_session_cookie(client)

    response = await client.post(
        "/api/v1/recordings/import/chunked/init",
        params={"filename": "board-call.mp3", **params},
        headers=HEADERS,
    )

    assert response.status_code == 200
    assert await _stored(test_session_maker, response.json()["id"]) == expected


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("params", "expected"),
    [({"name": "Budget review"}, ("Budget review", True)), ({}, ("board-call", False))],
)
async def test_a_single_request_import_keeps_a_name_given_to_it(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
    params: dict[str, str],
    expected: tuple[str, bool],
) -> None:
    from backend.api.v1.endpoints.recordings import routes_import_upload
    from backend.utils import recording_audio_sync

    monkeypatch.setattr(routes_import_upload, "get_audio_duration", lambda *_: 12.0)
    monkeypatch.setattr(recording_audio_sync, "get_audio_duration", lambda *_: 12.0)
    set_session_cookie(client)

    response = await client.post(
        "/api/v1/recordings/import",
        params=params,
        files={"file": ("board-call.wav", b"RIFF....fakewavdata", "audio/wav")},
        headers=HEADERS,
    )

    assert response.status_code == 200
    assert await _stored(test_session_maker, response.json()["id"]) == expected


@pytest.mark.anyio
async def test_a_capture_started_with_a_name_keeps_it(
    client: AsyncClient, test_session_maker: sessionmaker
) -> None:
    set_session_cookie(client)

    response = await client.post(
        "/api/v1/recordings/init", params={"name": " Standup "}, headers=HEADERS
    )

    assert response.status_code == 200
    assert await _stored(test_session_maker, response.json()["id"]) == (
        "Standup",
        True,
    )


@pytest.mark.anyio
async def test_a_capture_started_without_a_name_takes_a_default_one(
    client: AsyncClient, test_session_maker: sessionmaker
) -> None:
    set_session_cookie(client)

    response = await client.post("/api/v1/recordings/init", headers=HEADERS)

    assert response.status_code == 200
    name, chosen = await _stored(test_session_maker, response.json()["id"])
    assert chosen is False
    # The default the upgrade and a restore recognise as nobody's choice.
    assert is_default_meeting_name(name)
