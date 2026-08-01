import logging
import os
from typing import List
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.api.deps import get_current_user, get_db
from backend.api.error_handling import sanitized_http_exception
from backend.core.task_dispatch import dispatch_task
from backend.models.document import (
    Document,
    DocumentParseMode,
    DocumentStatus,
    parse_looks_stalled,
)
from backend.models.recording import Recording
from backend.models.recording_public import DocumentPublicRead, serialize_document
from backend.models.user import User
from backend.processing.documents import SUPPORTED_EXTENSIONS
from backend.services.recording_identity_service import get_recording_by_public_id
from backend.utils.path_manager import PathManager
from backend.utils.rate_limit import enforce_upload_concurrency
from backend.utils.upload_limit import (
    UPLOAD_LIMIT_DOCUMENT,
    ensure_disk_headroom,
    stream_and_validate_upload,
)

router = APIRouter()
logger = logging.getLogger(__name__)


async def _get_owned_recording(
    db: AsyncSession, recording_public_id: str, user_id: int
) -> Recording:
    recording = await get_recording_by_public_id(
        db, recording_public_id, user_id=user_id
    )
    if recording is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    return recording


# Configuration for documents storage. Resolved through PathManager so it tracks
# NOJOIN_DATA_DIR rather than the process working directory.
DOCUMENTS_DIR = str(PathManager().documents_directory)
os.makedirs(DOCUMENTS_DIR, exist_ok=True)


async def create_text_document(
    db: AsyncSession,
    *,
    recording: Recording,
    title: str,
    content: str,
    user_id: int,
) -> Document:
    """Persist authored text as an attached markdown document and queue parsing.

    The MCP attach_document tool's ingest path: text arrives as a JSON
    argument rather than a multipart file, so the upload streaming, size
    negotiation, and concurrency guard of upload_document do not apply.
    Parsing is structural: the source is already text, so no vision
    provider is ever called for it.
    """
    payload = content.encode("utf-8")
    ensure_disk_headroom(DOCUMENTS_DIR, len(payload))
    file_path = os.path.join(DOCUMENTS_DIR, f"{uuid4()}.md")
    with open(file_path, "wb") as handle:
        handle.write(payload)

    document = Document(
        recording_id=recording.id,
        title=title,
        file_path=file_path,
        file_type="text/markdown",
        file_size_bytes=len(payload),
        status=DocumentStatus.PENDING,
        parse_mode=DocumentParseMode.STRUCTURAL,
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)

    task = await dispatch_task(
        "backend.worker.tasks.process_document_task", args=[document.id]
    )
    from backend.models.task import register_task_ownership

    await register_task_ownership(db, task.id, user_id)
    return document


