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
async def test_a_file_is_refused_on_an_unknown_note_or_another_users(
    client: AsyncClient, uploads: list[tuple]
) -> None:
    note = await _note(client)

    unknown = await _attach(client, "00000000-0000-4000-8000-000000000000")
    foreign = await client.post(
        f"/api/v1/transcripts/{notes_api.FOREIGN_REC}/timeline-notes/{note['id']}/attachment",
        files={"file": ("capture.png", _png(), "image/png")},
    )

    assert unknown.status_code == 404
    assert foreign.status_code == 404


@pytest.mark.anyio
async def test_any_file_attaches_but_one_nojoin_cannot_read_stays_download_only(
    client: AsyncClient, uploads: list[tuple], tmp_path: Path
) -> None:
    zipped = await _note(client, anchor_ms=1_000)
    page = await _note(client, anchor_ms=2_000)

    archive = (
        await _attach(client, zipped["id"], name="slides.zip", data=b"PK\x03\x04")
    ).json()
    markup = await _attach(
        client,
        page["id"],
        name="../../\u202eevil.svg",
        data=b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
    )
    switched = await client.patch(
        f"{BASE}/{zipped['id']}/attachment", json={"use_in_notes": True}
    )
    served = await client.get(f"/api/v1/documents/{markup.json()['id']}/download")
    preview = await client.get(f"/api/v1/documents/{markup.json()['id']}/preview")

    assert (archive["readable"], archive["file_type"]) == (
        False,
        "application/octet-stream",
    )
    assert sorted(path.suffix for path in tmp_path.iterdir()) == [".svg", ".zip"]
    assert markup.json()["title"] == "evil.svg"
    assert markup.json()["readable"] is False
    assert switched.status_code == 409
    assert uploads == []
    assert served.headers["content-type"] == "application/octet-stream"
    assert served.headers["content-disposition"].startswith("attachment;")
    assert served.headers["x-content-type-options"] == "nosniff"
    assert preview.status_code == 404


@pytest.mark.anyio
async def test_an_image_downloads_as_an_image_for_the_viewer(
    client: AsyncClient, uploads: list[tuple]
) -> None:
    still = (await _attach(client, (await _note(client))["id"])).json()

    served = await client.get(f"/api/v1/documents/{still['id']}/download")

    assert (still["readable"], still["is_image"]) == (True, True)
    assert served.headers["content-type"] == "image/png"
    assert served.headers["x-content-type-options"] == "nosniff"


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


# --- Removing a note's file (#734) -------------------------------------------


async def _written_notes(session_maker) -> None:
    async with session_maker() as session:
        session.add(
            Transcript(recording_id=10, notes="## Summary", notes_status="completed")
        )
        await session.commit()


async def _notes_stale(session_maker) -> bool:
    async with session_maker() as session:
        transcript = (await session.execute(select(Transcript))).scalar_one()
        return bool(transcript.notes_stale_documents)


async def _signature(session_maker) -> str:
    from backend.services.timeline_note_service import (
        load_timeline_notes_list_signature_async,
    )

    async with session_maker() as session:
        return await load_timeline_notes_list_signature_async(session, 10)


