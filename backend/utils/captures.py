"""Captures: images taken during a meeting, kept as documents of their own kind.

A capture is a ``documents`` row of kind ``capture``, at ``capture_ms`` on the
recording's audio timeline (the clock timeline notes run on). It is stored,
parsed, indexed, archived and deleted exactly as an attached document is, so
search, Meeting Chat and the MCP connector read it like any other. Notes
generation is the one reader that tells the two apart: it reads a capture only
while its ``use_in_notes`` switch is on, and an attached document always.

Nothing here imports FastAPI: the workers read the notes rule too.
"""

from __future__ import annotations

import hashlib
import json
from typing import Iterable, Optional

from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from backend.models.document import Document, DocumentKind, DocumentStatus


def format_timeline_ms(ms: int) -> str:
    """``MM:SS``, and ``H:MM:SS`` from an hour, as the live transcript writes times."""
    seconds = max(int(ms), 0) // 1000
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def capture_title(capture_ms: int) -> str:
    return f"Capture at {format_timeline_ms(capture_ms)}"


def is_capture(document: Document) -> bool:
    return document.kind == DocumentKind.CAPTURE.value


def feeds_notes(document: Document) -> bool:
    """Whether notes generation reads this document once it is parsed."""
    return not is_capture(document) or bool(document.use_in_notes)


def feeds_notes_clause():
    """``feeds_notes`` as a WHERE clause."""
    return or_(
        col(Document.kind) != DocumentKind.CAPTURE.value,
        col(Document.use_in_notes).is_(True),
    )


def captures_statement(recording_id: int):
    """A recording's captures in timeline order, ties in the order they were taken."""
    return (
        select(Document)
        .where(Document.recording_id == recording_id)
        .where(Document.kind == DocumentKind.CAPTURE.value)
        .order_by(col(Document.capture_ms), col(Document.created_at), col(Document.id))
    )


def captures_signature(rows: Iterable[tuple[int, object, bool]]) -> str:
    """``count:hash`` of each capture's id, parse status and notes switch.

    Changes whenever the captures list would change in what the page shows, so
    the recording's detail read can carry it on every poll for one narrow
    query, as ``timeline_notes_list_signature`` does for notes.
    """
    triples = sorted(
        (int(document_id), str(getattr(status, "value", status)), bool(use_in_notes))
        for document_id, status, use_in_notes in rows
    )
    encoded = json.dumps(triples, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
    return f"{len(triples)}:{digest}"


async def load_captures_signature_async(db: AsyncSession, recording_id: int) -> str:
    rows = await db.execute(
        select(Document.id, Document.status, Document.use_in_notes)
        .where(Document.recording_id == recording_id)
        .where(Document.kind == DocumentKind.CAPTURE.value)
    )
    return captures_signature(rows.all())


def switch_makes_notes_stale(
    document: Document, *, notes_status: Optional[str], notes: Optional[str]
) -> bool:
    """Whether turning a capture's notes switch leaves the notes behind it.

    Only a parsed capture changes what notes generation reads, and only notes
    already written can be behind; an unparsed capture flags them itself when
    its parse ends, if its switch is on then.
    """
    return (
        document.status == DocumentStatus.READY
        and notes_status == "completed"
        and bool((notes or "").strip())
    )
