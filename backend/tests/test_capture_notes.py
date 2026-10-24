"""Captures reach notes generation only while their switch is on.

Every attached document feeds the notes, as before. A capture is parsed and
indexed like one, but notes generation reads it, and a finished parse marks
written notes stale for it, only while its ``use_in_notes`` switch is on.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session

from backend.models.document import Document, DocumentStatus
from backend.models.document_page import DocumentPage, PageParseMode
from backend.models.recording import Recording
from backend.models.transcript import Transcript
from backend.processing.llm_backends.base import LLMBackend
from backend.tests.sqlite_schemas import (
    DOCUMENT_PAGES_SCHEMA,
    DOCUMENTS_SCHEMA,
    RECORDINGS_SCHEMA,
    TRANSCRIPTS_SCHEMA,
    USERS_SCHEMA,
)
from backend.utils.meeting_intelligence import (
    AutomaticMeetingIntelligenceRequest,
    build_automatic_meeting_intelligence_prompt,
)
from backend.utils.meeting_notes import (
    AttachedDocument,
    NotesPromptContext,
    build_documents_prompt_section,
)
from backend.utils.notes_templates import load_attached_documents
from backend.worker.tasks.documents import _flag_notes_stale

T0 = datetime(2026, 10, 24, 9, 0, 0)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        for schema in (
            USERS_SCHEMA,
            RECORDINGS_SCHEMA,
            TRANSCRIPTS_SCHEMA,
            DOCUMENTS_SCHEMA,
            DOCUMENT_PAGES_SCHEMA,
        ):
            conn.execute(text(schema))
    with Session(engine) as db:
        db.add(
            Recording(
                id=1,
                name="Quarterly plan",
                audio_path="/tmp/plan.wav",
                status="PROCESSED",
                upload_progress=100,
                processing_progress=100,
            )
        )
        db.add(
            Transcript(
                recording_id=1,
                notes="# Notes\n\nWritten earlier.",
                notes_status="completed",
                transcript_status="completed",
            )
        )
        db.commit()
        yield db


def _attach(db: Session, title: str, content: str, **fields) -> Document:
    document = Document(
        recording_id=1,
        title=title,
        file_path=f"/tmp/{title}",
        status=fields.pop("status", DocumentStatus.READY),
        created_at=T0 + timedelta(seconds=len(title)),
        **fields,
    )
    db.add(document)
    db.flush()
    db.add(
        DocumentPage(
            document_id=document.id,
            page_number=1,
            content=content,
            parse_mode=PageParseMode.VISUAL,
        )
    )
    db.commit()
    return document


def _capture(db: Session, title: str, content: str, *, at: int, on: bool, **fields):
    return _attach(
        db,
        title,
        content,
        kind="capture",
        capture_ms=at,
        use_in_notes=on,
        file_type="image/png",
        **fields,
    )


def test_only_captures_switched_on_reach_the_notes(session: Session) -> None:
    _attach(session, "Agenda.pdf", "Budget, hiring.")
    _capture(
        session, "Capture at 01:05", "Quarterly plan, Alice Example", at=65_000, on=True
    )
    _capture(session, "Capture at 02:00", "A chart nobody chose", at=120_000, on=False)
    _capture(
        session,
        "Capture at 03:00",
        "Still being read",
        at=180_000,
        on=True,
        status=DocumentStatus.PROCESSING,
    )

    documents = load_attached_documents(session, 1)

    assert [document.title for document in documents] == [
        "Agenda.pdf",
        "Capture at 01:05",
    ]
    agenda, capture = documents
    assert agenda.captured_at_ms is None
    assert capture.captured_at_ms == 65_000
    assert "Alice Example" in capture.text


def test_a_capture_is_marked_with_its_time_in_the_prompt() -> None:
    section = build_documents_prompt_section(
        [
            AttachedDocument(title="Agenda.pdf", text="Budget."),
            AttachedDocument(
                title="Capture at 01:05", text="Quarterly plan", captured_at_ms=65_400
            ),
        ]
    )

    assert '<attached_document title="Agenda.pdf">' in section
    assert '<attached_document title="Capture at 01:05" captured_at="01:05">' in section
    assert "refer to it by its time" in section
    assert "never embed" in section


def test_a_prompt_without_captures_is_unchanged() -> None:
    documents = [AttachedDocument(title="Agenda.pdf", text="Budget.")]

    section = build_documents_prompt_section(documents)

    assert "captured_at" not in section
    assert "capture" not in section.lower()


def test_both_notes_paths_render_a_capture_alike() -> None:
    documents = [
        AttachedDocument(
            title="Capture at 01:05", text="Quarterly plan", captured_at_ms=65_000
        )
    ]
    expected = build_documents_prompt_section(documents)

    notes = LLMBackend.build_notes_prompt(
        None,
        "SPEAKER_00: hello",
        {"SPEAKER_00": "Alice Example"},
        notes_context=NotesPromptContext(documents=documents),
    )
    unified = build_automatic_meeting_intelligence_prompt(
        AutomaticMeetingIntelligenceRequest(
            resolved_transcript="Alice Example: hello",
            unresolved_speakers=(),
            documents=documents,
        ),
        None,
    )

    assert expected in notes
    assert expected in unified


def _stale(session: Session) -> bool:
    session.expire_all()
    transcript = session.get(Transcript, 1)
    assert transcript is not None
    return bool(transcript.notes_stale_documents)


def test_a_capture_left_out_of_the_notes_leaves_them_as_they_are(
    session: Session,
) -> None:
    capture = _capture(session, "Capture at 00:10", "A slide", at=10_000, on=False)

    _flag_notes_stale(session, capture)

    assert _stale(session) is False


def test_a_capture_switched_on_while_it_was_read_marks_the_notes(
    session: Session,
) -> None:
    capture = _capture(session, "Capture at 00:10", "A slide", at=10_000, on=False)
    # Switched on in another session while the parse ran.
    session.execute(text("UPDATE documents SET use_in_notes = 1"))
    session.commit()

    _flag_notes_stale(session, capture)

    assert _stale(session) is True


def test_an_attached_document_still_marks_the_notes(session: Session) -> None:
    document = _attach(session, "Agenda.pdf", "Budget.")

    _flag_notes_stale(session, document)

    assert _stale(session) is True