@pytest.mark.anyio
async def test_removing_a_captioned_notes_file_keeps_the_note_as_text(
    client: AsyncClient,
    uploads: list[tuple],
    session_maker,
    tmp_path: Path,
    edge_refreshes: list[int],
) -> None:
    note = await _note(client, body="Budget slide")
    await _attach(client, note["id"])
    before = await _signature(session_maker)

    response = await client.delete(f"{BASE}/{note['id']}/attachment")

    assert response.status_code == 200, response.text
    kept = response.json()["note"]
    assert (kept["id"], kept["body"], kept["attachment"]) == (
        note["id"],
        "Budget slide",
        None,
    )
    assert kept["revision"] == note["revision"]
    assert list(tmp_path.iterdir()) == []
    async with session_maker() as session:
        assert (await session.execute(select(Document))).first() is None
    listed = (await client.get(BASE)).json()
    assert [(n["id"], n["attachment"]) for n in listed] == [(note["id"], None)]
    # Other viewers' polls see the list change; Meeting Edge reads no files.
    assert await _signature(session_maker) != before
    assert edge_refreshes == [10]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("payload", "note_goes"),
    [
        ({}, True),
        ({"body": "Screen capture of the budget"}, False),
        ({"kind": "head", "anchor_ms": None}, False),
    ],
    ids=["capture-text", "captioned", "head-note"],
)
async def test_only_a_capture_nobody_captioned_goes_with_its_file(
    client: AsyncClient,
    uploads: list[tuple],
    edge_refreshes: list[int],
    payload: dict,
    note_goes: bool,
) -> None:
    note = await _note(client, **payload)
    assert note["body"].startswith("Screen capture")
    await _attach(client, note["id"])

    response = await client.delete(f"{BASE}/{note['id']}/attachment")

    assert response.status_code == 200, response.text
    assert (response.json()["note"] is None) is note_goes
    listed = [n["id"] for n in (await client.get(BASE)).json()]
    assert listed == ([] if note_goes else [note["id"]])
    assert edge_refreshes == ([10, 10] if note_goes else [10])
    if note_goes:
        # Gone as the note's own delete leaves it: an edit in flight hears so.
        edit = await client.patch(
            f"{BASE}/{note['id']}", json={"expected_revision": 1, "body": "Late"}
        )
        assert edit.status_code == 410
        assert edit.json()["detail"]["code"] == "note_deleted_elsewhere"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("payload", "note_goes"),
    [
        ({"body": "agenda.pdf"}, True),
        ({"kind": "head", "anchor_ms": None, "body": "agenda.pdf"}, True),
        ({"body": "Agenda.pdf"}, False),
        ({"body": "agenda"}, False),
        ({"body": "agenda.pdf, page 2"}, False),
    ],
    ids=["file-name", "file-name-head-note", "other-case", "no-extension", "captioned"],
)
async def test_a_note_whose_text_is_still_its_files_name_goes_with_it(
    client: AsyncClient,
    uploads: list[tuple],
    payload: dict,
    note_goes: bool,
) -> None:
    note = await _note(client, **payload)
    await _attach(client, note["id"], name="agenda.pdf")

    response = await client.delete(f"{BASE}/{note['id']}/attachment")

    assert response.status_code == 200, response.text
    assert (response.json()["note"] is None) is note_goes
    listed = [n["id"] for n in (await client.get(BASE)).json()]
    assert listed == ([] if note_goes else [note["id"]])


@pytest.mark.anyio
@pytest.mark.parametrize(
    "name", ["Q3  Budget (final).XLSX", " minutes.docx ", "image.png"]
)
async def test_the_name_a_file_gives_its_note_is_its_title(
    client: AsyncClient, uploads: list[tuple], name: str
) -> None:
    # A file pasted or dropped with nothing typed gives its note its name as
    # the browser has it (noteOutbox.submit). The API trims the note's text
    # and the file's title alike and keeps case, extension and inner spaces,
    # so the two are the same string and removing the file takes the note.
    note = await _note(client, body=name)
    attachment = (await _attach(client, note["id"], name=name)).json()
    assert note["body"] == attachment["title"] == name.strip()

    response = await client.delete(f"{BASE}/{note['id']}/attachment")

    assert response.status_code == 200, response.text
    assert response.json()["note"] is None


@pytest.mark.parametrize(
    ("kind", "body", "goes"),
    [
        ("point", "Screen capture", True),
        ("head", "Screen capture", False),
        ("point", "Slide 4.png", True),
        ("selection", "Slide 4.png", True),
        ("point", "slide 4.png", False),
        ("point", "Slide 4", False),
        ("point", "Slide 4.png ", False),
        ("point", "Slide 4.png, the budget", False),
    ],
)
def test_only_text_nojoin_gave_the_note_is_automatic(
    kind: str, body: str, goes: bool
) -> None:
    from backend.utils.note_attachments import note_goes_with_its_file

    note = TimelineNote(public_id="n", recording_id=10, kind=kind, body=body)

    assert note_goes_with_its_file(note, "Slide 4.png") is goes


@pytest.mark.anyio
@pytest.mark.parametrize(
    "case",
    [
        ("Screen capture", True, True),
        ("Budget slide", True, True),
        ("Budget slide", False, False),
    ],
    ids=["capture-on", "captioned-on", "captioned-off"],
)
async def test_removing_a_file_used_in_notes_marks_them_stale(
    client: AsyncClient,
    uploads: list[tuple],
    session_maker,
    case: tuple[str, bool, bool],
) -> None:
    body, use_in_notes, stale = case
    note = await _note(client, body=body)
    await _attach(client, note["id"])
    await _written_notes(session_maker)
    if use_in_notes:
        await client.patch(
            f"{BASE}/{note['id']}/attachment", json={"use_in_notes": True}
        )
        async with session_maker() as session:
            await session.execute(
                text("UPDATE transcripts SET notes_stale_documents = 0")
            )
            await session.commit()

    response = await client.delete(f"{BASE}/{note['id']}/attachment")

    assert response.status_code == 200, response.text
    assert await _notes_stale(session_maker) is stale


