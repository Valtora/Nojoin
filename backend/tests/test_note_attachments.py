"""A file on a timeline note: stored once its note is, read only while switched on.

The routes run on the timeline notes API's SQLite fixture. The rules for what
the AI reads (notes generation, chat and search, the parse task) run against
the same rows.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select, text
from sqlmodel import Session, create_engine

import backend.api.v1.endpoints.transcripts.routes_note_attachments as attachment_routes
from backend.api.v1.endpoints import documents as documents_ep
from backend.models.document import Document, DocumentStatus
from backend.models.document_page import DocumentPage
from backend.models.timeline_note import TimelineNote
from backend.models.transcript import Transcript
from backend.tests import test_timeline_notes_api as notes_api
from backend.tests.sqlite_schemas import (
    DOCUMENT_PAGES_SCHEMA,
    DOCUMENTS_SCHEMA,
    TIMELINE_NOTES_SCHEMA,
)
from backend.utils import document_storage
from backend.utils.meeting_notes import build_documents_prompt_section
from backend.utils.notes_templates import load_attached_documents
from backend.worker.tasks import documents as document_tasks

# The timeline notes API's app and SQLite database, shared as fixtures.
session_maker = notes_api.session_maker
edge_refreshes = notes_api.edge_refreshes
app = notes_api.app
client = notes_api.client
BASE = notes_api.BASE


def _png(size: tuple[int, int] = (1280, 720)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (30, 90, 160)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def uploads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[tuple]:
    """Stores uploads under ``tmp_path`` and records each parse dispatched."""

    @contextlib.asynccontextmanager
    async def no_limit(*_args, **_kwargs):
        yield

    async def dispatch(name, args=None, **_kwargs):
        dispatched.append((name, args))
        return SimpleNamespace(id=f"task-{len(dispatched)}")

    async def register(*_args, **_kwargs):
        return None

    import backend.models.task as task_models

    dispatched: list[tuple] = []
    monkeypatch.setattr(documents_ep, "DOCUMENTS_DIR", str(tmp_path))
    monkeypatch.setattr(document_storage, "documents_root_dir", lambda: tmp_path)
    monkeypatch.setattr(documents_ep, "enforce_upload_concurrency", no_limit)
    monkeypatch.setattr(documents_ep, "ensure_disk_headroom", lambda *_a, **_k: None)
    monkeypatch.setattr(attachment_routes, "dispatch_task", dispatch)
    monkeypatch.setattr(task_models, "register_task_ownership", register)
    return dispatched


async def _note(client: AsyncClient, **payload) -> dict:
    response = await client.post(
        BASE,
        json={
            "kind": "point",
            "body": "Screen capture",
            "anchor_ms": 65_000,
            **payload,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _attach(client: AsyncClient, note_id: str, name="capture.png", data=None):
    return await client.post(
        f"{BASE}/{note_id}/attachment",
        files={"file": (name, data if data is not None else _png(), "image/png")},
    )


@pytest.mark.anyio
async def test_a_file_is_stored_on_its_note_unread_and_listed_with_it(
    client: AsyncClient, uploads: list[tuple], session_maker
) -> None:
    note = await _note(client)

    response = await _attach(client, note["id"])

    assert response.status_code == 201, response.text
    attachment = response.json()
    assert (attachment["status"], attachment["use_in_notes"]) == ("PENDING", False)
    assert attachment["is_image"] is True
    assert uploads == []
    listed = (await client.get(BASE)).json()
    assert listed[0]["attachment"]["id"] == attachment["id"]
    # A note's file is listed with its note, not among the meeting's documents.
    documents = await client.get("/api/v1/recordings/rec-owned/documents")
    assert documents.json() == []
    again = await _attach(client, note["id"], name="second.png")
    assert again.status_code == 409


@pytest.mark.anyio
async def test_a_file_is_refused_on_an_unknown_note_or_in_an_unsupported_format(
    client: AsyncClient, uploads: list[tuple]
) -> None:
    note = await _note(client)

    unknown = await _attach(client, "00000000-0000-4000-8000-000000000000")
    foreign = await client.post(
        f"/api/v1/transcripts/{notes_api.FOREIGN_REC}/timeline-notes/{note['id']}/attachment",
        files={"file": ("capture.png", _png(), "image/png")},
    )
    script = await _attach(client, note["id"], name="run.sh", data=b"echo hi")

    assert unknown.status_code == 404
    assert foreign.status_code == 404
    assert script.status_code == 400


@pytest.mark.anyio
async def test_switching_on_starts_the_parse_and_either_way_marks_notes_stale(
    client: AsyncClient, uploads: list[tuple], session_maker
) -> None:
    note = await _note(client)
    attachment = (await _attach(client, note["id"])).json()
    async with session_maker() as session:
        session.add(
            Transcript(recording_id=10, notes="## Summary", notes_status="completed")
        )
        await session.commit()
    path = f"{BASE}/{note['id']}/attachment"

    on = await client.patch(path, json={"use_in_notes": True})
    async with session_maker() as session:
        await session.execute(text("UPDATE transcripts SET notes_stale_documents = 0"))
        await session.execute(text("UPDATE documents SET status = 'READY'"))
        await session.commit()
    off = await client.patch(path, json={"use_in_notes": False})
    repeat = await client.patch(path, json={"use_in_notes": False})

    assert (on.json()["use_in_notes"], on.json()["status"]) == (True, "PENDING")
    assert uploads == [
        ("backend.worker.tasks.process_document_task", [attachment["id"], False])
    ]
    assert off.json()["use_in_notes"] is False
    assert repeat.status_code == 200
    async with session_maker() as session:
        stale = (await session.execute(select(Transcript))).scalar_one()
    assert stale.notes_stale_documents is True


@pytest.mark.anyio
async def test_the_list_signature_moves_with_a_notes_file(
    client: AsyncClient, uploads: list[tuple], session_maker
) -> None:
    from backend.services.timeline_note_service import (
        load_timeline_notes_list_signature_async,
    )

    note = await _note(client)
    async with session_maker() as session:
        bare = await load_timeline_notes_list_signature_async(session, 10)
    await _attach(client, note["id"])
    async with session_maker() as session:
        attached = await load_timeline_notes_list_signature_async(session, 10)
    await client.patch(f"{BASE}/{note['id']}/attachment", json={"use_in_notes": True})
    async with session_maker() as session:
        switched = await load_timeline_notes_list_signature_async(session, 10)

    assert len({bare, attached, switched}) == 3


@pytest.mark.anyio
async def test_deleting_the_note_deletes_its_file(
    client: AsyncClient, uploads: list[tuple], session_maker, tmp_path: Path
) -> None:
    note = await _note(client)
    await _attach(client, note["id"])
    assert len(list(tmp_path.iterdir())) == 1

    response = await client.delete(f"{BASE}/{note['id']}")

    assert response.status_code == 204
    assert list(tmp_path.iterdir()) == []
    async with session_maker() as session:
        assert (await session.execute(select(Document))).first() is None


@pytest.mark.anyio
async def test_an_image_has_a_small_preview_and_another_file_none(
    client: AsyncClient, uploads: list[tuple]
) -> None:
    still = (await _attach(client, (await _note(client))["id"])).json()
    text_note = await _note(client, anchor_ms=70_000)
    notes = (await _attach(client, text_note["id"], name="notes.txt", data=b"x")).json()

    preview = await client.get(f"/api/v1/documents/{still['id']}/preview")
    none = await client.get(f"/api/v1/documents/{notes['id']}/preview")

    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/jpeg"
    assert max(Image.open(io.BytesIO(preview.content)).size) == 640
    assert none.status_code == 404


# --- What the AI reads -------------------------------------------------------


@pytest.fixture
def worker_session():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        for schema in (TIMELINE_NOTES_SCHEMA, DOCUMENTS_SCHEMA, DOCUMENT_PAGES_SCHEMA):
            connection.execute(text(schema))
    with Session(engine) as session:
        yield session


def _seed_documents(session: Session) -> None:
    session.add(
        TimelineNote(
            id=1, recording_id=10, kind="point", body="Slide", anchor_ms=65_000
        )
    )
    session.add(TimelineNote(id=2, recording_id=10, kind="head", body="Deck"))
    for document_id, title, note_id, on in (
        (1, "Agenda", None, False),
        (2, "off.png", 2, False),
        (3, "on.png", 1, True),
    ):
        session.add(
            Document(
                id=document_id,
                recording_id=10,
                title=title,
                file_path=f"/data/{title}",
                status=DocumentStatus.READY,
                note_id=note_id,
                use_in_notes=on,
            )
        )
        session.add(DocumentPage(document_id=document_id, page_number=1, content=title))
    session.commit()


def test_notes_read_documents_and_switched_on_files_with_their_notes_time(
    worker_session: Session,
) -> None:
    _seed_documents(worker_session)

    attached = load_attached_documents(worker_session, 10)

    assert [(d.title, d.attached_to_note, d.note_at_ms) for d in attached] == [
        ("Agenda", False, None),
        ("on.png", True, 65_000),
    ]
    section = build_documents_prompt_section(attached)
    assert 'note_at="01:05"' in section
    assert "attached to the note" in section
    assert "note_at" not in build_documents_prompt_section(attached[:1])


def test_a_file_switched_off_is_not_parsed(
    worker_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_documents(worker_session)
    worker_session.execute(text("UPDATE documents SET status = 'PENDING' WHERE id = 2"))
    worker_session.commit()
    parsed: list[int] = []
    monkeypatch.setattr(
        document_tasks,
        "_parse_document",
        lambda _s, document, _p: parsed.append(document.id),
    )
    task = document_tasks.process_document_task
    monkeypatch.setattr(task, "_session", worker_session, raising=False)

    task.run(2)

    assert parsed == []
    assert worker_session.get(Document, 2).status == DocumentStatus.PENDING
