"""Naming a live speaker during capture, through the API.

The lines are ``live_naming_fixtures.LIVE_LINES``: three live labels, one of
them with a locked turn shorter than its unlocked one. A name is the user's
name event on the label's speech, which the final pass carries; the live read
returns it so the live panel shows it in place of "Speaker <n>".
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.tests.live_naming_fixtures import (
    LIVE_LINES,
    ONE,
    THREE,
    TWO,
    seed_live_lines,
)
from backend.tests.sqlite_schemas import (
    GLOBAL_SPEAKERS_SCHEMA,
    PROCESSING_RUNS_SCHEMA,
    RECORDING_SPEAKER_ALIASES_SCHEMA,
    RECORDING_SPEAKER_DRIFT_REFUSALS_SCHEMA,
    RECORDING_SPEAKERS_SCHEMA,
    RECORDINGS_SCHEMA,
    SPEAKER_CORRECTION_EVENTS_SCHEMA,
    SPEAKER_NAME_EVENTS_SCHEMA,
    TIMELINE_NOTES_SCHEMA,
    TRANSCRIPT_UTTERANCE_EVENTS_SCHEMA,
    TRANSCRIPT_UTTERANCES_SCHEMA,
    TRANSCRIPTS_SCHEMA,
)

pytestmark = pytest.mark.anyio

NOW = "2026-10-24 00:00:00"
REC = "live-rec"
USER = SimpleNamespace(id=1, username="alice", settings={}, force_password_change=False)


@pytest.fixture
async def session_maker():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        for schema in (
            RECORDINGS_SCHEMA,
            TRANSCRIPTS_SCHEMA,
            GLOBAL_SPEAKERS_SCHEMA,
            RECORDING_SPEAKERS_SCHEMA,
            RECORDING_SPEAKER_DRIFT_REFUSALS_SCHEMA,
            PROCESSING_RUNS_SCHEMA,
            TRANSCRIPT_UTTERANCES_SCHEMA,
            TRANSCRIPT_UTTERANCE_EVENTS_SCHEMA,
            RECORDING_SPEAKER_ALIASES_SCHEMA,
            SPEAKER_CORRECTION_EVENTS_SCHEMA,
            SPEAKER_NAME_EVENTS_SCHEMA,
            TIMELINE_NOTES_SCHEMA,
        ):
            await connection.execute(text(schema))
    try:
        yield sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def client(session_maker, stub_meeting_edge_dispatch):
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    async def override_get_db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: USER
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as async_client:
        yield async_client


async def _seed(
    session_maker,
    *,
    status: str = "UPLOADING",
    owner: int = 1,
    people: tuple[tuple[int, int, str], ...] = (),
    lines: list[tuple[float, float, str, bool]] = LIVE_LINES,
) -> None:
    async with session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, status, upload_progress, processing_progress,
                    pipeline_generation, is_archived, is_deleted, user_id
                ) VALUES (1, :now, :now, 'Live meeting', :public_id, 'uid-live',
                          '/tmp/live.wav', 'UPLOADING', 50, 10, 'unified', 0, 0,
                          :owner)
                """
            ),
            {"now": NOW, "public_id": REC, "owner": owner},
        )
        await session.execute(
            text(
                """
                INSERT INTO transcripts (
                    id, created_at, updated_at, recording_id, text, segments,
                    meeting_edge_status, notes_status, transcript_status
                ) VALUES (1, :now, :now, 1, '', '[]', 'idle', 'pending',
                          'processing')
                """
            ),
            {"now": NOW},
        )
        for person_id, user_id, name in people:
            await session.execute(
                text(
                    """
                    INSERT INTO global_speakers (
                        id, created_at, updated_at, user_id, name,
                        is_voiceprint_locked
                    ) VALUES (:id, :now, :now, :user_id, :name, 0)
                    """
                ),
                {"id": person_id, "now": NOW, "user_id": user_id, "name": name},
            )
        await session.commit()
        await session.run_sync(lambda sync: seed_live_lines(sync, 1, lines))
        # The lines are written while capturing; the status is the case's.
        await session.execute(
            text("UPDATE recordings SET status = :status"), {"status": status}
        )
        await session.commit()


async def _events(session_maker) -> list[dict[str, Any]]:
    async with session_maker() as session:
        rows = await session.execute(
            text(
                "SELECT id, kind, name, global_speaker_id, source, authority, "
                "surface, anchor_start_ms, anchor_end_ms, anchor_label, "
                "recording_speaker_id, supersedes_event_id, created_by_user_id "
                "FROM speaker_name_events ORDER BY id"
            )
        )
        return [dict(row._mapping) for row in rows.all()]


