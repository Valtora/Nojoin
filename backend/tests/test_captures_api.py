"""Captures: stored as documents of their own kind, with a time and a notes switch.

The routes under test take an image with its time on the audio timeline, keep
it out of the documents list, list captures in timeline order, turn the notes
switch (marking written notes stale), and draw a small preview.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image, ImageDraw
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_current_user, get_db
from backend.api.v1.endpoints import captures as captures_ep
from backend.api.v1.endpoints import documents as documents_ep
from backend.models.document import Document, DocumentStatus
from backend.models.recording import Recording
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.tests.sqlite_schemas import (
    DOCUMENTS_SCHEMA,
    RECORDINGS_SCHEMA,
    TRANSCRIPTS_SCHEMA,
    USERS_SCHEMA,
)
from backend.utils.captures import captures_signature, format_timeline_ms

T0 = datetime(2026, 10, 24, 9, 0, 0)


def image_bytes(fmt: str = "PNG", size: tuple[int, int] = (1600, 900)) -> bytes:
    """A made-up slide: a light page with a dark band, as a frame might be."""
    image = Image.new("RGB", size, (250, 250, 250))
    ImageDraw.Draw(image).rectangle(
        (size[0] // 8, size[1] // 8, size[0] // 2, size[1] // 5), fill=(20, 20, 20)
    )
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


@pytest.fixture
def api(monkeypatch, tmp_path: Path):
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    app = FastAPI()
    app.include_router(documents_ep.router)
    app.include_router(captures_ep.router)

    async def override_db():
        async with maker() as session:
            yield session

    state = SimpleNamespace(user=None, dispatched=[])
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: state.user

    @contextlib.asynccontextmanager
    async def no_limit(*_args, **_kwargs):
        yield

    async def dispatch(name, args=None, **_kwargs):
        state.dispatched.append((name, args))
        return SimpleNamespace(id=f"task-{len(state.dispatched)}")

    async def register(*_args, **_kwargs):
        return None

    import backend.models.task as task_models

    monkeypatch.setattr(captures_ep, "enforce_upload_concurrency", no_limit)
    monkeypatch.setattr(captures_ep, "ensure_disk_headroom", lambda *_a, **_k: None)
    monkeypatch.setattr(captures_ep, "dispatch_task", dispatch)
    monkeypatch.setattr(task_models, "register_task_ownership", register)
    monkeypatch.setattr(documents_ep, "DOCUMENTS_DIR", str(tmp_path))

    async def setup(*, notes: str | None = None, owner: int = 1) -> Recording:
        async with engine.begin() as conn:
            for schema in (
                USERS_SCHEMA,
                RECORDINGS_SCHEMA,
                TRANSCRIPTS_SCHEMA,
                DOCUMENTS_SCHEMA,
            ):
                await conn.execute(text(schema))
        state.user = User(id=1, username="alice", hashed_password="x", role="user")
        async with maker() as session:
            session.add(state.user)
            if owner != 1:
                session.add(
                    User(id=owner, username="bob", hashed_password="x", role="user")
                )
            recording = Recording(
                id=3,
                name="Quarterly plan",
                audio_path="/tmp/plan.wav",
                status="UPLOADING",
                user_id=owner,
                upload_progress=0,
                processing_progress=0,
            )
            session.add(recording)
            session.add(
                Transcript(
                    recording_id=3,
                    notes=notes,
                    notes_status="completed" if notes else "pending",
                    transcript_status="completed",
                )
            )
            await session.commit()
            await session.refresh(recording)
            return recording

    async def call(method: str, url: str, **kwargs):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            return await client.request(method, url, **kwargs)

    yield SimpleNamespace(
        setup=setup, call=call, maker=maker, state=state, tmp_path=tmp_path
    )
    app.dependency_overrides.clear()


def _upload(api, recording, data: bytes, *, capture_ms: object = 65_000, name="frame"):
    return api.call(
        "POST",
        f"/recordings/{recording.public_id}/captures",
        files={"file": (name, data, "application/octet-stream")},
        data={"capture_ms": str(capture_ms)},
    )


def test_an_upload_is_stored_as_a_capture_at_its_time(api) -> None:
    async def go():
        recording = await api.setup()
        response = await _upload(api, recording, image_bytes("PNG"))
        assert response.status_code == 200, response.text
        async with api.maker() as session:
            return response.json(), await session.get(Document, response.json()["id"])

    body, stored = asyncio.run(go())

    assert body["kind"] == "capture"
    assert body["capture_ms"] == 65_000
    assert body["use_in_notes"] is False
    assert body["title"] == "Capture at 01:05"
    assert stored.file_type == "image/png"
    assert stored.file_path.endswith(".png")
    assert stored.parse_mode == "VISUAL"
    assert Path(stored.file_path).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    # Read at once, on the parse lane, like any document.
    assert api.state.dispatched == [
        ("backend.worker.tasks.process_document_task", [stored.id])
    ]


def test_the_format_comes_from_the_bytes_not_the_name(api) -> None:
    async def go():
        recording = await api.setup()
        response = await _upload(api, recording, image_bytes("JPEG"), name="x.png")
        assert response.status_code == 200, response.text
        async with api.maker() as session:
            return await session.get(Document, response.json()["id"])

    stored = asyncio.run(go())

    assert stored.file_type == "image/jpeg"
    assert stored.file_path.endswith(".jpg")


@pytest.mark.parametrize(
    ("data", "status"),
    [
        (b"%PDF-1.7 not an image", 415),
        (b"GIF89a" + b"\x00" * 64, 415),
        # A PNG signature on bytes no reader can open.
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, 400),
    ],
)
def test_anything_but_a_readable_png_or_jpeg_is_refused(api, data, status) -> None:
    async def go():
        recording = await api.setup()
        response = await _upload(api, recording, data)
        async with api.maker() as session:
            count = len((await session.execute(text("SELECT id FROM documents"))).all())
        return response, count

    response, count = asyncio.run(go())

    assert response.status_code == status, response.text
    assert count == 0
    assert not any(api.tmp_path.iterdir()), "a refused upload leaves no file"
    assert api.state.dispatched == []


@pytest.mark.parametrize("capture_ms", [-1, "soon"])
def test_a_capture_needs_a_time_on_the_timeline(api, capture_ms) -> None:
    async def go():
        recording = await api.setup()
        return await _upload(api, recording, image_bytes(), capture_ms=capture_ms)

    assert asyncio.run(go()).status_code == 422


def test_someone_elses_recording_takes_no_capture(api) -> None:
    async def go():
        recording = await api.setup(owner=2)
        return await _upload(api, recording, image_bytes())

    assert asyncio.run(go()).status_code == 404


async def _seed(api, recording, *rows: dict) -> list[int]:
    ids = []
    async with api.maker() as session:
        for index, row in enumerate(rows):
            document = Document(
                recording_id=recording.id,
                file_path=str(api.tmp_path / f"seed-{index}.png"),
                created_at=T0 + timedelta(seconds=index),
                updated_at=T0 + timedelta(seconds=index),
                **{"file_type": "image/png", **row},
            )
            session.add(document)
            await session.flush()
            ids.append(document.id)
        await session.commit()
    return ids


def test_captures_and_documents_are_listed_apart(api) -> None:
    async def go():
        recording = await api.setup()
        await _seed(
            api,
            recording,
            {"title": "Agenda.pdf"},
            {"title": "Capture at 02:00", "kind": "capture", "capture_ms": 120_000},
            {"title": "Capture at 00:30", "kind": "capture", "capture_ms": 30_000},
        )
        documents = await api.call(
            "GET", f"/recordings/{recording.public_id}/documents"
        )
        captures = await api.call("GET", f"/recordings/{recording.public_id}/captures")
        return documents.json(), captures.json()

    documents, captures = asyncio.run(go())

    assert [item["title"] for item in documents] == ["Agenda.pdf"]
    assert documents[0]["kind"] == "document"
    # Timeline order, not upload order.
    assert [item["title"] for item in captures] == [
        "Capture at 00:30",
        "Capture at 02:00",
    ]


def _switch(api, recording, capture_id: int, on: bool):
    return api.call(
        "PATCH",
        f"/recordings/{recording.public_id}/captures/{capture_id}",
        json={"use_in_notes": on},
    )


async def _stale(api) -> bool:
    async with api.maker() as session:
        transcript = (
            (
                await session.execute(
                    text("SELECT notes_stale_documents FROM transcripts")
                )
            )
            .scalars()
            .one()
        )
    return bool(transcript)


@pytest.mark.parametrize(
    ("status", "notes", "stale"),
    [
        (DocumentStatus.READY, "# Notes\n\nWritten.", True),
        # Not parsed yet: the parse flags the notes when it ends, if the
        # switch is on by then.
        (DocumentStatus.PROCESSING, "# Notes\n\nWritten.", False),
        # No notes to be behind.
        (DocumentStatus.READY, None, False),
    ],
)
def test_the_switch_marks_written_notes_stale(api, status, notes, stale) -> None:
    async def go():
        recording = await api.setup(notes=notes)
        (capture_id,) = await _seed(
            api,
            recording,
            {
                "title": "Capture at 00:10",
                "kind": "capture",
                "capture_ms": 10_000,
                "status": status,
            },
        )
        response = await _switch(api, recording, capture_id, True)
        return response, await _stale(api)

    response, is_stale = asyncio.run(go())

    assert response.status_code == 200, response.text
    assert response.json()["use_in_notes"] is True
    assert is_stale is stale


def test_turning_the_switch_back_off_also_marks_the_notes(api) -> None:
    async def go():
        recording = await api.setup(notes="# Notes")
        (capture_id,) = await _seed(
            api,
            recording,
            {
                "title": "Capture at 00:10",
                "kind": "capture",
                "capture_ms": 10_000,
                "status": DocumentStatus.READY,
                "use_in_notes": True,
            },
        )
        off = await _switch(api, recording, capture_id, False)
        return off, await _stale(api)

    off, is_stale = asyncio.run(go())

    assert off.json()["use_in_notes"] is False
    assert is_stale is True


def test_a_document_has_no_switch(api) -> None:
    async def go():
        recording = await api.setup(notes="# Notes")
        (document_id,) = await _seed(api, recording, {"title": "Agenda.pdf"})
        return await _switch(api, recording, document_id, True)

    assert asyncio.run(go()).status_code == 404


def test_the_preview_is_a_small_jpeg(api) -> None:
    async def go():
        recording = await api.setup()
        upload = await _upload(api, recording, image_bytes("PNG", (3840, 2160)))
        return await api.call("GET", f"/documents/{upload.json()['id']}/preview")

    response = asyncio.run(go())

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/jpeg"
    assert "max-age" in response.headers["cache-control"]
    with Image.open(io.BytesIO(response.content)) as preview:
        assert preview.format == "JPEG"
        assert max(preview.size) == 640
        assert preview.size == (640, 360)


@pytest.mark.parametrize("file_type", ["text/markdown", "image/png"])
def test_only_a_capture_has_a_preview(api, file_type) -> None:
    # An attached image may be far larger than a capture is allowed to be.
    async def go():
        recording = await api.setup()
        (document_id,) = await _seed(
            api, recording, {"title": "attached", "file_type": file_type}
        )
        return await api.call("GET", f"/documents/{document_id}/preview")

    assert asyncio.run(go()).status_code == 404


def test_someone_elses_capture_has_no_preview(api) -> None:
    async def go():
        recording = await api.setup(owner=2)
        (capture_id,) = await _seed(
            api,
            recording,
            {"title": "Capture at 00:01", "kind": "capture", "capture_ms": 1_000},
        )
        return await api.call("GET", f"/documents/{capture_id}/preview")

    assert asyncio.run(go()).status_code == 404


def test_the_signature_follows_status_and_switch() -> None:
    base = captures_signature([(1, DocumentStatus.PROCESSING, False)])

    assert base.startswith("1:")
    assert captures_signature([(1, DocumentStatus.READY, False)]) != base
    assert captures_signature([(1, DocumentStatus.PROCESSING, True)]) != base
    assert captures_signature([]) == captures_signature([])
    assert captures_signature([]).startswith("0:")


@pytest.mark.parametrize(
    ("ms", "label"),
    [(0, "00:00"), (65_400, "01:05"), (3_600_000, "1:00:00"), (3_725_000, "1:02:05")],
)
def test_capture_times_read_as_the_live_transcript_writes_them(ms, label) -> None:
    assert format_timeline_ms(ms) == label
