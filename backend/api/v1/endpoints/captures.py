"""Captures: images taken during a meeting, at a time on its audio timeline.

A capture is stored as a document of kind ``capture`` (``backend.utils.captures``),
so the parse, the search index, the backup and every delete path treat it as
they treat an attached file. These routes add what a document has no use for:
an upload that takes the capture's time and accepts only PNG and JPEG, the
list in timeline order, the switch that lets notes generation read it, and a
small preview to draw in lists.

An upload is accepted whatever the recording's state, as a document's is, so
a frame taken while the meeting records is stored at once, beside the audio,
and parsed on the parse lane while capture goes on.
"""

import logging
import os
from pathlib import Path
from typing import List
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.api.deps import get_current_user, get_db
from backend.api.error_handling import sanitized_http_exception
from backend.core.task_dispatch import dispatch_task
from backend.models.document import Document, DocumentKind, DocumentParseMode
from backend.models.recording import Recording
from backend.models.recording_public import DocumentPublicRead, serialize_document
from backend.models.timeline_note_public import TIMELINE_MS_MAX
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.utils.capture_images import (
    SIGNATURE_BYTES,
    CaptureImageError,
    check_capture_image,
    render_preview,
    sniff_capture_format,
)
from backend.utils.captures import (
    capture_title,
    captures_statement,
    is_capture,
    switch_makes_notes_stale,
)
from backend.utils.rate_limit import enforce_upload_concurrency
from backend.utils.stored_paths import confined_document_path
from backend.utils.time import utc_now
from backend.utils.upload_limit import (
    UPLOAD_LIMIT_CAPTURE,
    ensure_disk_headroom,
    stream_and_validate_upload,
)

from . import documents as documents_ep

router = APIRouter()
logger = logging.getLogger(__name__)

# A preview never changes for a given document: its file is written once.
PREVIEW_CACHE_CONTROL = "private, max-age=86400"


class CaptureUpdate(BaseModel):
    use_in_notes: bool


async def _owned_capture(
    db: AsyncSession, recording: Recording, capture_id: int
) -> Document:
    document = await db.get(Document, capture_id)
    if (
        document is None
        or document.recording_id != recording.id
        or not is_capture(document)
    ):
        raise HTTPException(status_code=404, detail="Capture not found")
    return document


@router.get(
    "/recordings/{recording_id}/captures", response_model=List[DocumentPublicRead]
)
async def list_captures(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The recording's captures in timeline order."""
    recording = await documents_ep._get_owned_recording(
        db, recording_id, current_user.id
    )
    result = await db.execute(captures_statement(recording.id))
    return [
        serialize_document(document, recording_public_id=recording.public_id)
        for document in result.scalars().all()
    ]


@router.post("/recordings/{recording_id}/captures", response_model=DocumentPublicRead)
async def upload_capture(  # noqa: PLR0913 - FastAPI dependencies are parameters
    request: Request,
    recording_id: str,
    file: UploadFile = File(...),
    capture_ms: int = Form(..., ge=0, le=TIMELINE_MS_MAX),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Store an image taken at ``capture_ms`` and queue it to be read.

    The image is a PNG or JPEG, told by its first bytes rather than its name or
    declared type, and checked before its row is written. It is always read
    with visual analysis where a vision model is configured (local OCR where
    not), so its contents are searchable as soon as the parse ends; notes
    generation reads it only once ``use_in_notes`` is switched on.
    """
    started_at = utc_now()
    recording = await documents_ep._get_owned_recording(
        db, recording_id, current_user.id
    )

    head = await file.read(SIGNATURE_BYTES)
    await file.seek(0)
    try:
        image_format = sniff_capture_format(head)
    except CaptureImageError as error:
        raise HTTPException(status_code=415, detail=str(error)) from error

    directory = documents_ep.DOCUMENTS_DIR
    ensure_disk_headroom(directory, documents_ep._content_length(request))
    file_path = os.path.join(directory, f"{uuid4()}{image_format.extension}")

    async with enforce_upload_concurrency(
        request, "upload_capture", str(current_user.id), 2
    ):
        try:
            size_bytes = await stream_and_validate_upload(
                file=file, dest_path=file_path, max_size=UPLOAD_LIMIT_CAPTURE
            )
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001 -- boundary: any failure to store the file becomes one sanitised 500
            raise sanitized_http_exception(
                logger=logger,
                status_code=500,
                client_message="Failed to save the capture.",
                log_message=f"Failed to persist a capture for recording {recording.public_id}.",
                exc=e,
            )
    try:
        await run_in_threadpool(check_capture_image, file_path)
    except CaptureImageError as error:
        # Written by this request a moment ago, and named by no row.
        Path(file_path).unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(error)) from error

    document = Document(
        recording_id=recording.id,
        kind=DocumentKind.CAPTURE.value,
        capture_ms=capture_ms,
        use_in_notes=False,
        title=capture_title(capture_ms),
        file_path=file_path,
        file_type=image_format.media_type,
        file_size_bytes=size_bytes,
        created_at=started_at,
        parse_mode=DocumentParseMode.VISUAL,
    )
    await documents_ep._insert_document(db, document)
    await db.commit()
    await db.refresh(document)

    task = await dispatch_task(
        "backend.worker.tasks.process_document_task", args=[document.id]
    )
    from backend.models.task import register_task_ownership

    await register_task_ownership(db, task.id, current_user.id)
    return serialize_document(document, recording_public_id=recording.public_id)


@router.patch(
    "/recordings/{recording_id}/captures/{capture_id}",
    response_model=DocumentPublicRead,
)
async def update_capture(
    recording_id: str,
    capture_id: int,
    update: CaptureUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Turn a capture's notes switch on or off.

    A change to a parsed capture marks notes already written as stale, as a
    document parsed after them does: they no longer reflect what generation
    would read. Regenerating stays the user's choice.
    """
    recording = await documents_ep._get_owned_recording(
        db, recording_id, current_user.id
    )
    document = await _owned_capture(db, recording, capture_id)
    if bool(document.use_in_notes) != update.use_in_notes:
        document.use_in_notes = update.use_in_notes
        db.add(document)
        transcript = (
            await db.execute(
                select(Transcript).where(Transcript.recording_id == recording.id)
            )
        ).scalar_one_or_none()
        if transcript is not None and switch_makes_notes_stale(
            document, notes_status=transcript.notes_status, notes=transcript.notes
        ):
            transcript.notes_stale_documents = True
            db.add(transcript)
        await db.commit()
        await db.refresh(document)
    return serialize_document(document, recording_public_id=recording.public_id)


@router.get("/documents/{document_id}/preview")
async def preview_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """A small JPEG of a capture, to draw in lists and markers.

    Captures only: their size was checked when they arrived, where an attached
    image may be far larger than any list needs to decode.
    """
    document = await db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    recording = await db.get(Recording, document.recording_id)
    if recording is None or recording.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Document not found")
    if not is_capture(document):
        raise HTTPException(status_code=404, detail="This document has no preview.")
    file_path = confined_document_path(document.file_path)
    if file_path is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if not os.path.exists(file_path):
        raise HTTPException(
            status_code=410, detail="The original file is no longer available."
        )
    try:
        data = await run_in_threadpool(render_preview, file_path)
    except CaptureImageError as error:
        logger.warning("Could not draw a preview of document %s", document.id)
        raise HTTPException(status_code=422, detail=str(error)) from error
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": PREVIEW_CACHE_CONTROL},
    )
