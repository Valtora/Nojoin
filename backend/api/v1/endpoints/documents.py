from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
import os
import aiofiles
from uuid import uuid4

from backend.api.deps import get_db, get_current_user
from backend.models.recording import Recording
from backend.models.document import Document, DocumentStatus
from backend.models.user import User
from backend.worker.tasks import process_document_task

router = APIRouter()

# Configuration for documents storage
DOCUMENTS_DIR = os.getenv("DOCUMENTS_DIR", "data/documents")
os.makedirs(DOCUMENTS_DIR, exist_ok=True)

@router.get("/recordings/{recording_id}/documents", response_model=List[Document])
async def list_documents(
    recording_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    List all documents associated with a recording.
    """
    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Recording not found")

    stmt = select(Document).where(Document.recording_id == recording_id)
    result = await db.execute(stmt)
    documents = result.scalars().all()
    return documents

@router.post("/recordings/{recording_id}/documents", response_model=Document)
async def upload_document(
    recording_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Upload a document (PDF, TXT, MD) to be included in the context.
    """
    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Recording not found")

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
        raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")

    # Create Document entry
    document = Document(
        recording_id=recording_id,
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

    return document

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
