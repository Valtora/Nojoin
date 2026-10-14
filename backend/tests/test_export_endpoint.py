"""GET /transcripts/{id}/export: choosing sections, legacy requests, branding.

The canonical-table reads are replaced with fixed results; what is under test
is how the route turns a request into a document, not the derivations behind
it, which have their own tests.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_current_user, get_db
from backend.api.v1.endpoints.transcripts import routes_analytics, routes_export
from backend.api.v1.endpoints.transcripts.router import router
from backend.models.recording import Recording
from backend.models.speaker import RecordingSpeaker
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.tests.export_fixtures import NOTES_WITH_TABLES, analytics_payload
from backend.tests.sqlite_schemas import (
    GLOBAL_SPEAKERS_SCHEMA,
    RECORDING_SPEAKERS_SCHEMA,
    RECORDINGS_SCHEMA,
    TRANSCRIPTS_SCHEMA,
    USERS_SCHEMA,
)
from backend.utils.config_manager import EXPORT_BRANDING_KEY, config_manager

SEGMENTS = [
    {"speaker": "SPEAKER_00", "start": 0.0, "end": 4.0, "text": "Let us begin."},
    {"speaker": "SPEAKER_01", "start": 5.0, "end": 8.0, "text": "Agreed."},
]


@pytest.fixture
def api(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    app = FastAPI()
    app.include_router(router, prefix="/transcripts")

    async def override_db():
        async with maker() as session:
            yield session

    state = SimpleNamespace(user=None, analytics_reads=0, recording=None)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: state.user

    monkeypatch.setattr(
        routes_export,
        "build_transcript_segments_for_read",
        lambda _session, _recording_id: SEGMENTS,
    )

    def compute(_session, _recording):
        state.analytics_reads += 1
        payload = analytics_payload()
        return (
            5,
            {
                key: payload[key]
                for key in ("speakers", "metrics", "attribution_warning")
            },
            {},
        )

    monkeypatch.setattr(routes_analytics, "_backfill_and_compute", compute)
    monkeypatch.setattr(
        config_manager, "get", lambda key, default=None: state.config.get(key, default)
    )
    state.config = {}

    async def setup(*, notes: str | None = NOTES_WITH_TABLES):
        # Once per test: a second export in the same test reuses the meeting.
        if state.recording is not None:
            return state.recording
        async with engine.begin() as conn:
            for schema in (
                USERS_SCHEMA,
                RECORDINGS_SCHEMA,
                TRANSCRIPTS_SCHEMA,
                GLOBAL_SPEAKERS_SCHEMA,
                RECORDING_SPEAKERS_SCHEMA,
            ):
                await conn.execute(text(schema))
        state.user = User(
            id=1,
            username="alice",
            hashed_password="x",
            role="user",
            settings={"timezone": "Europe/London"},
        )
        async with maker() as session:
            session.add(state.user)
            recording = Recording(
                id=7,
                name="Architecture Review",
                audio_path="/tmp/review.wav",
                status="PROCESSED",
                user_id=1,
                duration_seconds=1800,
                upload_progress=100,
                processing_progress=100,
            )
            session.add(recording)
            for index, name in enumerate(("Peter", "Anna")):
                session.add(
                    RecordingSpeaker(
                        recording_id=7,
                        diarization_label=f"SPEAKER_{index:02d}",
                        local_name=name,
                    )
                )
            session.add(
                Transcript(
                    recording_id=7,
                    transcript_status="completed",
                    notes_status="completed",
                    notes=notes,
                    analytics_status="pending",
                    analytics_payload={},
                )
            )
            await session.commit()
            await session.refresh(recording)
            state.recording = recording
            return recording

    async def export(recording, **params):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            return await client.get(
                f"/transcripts/{recording.public_id}/export", params=params
            )

    yield SimpleNamespace(setup=setup, export=export, state=state)
    app.dependency_overrides.clear()


def _run(api, notes: str | None = NOTES_WITH_TABLES, **params):
    async def go():
        recording = await api.setup(notes=notes)
        return await api.export(recording, **params)

    return asyncio.run(go())


def test_any_combination_of_sections_comes_back_in_the_fixed_order(api) -> None:
    response = _run(api, sections=["transcript", "notes"], export_format="txt")

    assert response.status_code == 200, response.text
    body = response.text
    assert body.index("MEETING NOTES") < body.index("TRANSCRIPT")
    assert "[00:00] Peter: Let us begin." in body
    assert 'FullExport.txt"' in response.headers["content-disposition"]


def test_the_legacy_content_type_still_works(api) -> None:
    response = _run(api, content_type="both", export_format="txt")

    assert response.status_code == 200
    assert "MEETING NOTES" in response.text and "TRANSCRIPT" in response.text
    assert "ANALYTICS" not in response.text


def test_sections_win_over_content_type(api) -> None:
    response = _run(
        api, content_type="transcript", sections=["notes"], export_format="txt"
    )

    assert "MEETING NOTES" in response.text
    assert "TRANSCRIPT" not in response.text
    assert '_Notes.txt"' in response.headers["content-disposition"]


def test_a_notes_only_export_without_notes_is_refused(api) -> None:
    response = _run(api, notes=None, sections=["notes"])

    assert response.status_code == 404
    assert response.json()["detail"] == "No meeting notes available to export"


def test_analytics_are_read_only_when_asked_for(api) -> None:
    _run(api, sections=["notes"])
    assert api.state.analytics_reads == 0

    response = _run(api, sections=["analytics"], export_format="txt")
    assert api.state.analytics_reads == 1
    assert "## Who spoke" in response.text
    # Delivery was never measured; the export says so instead of measuring it.
    assert "Not measured yet." in response.text


def test_every_format_renders(api) -> None:
    for export_format, media_type, magic in (
        ("pdf", "application/pdf", b"%PDF"),
        (
            "docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            b"PK",
        ),
        ("txt", "text/plain; charset=utf-8", b"Nojoin"),
    ):
        response = _run(
            api,
            sections=["notes", "analytics", "transcript"],
            export_format=export_format,
        )
        assert response.status_code == 200, (export_format, response.text)
        assert response.headers["content-type"] == media_type
        assert response.content.startswith(magic)


def test_the_branding_setting_turns_the_letterhead_off(api) -> None:
    api.state.config = {EXPORT_BRANDING_KEY: False}
    response = _run(api, sections=["notes"], export_format="txt")

    assert "Generated by Nojoin" not in response.text
    assert "Nojoin \u00b7 Meeting record" not in response.text


def test_speaker_colours_from_the_dialog_are_accepted(api) -> None:
    colors = json.dumps({"SPEAKER_00": "violet", "SPEAKER_01": "not-a-colour"})
    response = _run(
        api, sections=["transcript"], export_format="docx", speaker_colors=colors
    )

    assert response.status_code == 200


def test_an_unknown_section_is_rejected(api) -> None:
    response = _run(api, sections=["audio"])

    assert response.status_code == 422