async def _count(session_maker, table: str) -> int:
    async with session_maker() as session:
        return (await session.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar()


async def _name(client, label: str, name: str, recording: str = REC):
    return await client.put(
        f"/api/v1/speakers/recordings/{recording}/live/{label}", json={"name": name}
    )


async def test_a_name_is_the_users_event_on_the_labels_locked_turn(
    client, session_maker
) -> None:
    await _seed(session_maker)

    response = await _name(client, ONE, "  Alex ")

    assert response.status_code == 200, response.text
    assert response.json() == {"live_speaker_names": {ONE: "Alex"}}
    # The locked 0-6 s turn, not the longer unlocked one at 9.5-20 s, whose
    # label may still change.
    assert await _events(session_maker) == [
        {
            "id": 1,
            "kind": "name",
            "name": "Alex",
            "global_speaker_id": None,
            "source": "user",
            "authority": 40,
            "surface": "live",
            "anchor_start_ms": 0,
            "anchor_end_ms": 6000,
            "anchor_label": ONE,
            "recording_speaker_id": None,
            "supersedes_event_id": None,
            "created_by_user_id": 1,
        }
    ]
    # A live label is not a speaker of the recording, named or not.
    assert await _count(session_maker, "recording_speakers") == 0


@pytest.mark.parametrize(
    ("label", "span"),
    [
        # Nothing locked: the longest turn of any.
        pytest.param(TWO, (6000, 9000), id="longest-turn"),
        # The unlabelled line between its two lines is someone else's.
        pytest.param(THREE, (30000, 33000), id="unlabelled-line-ends-a-turn"),
    ],
)
async def test_an_unlocked_label_is_anchored_on_its_longest_turn(
    client, session_maker, label, span
) -> None:
    await _seed(session_maker)

    response = await _name(client, label, "Blair")

    assert response.status_code == 200, response.text
    (event,) = await _events(session_maker)
    assert (event["anchor_start_ms"], event["anchor_end_ms"]) == span


@pytest.mark.parametrize(
    ("lines", "span"),
    [
        # A locked 1.5 s "yes" says less than the 12 s turn after it.
        pytest.param(
            [(0.0, 1.5, ONE, True), (1.5, 3.0, TWO, False), (3.0, 15.0, ONE, False)],
            (3000, 15000),
            id="short-locked-turn",
        ),
        # The same "yes" running straight into the long turn: one turn of both.
        pytest.param(
            [(0.0, 1.5, ONE, True), (1.5, 13.5, ONE, False)],
            (0, 13500),
            id="short-locked-turn-in-a-long-one",
        ),
        # Under 3 s but half the unlocked turn: still the locked one.
        pytest.param(
            [(0.0, 2.0, ONE, True), (2.0, 3.5, TWO, False), (3.5, 7.0, ONE, False)],
            (0, 2000),
            id="short-locked-turn-half-the-unlocked",
        ),
        # 3 s locked is enough, however long the unlocked turn.
        pytest.param(
            [(0.0, 3.0, ONE, True), (3.0, 4.5, TWO, False), (4.5, 16.5, ONE, False)],
            (0, 3000),
            id="locked-turn-long-enough",
        ),
    ],
)
async def test_a_short_locked_turn_gives_way_to_a_long_one(
    client, session_maker, lines, span
) -> None:
    await _seed(session_maker, lines=lines)

    assert (await _name(client, ONE, "Alex")).status_code == 200
    (event,) = await _events(session_maker)
    assert (event["anchor_start_ms"], event["anchor_end_ms"]) == span


async def test_only_this_recordings_current_lines_anchor_a_name(
    client, session_maker
) -> None:
    await _seed(session_maker)
    async with session_maker() as session:
        # Another meeting's long, locked turn under the same live label.
        await session.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, status, upload_progress, processing_progress,
                    pipeline_generation, is_archived, is_deleted, user_id
                ) VALUES (2, :now, :now, 'Other', 'other-rec', 'uid-other',
                          '/tmp/other.wav', 'UPLOADING', 50, 10, 'unified', 0, 0, 1)
                """
            ),
            {"now": NOW},
        )
        await session.execute(
            text(
                """
                INSERT INTO transcripts (
                    id, created_at, updated_at, recording_id, text, segments,
                    meeting_edge_status, notes_status, transcript_status
                ) VALUES (2, :now, :now, 2, '', '[]', 'idle', 'pending',
                          'processing')
                """
            ),
            {"now": NOW},
        )
        await session.commit()
        await session.run_sync(
            lambda sync: seed_live_lines(sync, 2, [(40.0, 60.0, TWO, True)])
        )
        # The live lane replaced the 6-9 s line.
        await session.execute(
            text(
                "UPDATE transcript_utterances SET state = 'superseded' "
                "WHERE recording_id = 1 AND start_ms = 6000"
            )
        )
        await session.commit()

    response = await _name(client, TWO, "Blair")

    assert response.status_code == 200, response.text
    (event,) = await _events(session_maker)
    assert (event["anchor_start_ms"], event["anchor_end_ms"]) == (21000, 22000)


async def test_a_second_name_supersedes_the_first_and_the_live_read_shows_it(
    client, session_maker
) -> None:
    await _seed(session_maker)
    assert (await _name(client, ONE, "Alex")).status_code == 200
    assert (await _name(client, TWO, "Blair")).status_code == 200

    response = await _name(client, ONE, "Alexis")

    assert response.json() == {"live_speaker_names": {ONE: "Alexis", TWO: "Blair"}}
    events = await _events(session_maker)
    assert [(e["id"], e["name"], e["supersedes_event_id"]) for e in events] == [
        (1, "Alex", None),
        (2, "Blair", None),
        (3, "Alexis", 1),
    ]
    full = await client.get(f"/api/v1/transcripts/{REC}/utterances")
    assert full.json()["live_speaker_names"] == {ONE: "Alexis", TWO: "Blair"}
    # A name changes no line, so a delta with nothing new still carries it.
    revision = full.json()["revision"]
    delta = await client.get(
        f"/api/v1/transcripts/{REC}/utterances", params={"after_revision": revision}
    )
    assert delta.json()["utterances"] == []
    assert delta.json()["live_speaker_names"] == {ONE: "Alexis", TWO: "Blair"}


async def test_live_names_leave_the_read_with_the_live_lines(
    client, session_maker
) -> None:
    await _seed(session_maker)
    assert (await _name(client, ONE, "Alex")).status_code == 200
    assert (await _name(client, TWO, "Blair")).status_code == 200

    async def replace(where: str) -> dict:
        async with session_maker() as session:
            await session.execute(
                text(f"UPDATE transcript_utterances SET state = 'superseded' {where}")
            )
            await session.commit()
        return (await client.get(f"/api/v1/transcripts/{REC}/utterances")).json()

    # Only Speaker 2 still has lines.
    read = await replace(f"WHERE speaker_label = '{ONE}'")
    assert read["live_speaker_names"] == {TWO: "Blair"}
    # The final pass has replaced every live line: no live label is left.
    read = await replace("")
    assert read["live_speaker_names"] == {}


async def test_a_persons_exact_name_links_them_and_nothing_creates_a_person(
    client, session_maker
) -> None:
    # Dana is the owner's; Erin is someone else's.
    await _seed(session_maker, people=((7, 1, "Dana"), (8, 2, "Erin")))

    for label, name in ((ONE, "Dana"), (TWO, "Erin"), (THREE, "dana")):
        assert (await _name(client, label, name)).status_code == 200

    events = await _events(session_maker)
    assert [(e["name"], e["global_speaker_id"]) for e in events] == [
        ("Dana", 7),
        ("Erin", None),
        ("dana", None),
    ]
    assert await _count(session_maker, "global_speakers") == 2


@pytest.mark.parametrize(
    ("status", "named"),
    [
        ("UPLOADING", True),
        ("PAUSED", True),
        ("QUEUED", False),
        ("PROCESSING", False),
        ("PROCESSED", False),
        ("ERROR", False),
    ],
)
async def test_names_are_taken_only_while_capturing_or_paused(
    client, session_maker, status, named
) -> None:
    await _seed(session_maker, status=status)

    response = await _name(client, ONE, "Alex")

    assert response.status_code == (200 if named else 409), response.text
    assert len(await _events(session_maker)) == (1 if named else 0)


NOT_FOUND = (404, "Live speaker not found in this recording")
PLACEHOLDER = (422, "Pick a real name, or rename them to the right person")


@pytest.mark.parametrize(
    ("label", "name", "refusal"),
    [
        # No line carries it.
        pytest.param("LIVE_PROVISIONAL_9", "Alex", NOT_FOUND, id="unheard-label"),
        # A final pass's label is renamed the usual way, not here.
        pytest.param("SPEAKER_00", "Alex", NOT_FOUND, id="not-a-live-label"),
        # Unlabelled lines are nobody's: a name for them would name them all.
        pytest.param("UNKNOWN", "Alex", NOT_FOUND, id="unlabelled-lines"),
        pytest.param(ONE, "   ", (422, "Enter a name"), id="blank-name"),
        # The final pass names unnamed speakers "Speaker <n>" and merges
        # speakers that share a name, so this one would take in another voice.
        pytest.param(ONE, "Speaker 2", PLACEHOLDER, id="placeholder-name"),
        pytest.param(ONE, "speaker_01", PLACEHOLDER, id="placeholder-label"),
    ],
)
async def test_a_name_for_no_live_speaker_is_refused(
    client, session_maker, label, name, refusal
) -> None:
    await _seed(session_maker)

    response = await _name(client, label, name)

    assert (response.status_code, response.json()["detail"]) == refusal, response.text
    assert await _events(session_maker) == []


async def test_another_users_recording_is_not_found(client, session_maker) -> None:
    await _seed(session_maker, owner=2)

    response = await _name(client, ONE, "Alex")

    assert response.status_code == 404
    assert response.json()["detail"] == "Recording not found"
    assert await _events(session_maker) == []


async def test_an_export_during_capture_shows_the_live_names(
    client, session_maker
) -> None:
    await _seed(session_maker)
    assert (await _name(client, ONE, "Alex")).status_code == 200

    response = await client.get(
        f"/api/v1/transcripts/{REC}/export",
        params={"sections": "transcript", "export_format": "txt"},
    )

    assert response.status_code == 200, response.text
    speakers = [
        line.split("] ", 1)[1].split(":", 1)[0]
        for line in response.text.splitlines()
        if line.startswith("[") and "live line" in line
    ]
    assert speakers[:4] == ["Alex", "Alex", "Speaker 2", "Alex"]
    assert "LIVE_" not in response.text
