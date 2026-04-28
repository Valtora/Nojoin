from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
import logging
import os
import aiofiles
from uuid import uuid4

from backend.api.error_handling import sanitized_http_exception
from backend.api.deps import get_db, get_current_user
from backend.models.recording import Recording
from backend.models.recording_public import DocumentPublicRead, serialize_document
from backend.models.document import Document, DocumentStatus
from backend.models.user import User
from backend.services.recording_identity_service import get_recording_by_public_id
from backend.worker.tasks import process_document_task

router = APIRouter()
logger = logging.getLogger(__name__)


async def _get_owned_recording(db: AsyncSession, recording_public_id: str, user_id: int) -> Recording:
    recording = await get_recording_by_public_id(db, recording_public_id, user_id=user_id)
    if recording is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    return recording

# Configuration for documents storage
DOCUMENTS_DIR = os.getenv("DOCUMENTS_DIR", "data/documents")
os.makedirs(DOCUMENTS_DIR, exist_ok=True)

@router.get("/recordings/{recording_id}/documents", response_model=List[DocumentPublicRead])
async def list_documents(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    List all documents associated with a recording.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    stmt = select(Document).where(Document.recording_id == recording.id)
    result = await db.execute(stmt)
    documents = result.scalars().all()
    return [serialize_document(document, recording_public_id=recording.public_id) for document in documents]

@router.post("/recordings/{recording_id}/documents", response_model=DocumentPublicRead)
async def upload_document(
    recording_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Upload a document (PDF, TXT, MD) to be included in the context.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    # Validate file type
    allowed_types = ["application/pdf", "text/plain", "text/markdown"]
    # Simple check on content_type or extension
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in [".pdf", ".txt", ".md"]:
        raise HTTPException(status_code=400, detail="Unsupported file type. Only PDF, TXT, and Markdown are supported.")

    # Save file
    unique_filename = f"{uuid4()}{file_ext}"
    file_path = os.path.join(DOCUMENTS_DIR, unique_filename)

    try:
        async with aiofiles.open(file_path, 'wb') as out_file:
            content = await file.read()
            await out_file.write(content)
    except Exception as e:
        raise sanitized_http_exception(
            logger=logger,
            status_code=500,
            client_message="Failed to save the uploaded document.",
            log_message=f"Failed to persist uploaded document '{file.filename}' for recording {recording.public_id}.",
            exc=e,
        )

    # Create Document entry
    document = Document(
        recording_id=recording.id,
        title=file.filename,
        file_path=file_path,
        file_type=file.content_type or "application/octet-stream",
        status=DocumentStatus.PENDING
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)

    # Trigger processing task
    process_document_task.delay(document.id)

    return serialize_document(document, recording_public_id=recording.public_id)

@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
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