@router.get(
    "/recordings/{recording_id}/documents", response_model=List[DocumentPublicRead]
)
async def list_documents(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List all documents associated with a recording.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    stmt = select(Document).where(Document.recording_id == recording.id)
    result = await db.execute(stmt)
    documents = result.scalars().all()

    # Backfill sizes for documents that predate the column. Bounded to one
    # recording's documents and done once, since the value then persists.
    if any(_backfill_size(document) for document in list(documents)):
        await db.commit()

    return [
        serialize_document(document, recording_public_id=recording.public_id)
        for document in documents
    ]


def _backfill_size(document: Document) -> bool:
    """Fill in a missing file size from the stored file. Returns True if set.

    Only documents uploaded before the column existed lack one. Reading it from
    disk is accurate rather than a guess: an uploaded document is written once
    and never rewritten, so the file's size *is* its upload size.
    """
    if document.file_size_bytes is not None:
        return False
    try:
        document.file_size_bytes = os.path.getsize(document.file_path)
    except OSError:
        # The file is gone. Leave it NULL, which renders as an em dash, rather
        # than recording a zero that would read as an empty document.
        return False
    return True


@router.get("/documents/{document_id}/download")
async def download_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the original uploaded file.

    Serves the stored file under its original title rather than the UUID it is
    saved as, so a download is recognisable. Ownership is checked through the
    recording, exactly as delete and re-parse do.
    """
    document = await db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    recording = await db.get(Recording, document.recording_id)
    if not recording or recording.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Document not found")

    if not document.file_path or not os.path.exists(document.file_path):
        raise HTTPException(
            status_code=410, detail="The original file is no longer available."
        )

    return FileResponse(
        path=document.file_path,
        # Sanitised: the title is user-supplied and ends up in a
        # Content-Disposition header, where a quote or newline could inject one.
        filename=_safe_download_name(document.title, document.file_path),
        media_type=document.file_type or "application/octet-stream",
    )


def _safe_download_name(title: str, file_path: str) -> str:
    """A filename safe to place in a Content-Disposition header."""
    cleaned = "".join(
        character
        for character in (title or "")
        if character.isprintable() and character not in '"\\/\r\n'
    ).strip()
    if not cleaned:
        cleaned = f"document{os.path.splitext(file_path)[1] or ''}"
    return cleaned


def _content_length(request: Request) -> int | None:
    raw = request.headers.get("content-length")
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


@router.post("/recordings/{recording_id}/documents", response_model=DocumentPublicRead)
async def upload_document(  # noqa: PLR0913 - FastAPI dependencies are parameters
    request: Request,
    recording_id: str,
    file: UploadFile = File(...),
    deep_parse: bool = Form(True),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload a document to be included in the meeting's context.

    ``deep_parse`` defaults to true: pages are sent to a vision-capable model so
    charts, diagrams and scanned pages survive. Setting it false restricts the
    parse to structural extraction, which costs nothing and calls no provider.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    file_ext = os.path.splitext(file.filename or "")[1].lower()
    if file_ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported file type. Supported formats: "
                + ", ".join(
                    sorted(ext.lstrip(".").upper() for ext in SUPPORTED_EXTENSIONS)
                )
                + "."
            ),
        )

    # Refuse before writing anything if the volume cannot take it. The size cap
    # alone is not enough: many uploads under the cap can still fill a disk.
    ensure_disk_headroom(DOCUMENTS_DIR, _content_length(request))

    unique_filename = f"{uuid4()}{file_ext}"
    file_path = os.path.join(DOCUMENTS_DIR, unique_filename)

    async with enforce_upload_concurrency(
        request, "upload_document", str(current_user.id), 2
    ):
        try:
            size_bytes = await stream_and_validate_upload(
                file=file,
                dest_path=file_path,
                max_size=UPLOAD_LIMIT_DOCUMENT,
            )
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise sanitized_http_exception(
                logger=logger,
                status_code=500,
                client_message="Failed to save the uploaded document.",
                log_message=f"Failed to persist uploaded document '{file.filename}' for recording {recording.public_id}.",
                exc=e,
            )

    document = Document(
        recording_id=recording.id,
        title=file.filename,
        file_path=file_path,
        file_type=file.content_type or "application/octet-stream",
        file_size_bytes=size_bytes,
        status=DocumentStatus.PENDING,
        parse_mode=(
            DocumentParseMode.VISUAL if deep_parse else DocumentParseMode.STRUCTURAL
        ),
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)

    task = await dispatch_task(
        "backend.worker.tasks.process_document_task", args=[document.id]
    )
    from backend.models.task import register_task_ownership

    await register_task_ownership(db, task.id, current_user.id)

    return serialize_document(document, recording_public_id=recording.public_id)


@router.post("/documents/{document_id}/reparse", response_model=DocumentPublicRead)
async def reparse_document(
    document_id: int,
    deep_parse: bool = True,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Parse an already-uploaded document again, discarding the previous result.

    The escape hatch for a document parsed before a vision model was configured,
    or one whose visual parse was downgraded. Always a full re-parse rather than
    a resume, since the point is to redo work the stored pages already hold.
    """
    document = await db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    recording = await db.get(Recording, document.recording_id)
    if not recording or recording.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Document not found")

    # A live parse touches the row on every page batch, so a PROCESSING
    # document that has not been written to in a long time is one whose worker
    # died -- an OOM kill, a restart, a segfault. Refusing those outright left
    # the document permanently unrecoverable from the UI, which is a worse
    # failure than the duplicate parse the guard exists to prevent.
    if document.status == DocumentStatus.PROCESSING and not parse_looks_stalled(
        document
    ):
        raise HTTPException(
            status_code=409,
            detail="This document is already being parsed.",
        )

    if not os.path.exists(document.file_path):
        raise HTTPException(
            status_code=410,
            detail="The original file is no longer available, so it cannot be parsed again.",
        )

    document.parse_mode = (
        DocumentParseMode.VISUAL if deep_parse else DocumentParseMode.STRUCTURAL
    )
    document.status = DocumentStatus.PENDING
    document.error_message = None
    document.parse_warning = None
    # Reset progress immediately. The worker clears the old pages anyway, but
    # until it picks the job up the client would otherwise render the previous
    # run's "7 of 7" and look finished before it has started.
    document.pages_parsed = 0
    document.page_count = None
    document.parse_stage = "Queued"
    db.add(document)
    await db.commit()
    await db.refresh(document)

    task = await dispatch_task(
        "backend.worker.tasks.process_document_task",
        args=[document.id, True],
    )
    from backend.models.task import register_task_ownership

    await register_task_ownership(db, task.id, current_user.id)

    return serialize_document(document, recording_public_id=recording.public_id)


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Delete a document and its context chunks.
    """
    document = await db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Check ownership via recording
    recording = await db.get(Recording, document.recording_id)
    if not recording or recording.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Document not found")

    # Remove file from disk
    if document.file_path and os.path.exists(document.file_path):
        try:
            os.remove(document.file_path)
        except OSError:
            pass

    await db.delete(document)
    await db.commit()

    return {"status": "success"}