@pytest.mark.anyio
async def test_deleting_a_note_whose_file_is_in_notes_marks_them_stale(
    client: AsyncClient, uploads: list[tuple], session_maker
) -> None:
    note = await _note(client, body="Budget slide")
    await _attach(client, note["id"])
    await _written_notes(session_maker)
    await client.patch(f"{BASE}/{note['id']}/attachment", json={"use_in_notes": True})
    async with session_maker() as session:
        await session.execute(text("UPDATE transcripts SET notes_stale_documents = 0"))
        await session.commit()

    response = await client.delete(f"{BASE}/{note['id']}")

    assert response.status_code == 204
    assert await _notes_stale(session_maker) is True


@pytest.mark.anyio
async def test_removing_refuses_what_has_no_file_to_remove(
    client: AsyncClient, uploads: list[tuple]
) -> None:
    bare = await _note(client, body="No file here")
    capture = await _note(client, anchor_ms=70_000)
    await _attach(client, capture["id"])

    none = await client.delete(f"{BASE}/{bare['id']}/attachment")
    unknown = await client.delete(
        f"{BASE}/00000000-0000-4000-8000-000000000000/attachment"
    )
    foreign = await client.delete(
        f"/api/v1/transcripts/{notes_api.FOREIGN_REC}/timeline-notes/{capture['id']}/attachment"
    )
    first = await client.delete(f"{BASE}/{capture['id']}/attachment")
    again = await client.delete(f"{BASE}/{capture['id']}/attachment")

    assert none.status_code == 404
    assert none.json()["detail"] == attachment_routes.NO_ATTACHMENT_DETAIL
    assert unknown.status_code == 404
    assert foreign.status_code == 404
    assert foreign.json()["detail"]["code"] == "recording_not_found"
    assert first.status_code == 200
    # Its note went with it, so a repeat finds the tombstone.
    assert again.status_code == 410


@pytest.mark.anyio
async def test_a_note_whose_file_was_removed_takes_no_other(
    client: AsyncClient, uploads: list[tuple], session_maker, tmp_path: Path
) -> None:
    note = await _note(client, body="Budget slide")
    await _attach(client, note["id"])
    await client.delete(f"{BASE}/{note['id']}/attachment")

    # A retry of the first upload, whose first attempt had landed.
    late = await _attach(client, note["id"])

    assert late.status_code == 410
    assert late.json()["detail"] == attachment_routes.FILE_REMOVED_DETAIL
    assert list(tmp_path.iterdir()) == []
    async with session_maker() as session:
        assert (await session.execute(select(Document))).first() is None


@pytest.mark.anyio
async def test_an_upload_stored_while_its_file_was_removed_is_refused(
    client: AsyncClient,
    uploads: list[tuple],
    session_maker,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    note = await _note(client, body="Budget slide")
    store_upload = documents_ep.store_upload

    async def store_then_removed(*args, **kwargs):
        stored = await store_upload(*args, **kwargs)
        # The removal commits while this upload is being stored.
        async with session_maker() as session:
            await session.execute(text("UPDATE timeline_notes SET file_removed = 1"))
            await session.commit()
        return stored

    monkeypatch.setattr(documents_ep, "store_upload", store_then_removed)

    late = await _attach(client, note["id"])

    assert late.status_code == 410
    assert list(tmp_path.iterdir()) == []
    async with session_maker() as session:
        assert (await session.execute(select(Document))).first() is None


@pytest.mark.anyio
async def test_a_notes_file_is_removed_through_its_note_only(
    client: AsyncClient, uploads: list[tuple], tmp_path: Path
) -> None:
    note = await _note(client)
    attachment = (await _attach(client, note["id"])).json()

    response = await client.delete(f"/api/v1/documents/{attachment['id']}")

    assert response.status_code == 409
    assert len(list(tmp_path.iterdir())) == 1
    assert (await client.get(BASE)).json()[0]["attachment"]["id"] == attachment["id"]


def test_the_capture_text_is_the_one_the_browser_writes() -> None:
    from backend.utils.note_attachments import CAPTURE_NOTE_BODY

    source = (
        Path(__file__).resolve().parents[2]
        / "frontend/src/lib/captures/useCaptureActions.ts"
    ).read_text()
    assert f'export const CAPTURE_NOTE_BODY = "{CAPTURE_NOTE_BODY}";' in source


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
