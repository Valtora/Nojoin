import json
import logging
from typing import List

from fastapi import Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select
from starlette.concurrency import iterate_in_threadpool

from backend.api.deps import get_current_user, get_db
from backend.api.error_handling import sanitized_http_exception
from backend.celery_app import celery_app
from backend.core.db import async_session_maker
from backend.models.chat import ChatMessage
from backend.models.context_chunk import ContextChunk
from backend.models.recording import Recording
from backend.models.recording_public import (
    ChatMessagePublicRead,
    serialize_chat_message,
)
from backend.models.speaker import RecordingSpeaker
from backend.models.tag import RecordingTag
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.utils.llm_config import resolve_llm_config_async

from .helpers import ChatRequest, _build_speaker_map, _get_owned_recording
from .router import router

logger = logging.getLogger(__name__)


@router.get("/{recording_id}/chat", response_model=List[ChatMessagePublicRead])
async def get_chat_history(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get the chat history for a recording.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    # Fetch chat messages
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.recording_id == recording.id)
        .order_by(ChatMessage.created_at)
    )
    result = await db.execute(stmt)
    messages = result.scalars().all()

    return [
        serialize_chat_message(message, recording_public_id=recording.public_id)
        for message in messages
    ]


@router.delete("/{recording_id}/chat")
async def clear_chat_history(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Clear the chat history for a recording.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    # Delete all chat messages for this recording
    stmt = select(ChatMessage).where(ChatMessage.recording_id == recording.id)
    result = await db.execute(stmt)
    messages = result.scalars().all()

    for msg in messages:
        await db.delete(msg)

    await db.commit()
    return {"status": "success"}


@router.post("/{recording_id}/chat")
async def chat_with_meeting(
    recording_id: str,
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Chat with the meeting transcript using LLM (Streaming).
    """
    # 1. Check Ownership & Fetch Data
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    stmt = select(Transcript).where(Transcript.recording_id == recording.id)
    result = await db.execute(stmt)
    transcript_obj = result.scalar_one_or_none()

    if not transcript_obj:
        raise HTTPException(status_code=404, detail="Transcript not found")

    meeting_notes = transcript_obj.notes or ""

    # 2. Get Chat History
    # Retrieve full history; truncation is deferred to the LLM backend if required.
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.recording_id == recording.id)
        .order_by(ChatMessage.created_at)
    )
    result = await db.execute(stmt)
    db_messages = result.scalars().all()

    # Convert to format expected by LLMBackend
    # Google Gemini: {"role": "user"|"model", "parts": [{"text": ...}]}
    # OpenAI/Anthropic: Adapted by backend logic, generally checks for standard roles.
    # Standardizing on Gemini format for internal consistency before backend adaptation.

    formatted_history = []
    for msg in db_messages:
        role = (
            "user" if msg.role == "user" else "model"
        )  # Gemini uses 'model' instead of 'assistant'
        formatted_history.append({"role": role, "parts": [{"text": msg.content}]})

    user_msg = ChatMessage(
        recording_id=recording.id,
        user_id=current_user.id,
        role="user",
        content=request.message,
    )
    db.add(user_msg)
    await db.commit()

    # --- RAG Context Retrieval ---
    context_text = ""
    relevant_chunks = []

    # --- RAG Context Retrieval ---
    context_text = ""
    relevant_chunks = []

    # Always attempt RAG, at least for the current recording
    try:
        # 1. Get embedding for the user query via Celery
        from fastapi.concurrency import run_in_threadpool

        task = celery_app.send_task(
            "backend.worker.tasks.get_text_embedding_task", args=[request.message]
        )
        embeddings = await run_in_threadpool(task.get, timeout=30)
        query_embedding = embeddings[0]

        # 2. Build Query Condition
        if request.tag_ids:
            # Identify relevant recordings from tags
            subquery = select(RecordingTag.recording_id).where(
                RecordingTag.tag_id.in_(request.tag_ids)
            )
            condition = (ContextChunk.recording_id.in_(subquery)) | (
                ContextChunk.recording_id == recording.id
            )
        else:
            # Only search current recording
            condition = ContextChunk.recording_id == recording.id

        # 3. Vector Search
        stmt = (
            select(ContextChunk)
            .where(condition)
            .order_by(ContextChunk.embedding.cosine_distance(query_embedding))
            .limit(5)
        )

        result = await db.execute(stmt)
        relevant_chunks = result.scalars().all()

        if relevant_chunks:
            context_sections = []
            for chunk in relevant_chunks:
                # Fetch recording with speakers for name resolution
                stmt = (
                    select(Recording)
                    .where(Recording.id == chunk.recording_id)
                    .options(
                        selectinload(Recording.speakers).options(
                            selectinload(RecordingSpeaker.global_speaker)
                        )
                    )
                )
                rec_result = await db.execute(stmt)
                rec = rec_result.scalar_one_or_none()

                rec_name = rec.name if rec else f"Recording {chunk.recording_id}"

                content = chunk.content
                # If it's a transcript chunk with speaker info, resolve speaker names
                if chunk.meta and chunk.meta.get("source") == "transcript" and rec:
                    speaker_map = _build_speaker_map(rec.speakers)
                    # Replace raw labels with names
                    # Replace raw diarization labels (e.g. "SPEAKER_XX:") with resolved names.
                    for label, name in speaker_map.items():
                        if label and name and label != name:
                            content = content.replace(f"{label}:", f"{name}:")

                context_sections.append(f"--- From {rec_name} ---\n{content}")

            context_text = "\n\n".join(context_sections)
            logger.info(f"Retrieved {len(relevant_chunks)} context chunks for chat.")

    except Exception as e:  # noqa: BLE001
        logger.error(f"RAG Retrieval failed: {e}")
        # Continue without context rather than failing

    # Augment the final user message with retrieved RAG context.

    augmented_message = request.message
    if context_text:
        augmented_message = f"Context from related meetings/documents:\n{context_text}\n\nUser Question: {request.message}"

    user_settings = current_user.settings or {}

    llm_config = await resolve_llm_config_async(db, user_settings)

    if not llm_config.api_key and llm_config.provider != "ollama":
        raise HTTPException(
            status_code=400,
            detail=f"No API key configured for {llm_config.provider}. Please configure it in settings.",
        )

    try:
        # Resolved through the package namespace at call time so test patches
        # against ``backend.api.v1.endpoints.transcripts.get_llm_backend_with_secondary``
        # continue to take effect after the module-to-package split.
        from backend.api.v1.endpoints import transcripts as transcripts_pkg

        llm_backend = transcripts_pkg.get_llm_backend_with_secondary(llm_config)
    except ValueError as e:
        raise sanitized_http_exception(
            logger=logger,
            status_code=400,
            client_message="Invalid AI configuration.",
            log_message=f"Rejected chat request for recording {recording_id} due to invalid AI configuration.",
            exc=e,
        )
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to initialize LLM backend: {e}")
        raise HTTPException(status_code=500, detail="Failed to initialize AI service")

    # 5. Define Streaming Generator
    async def stream_generator():
        full_response = ""
        try:
            generator = llm_backend.ask_question_streaming(
                user_question=augmented_message,
                meeting_notes=meeting_notes,
                diarized_transcript=None,  # Will be fetched inside using recording_id
                conversation_history=formatted_history,
                recording_id=recording.id,
            )

            # Iterate over the generator response asynchronously using threadpool
            # to prevent blocking the asyncio event loop
            async for chunk in iterate_in_threadpool(generator):
                if isinstance(chunk, dict) and chunk.get("type") == "notes_update":
                    yield f"event: notes_update\ndata: {json.dumps({'status': 'success'})}\n\n"
                else:
                    full_response += str(chunk)
                    # Yield SSE format
                    yield f"data: {json.dumps({'token': str(chunk)})}\n\n"

        except Exception as e:  # noqa: BLE001
            logger.error(f"Streaming error: {e}")
            error_msg = str(e).lower()

            # Map common upstream API failures to friendly messages
            if (
                "503" in error_msg
                or "unavailable" in error_msg
                or "overloaded" in error_msg
            ):
                user_msg = "The AI provider is currently experiencing high demand and is unavailable. Please try again later."
            elif (
                "429" in error_msg or "rate limit" in error_msg or "quota" in error_msg
            ):
                user_msg = "You have exceeded your AI provider's rate limit or quota. Please check your billing or try again later."
            elif "timeout" in error_msg or "deadline" in error_msg:
                user_msg = "The AI provider took too long to respond. Please try again."
            elif (
                "context window was exhausted" in error_msg
                or "done_reason=length" in error_msg
            ):
                user_msg = "The Ollama context window was exhausted before a full answer could be generated. Increase the Ollama context window or choose a larger-context model."
            else:
                user_msg = "An internal error occurred while communicating with the AI service. Please try again."

            yield f"data: {json.dumps({'error': user_msg})}\n\n"
            return

        # 6. Save Assistant Response to DB
        try:
            async with async_session_maker() as session:
                assistant_msg = ChatMessage(
                    recording_id=recording.id,
                    user_id=current_user.id,
                    role="assistant",
                    content=full_response,
                )
                session.add(assistant_msg)
                await session.commit()
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to save assistant message: {e}")

        yield "data: [DONE]\n\n"

    return StreamingResponse(stream_generator(), media_type="text/event-stream")
