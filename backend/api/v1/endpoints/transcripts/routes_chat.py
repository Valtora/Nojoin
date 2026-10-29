import json
import logging
from typing import List

from fastapi import Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select
from starlette.concurrency import iterate_in_threadpool

from backend.api.deps import get_current_user, get_db
from backend.api.error_handling import sanitized_http_exception
from backend.core.db import async_session_maker
from backend.core.task_dispatch import dispatch_task
from backend.models.chat import ChatMessage
from backend.models.context_chunk import ContextChunk
from backend.models.recording import IN_FLIGHT_TRANSCRIPT_STATUSES, Recording
from backend.models.recording_public import (
    ChatMessagePublicRead,
    serialize_chat_message,
)
from backend.models.speaker import RecordingSpeaker
from backend.models.tag import RecordingTag
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.processing.text_embedding_version import TEXT_EMBEDDING_VERSION
from backend.prompts import render_prompt
from backend.services.live_chat import chat_transcript_in_progress
from backend.utils.chat_prompt import ChatTranscript, TranscriptExcerpts
from backend.utils.context_window import ContextWindowExceededError
from backend.utils.llm_config import CLI_PROVIDER, resolve_llm_config_async
from backend.utils.meeting_notes import escape_prompt_attribute
from backend.utils.note_attachments import unread_attachment_ids

from .helpers import ChatRequest, _build_speaker_map, _get_owned_recording
from .router import router

# Retrieval budgets, per source. Kept separate so an attached document can never
# be crowded out of the context by transcript matches, nor the reverse.
CHAT_TRANSCRIPT_CHUNK_BUDGET = 5
# Document chunks are whole pages, so a smaller count still carries more
# content than the transcript budget.
CHAT_DOCUMENT_CHUNK_BUDGET = 4
# Passages of this meeting's own transcript considered for a meeting too long
# to send whole. The backend keeps the most relevant that fit its window.
CHAT_EXCERPT_CANDIDATES = 40

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

    stmt = select(ChatMessage).where(ChatMessage.recording_id == recording.id)
    result = await db.execute(stmt)
    messages = result.scalars().all()

    for msg in messages:
        await db.delete(msg)

    await db.commit()
    return {"status": "success"}


def _tag_context_recording_ids(tag_ids: list[int], user_id: int):
    """Recording ids allowed to widen chat retrieval by tag.

    Tag ids arrive from the client, so the widening must stay inside the
    caller's own recordings: without the ownership join, any tag id would
    pull other users' context chunks into the retrieval pool.
    """
    return (
        select(RecordingTag.recording_id)
        .join(Recording, Recording.id == RecordingTag.recording_id)
        .where(
            RecordingTag.tag_id.in_(tag_ids),
            Recording.user_id == user_id,
        )
    )


def _chat_retrieval_filters(recording_id: int, tag_ids: list[int], user_id: int):
    """Where chat retrieval looks: (transcript filter or None, document filter).

    Chat already sends the meeting's full transcript with every turn, so
    excerpts of that same transcript would only repeat it. Worse, the index is
    rebuilt after processing, not after an edit or a rename, so an excerpt can
    carry text and speaker names the transcript no longer has, and the model
    would see both versions. Transcript excerpts therefore come only from the
    other meetings a tag widens the search to; attached documents come from
    this meeting and from those. A meeting too long to send whole is the
    exception, handled by ``_transcript_excerpts``.
    """
    # A note's file switched off is not read, even if it was parsed while on.
    is_document = and_(
        ContextChunk.document_id.is_not(None),
        ContextChunk.document_id.not_in(unread_attachment_ids()),
    )
    if not tag_ids:
        return None, and_(ContextChunk.recording_id == recording_id, is_document)

    tagged = _tag_context_recording_ids(tag_ids, user_id)
    transcript_filter = and_(
        ContextChunk.document_id.is_(None),
        ContextChunk.recording_id.in_(tagged),
        ContextChunk.recording_id != recording_id,
    )
    document_filter = and_(
        or_(
            ContextChunk.recording_id.in_(tagged),
            ContextChunk.recording_id == recording_id,
        ),
        is_document,
    )
    return transcript_filter, document_filter


def _excerpt_ranges(metas) -> tuple[tuple[float, float], ...]:
    """The (start, end) times of the transcript chunks among ``metas``, in order.

    Notes chunks share the rows a transcript search returns and carry no times.
    """
    ranges = [
        (float(meta["start"]), float(meta["end"]))
        for meta in metas
        if isinstance(meta, dict)
        and meta.get("source") == "transcript"
        and isinstance(meta.get("start"), (int, float))
        and isinstance(meta.get("end"), (int, float))
    ]
    return tuple(ranges[:CHAT_EXCERPT_CANDIDATES])


async def _transcript_excerpts(
    recording_id: int, query_embedding: list[float] | None
) -> TranscriptExcerpts:
    """Where this meeting's passages most relevant to the question are.

    For a meeting the provider refused whole. The search index finds the
    passages, and only their times are kept: the backend reads the lines at
    those times from the transcript as it is now, so an edit or a rename made
    since the index was built still shows. Without a query embedding or an
    index, no passages are chosen and chat answers from the notes.
    """
    if query_embedding is None:
        return TranscriptExcerpts(ranges=())
    try:
        async with async_session_maker() as session:
            stmt = (
                select(ContextChunk.meta)
                .where(ContextChunk.recording_id == recording_id)
                .where(ContextChunk.document_id.is_(None))
                .where(ContextChunk.embedding_version == TEXT_EMBEDDING_VERSION)
                .order_by(ContextChunk.embedding.cosine_distance(query_embedding))
                # Over-fetch, so notes chunks cannot crowd out the transcript.
                .limit(CHAT_EXCERPT_CANDIDATES * 2)
            )
            metas = (await session.execute(stmt)).scalars().all()
    except Exception as e:  # noqa: BLE001 -- boundary: chat still answers from the notes
        logger.error(f"Transcript excerpt search failed: {e}")
        return TranscriptExcerpts(ranges=())
    return TranscriptExcerpts(ranges=_excerpt_ranges(metas))


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
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    in_flight = recording.status in IN_FLIGHT_TRANSCRIPT_STATUSES

    stmt = select(Transcript).where(Transcript.recording_id == recording.id)
    result = await db.execute(stmt)
    transcript_obj = result.scalar_one_or_none()

    if not transcript_obj and not in_flight:
        raise HTTPException(status_code=404, detail="Transcript not found")
    transcript_in_progress = chat_transcript_in_progress(
        recording.status, transcript_obj
    )

    meeting_notes = (transcript_obj.notes if transcript_obj else "") or ""

    # Full history; the LLM backend truncates it if it must.
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.recording_id == recording.id)
        .order_by(ChatMessage.created_at)
    )
    result = await db.execute(stmt)
    db_messages = result.scalars().all()

    # History is kept in Gemini's shape ({"role": "user"|"model", "parts": [...]})
    # and each backend adapts it to its own API.

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
    query_embedding = None
    relevant_chunks = []
    transcript_chunks = []
    document_chunks = []

    # Always attempt RAG, at least for this meeting's attached documents.
    try:
        # 1. Get embedding for the user query via Celery
        from fastapi.concurrency import run_in_threadpool

        task = await dispatch_task(
            "backend.worker.tasks.get_text_embedding_task", args=[request.message]
        )
        embeddings = await run_in_threadpool(task.get, timeout=30)
        query_embedding = embeddings[0]

        # 2. Scope the search (see _chat_retrieval_filters).
        transcript_filter, document_filter = _chat_retrieval_filters(
            recording.id, request.tag_ids or [], current_user.id
        )

        # 3. Vector search, with a separate budget per source, so a strongly
        # matching transcript passage cannot take every slot and leave an
        # attached deck unrepresented, or the reverse.
        #
        # Every query is filtered to the current embedding version: vectors from
        # a previous model are not comparable, and scoring against them would
        # return noise ranked as though it were relevant.
        async def _search(source_filter, limit: int):
            stmt = (
                select(ContextChunk)
                .where(ContextChunk.embedding_version == TEXT_EMBEDDING_VERSION)
                .where(source_filter)
                .order_by(ContextChunk.embedding.cosine_distance(query_embedding))
                .limit(limit)
            )
            return (await db.execute(stmt)).scalars().all()

        if transcript_filter is not None:
            transcript_chunks = await _search(
                transcript_filter, CHAT_TRANSCRIPT_CHUNK_BUDGET
            )
        document_chunks = await _search(document_filter, CHAT_DOCUMENT_CHUNK_BUDGET)
        relevant_chunks = list(transcript_chunks) + list(document_chunks)

        if relevant_chunks:
            context_sections = []
            for chunk in relevant_chunks:
                is_document_chunk = chunk.document_id is not None
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

                rec_name = (
                    rec.name
                    if rec
                    else render_prompt(
                        "chat.recording_label", id=str(chunk.recording_id)
                    )
                )

                content = chunk.content
                if chunk.meta and chunk.meta.get("source") == "transcript" and rec:
                    speaker_map = _build_speaker_map(rec.speakers)
                    # Replace raw diarization labels (e.g. "SPEAKER_XX:") with resolved names.
                    for label, name in speaker_map.items():
                        if label and name and label != name:
                            content = content.replace(f"{label}:", f"{name}:")

                if is_document_chunk:
                    # Document text is authored outside the meeting, so it is
                    # untrusted input and is fenced accordingly. The instruction
                    # to treat it as data rides with the augmented message.
                    meta = chunk.meta or {}
                    label = meta.get("document_title") or render_prompt(
                        "chat.attached_document_label"
                    )
                    page = meta.get("page_number")
                    page_title = meta.get("page_title")
                    if page and page_title:
                        label = render_prompt(
                            "chat.document_page_title_label",
                            label=label,
                            page=str(page),
                            title=page_title,
                        )
                    elif page:
                        label = render_prompt(
                            "chat.document_page_label", label=label, page=str(page)
                        )
                    # State how the text was produced. Without this a model
                    # reading a vision transcription reports the document as
                    # "text extraction only" -- true of what it received, but
                    # wrong about the document, and misleading to the user.
                    origin = {
                        "VISUAL": render_prompt("chat.visual_origin"),
                        "OCR": render_prompt("chat.ocr_origin"),
                    }.get(
                        meta.get("parse_mode"), render_prompt("chat.text_layer_origin")
                    )
                    context_sections.append(
                        render_prompt(
                            "chat.retrieved_document",
                            recording=rec_name,
                            location=escape_prompt_attribute(label),
                            origin=origin,
                            content=content,
                        )
                    )
                else:
                    context_sections.append(
                        render_prompt(
                            "chat.related_transcript",
                            recording=rec_name,
                            content=content,
                        )
                    )

            context_text = "\n\n".join(context_sections)
            logger.info(
                "Retrieved %s transcript and %s document chunks for chat.",
                len(transcript_chunks),
                len(document_chunks),
            )

    except Exception as e:  # noqa: BLE001 -- boundary: retrieval is optional; chat continues without context
        logger.error(f"RAG Retrieval failed: {e}")
        # Continue without context rather than failing

    # Augment the final user message with retrieved RAG context.

    augmented_message = request.message
    if context_text:
        augmented_message = render_prompt(
            "chat.retrieved_context_question",
            context=context_text,
            question=request.message,
        )

    user_settings = current_user.settings or {}

    llm_config = await resolve_llm_config_async(
        db, user_settings, user_id=current_user.id
    )

    # ollama needs no key; cli authenticates with the user's subscription token
    # (validated at call time in the worker), so neither requires an api_key here.
    if not llm_config.api_key and llm_config.provider not in ("ollama", "cli"):
        raise HTTPException(
            status_code=400,
            detail=f"No API key configured for {llm_config.provider}. Please configure it in settings.",
        )

    # CLI OAuth chat runs in worker-io (the API image has no Agent SDK): dispatch
    # to a Celery task on the io lane and relay its token stream back over the
    # same SSE contract, so the browser client is unchanged. The worker persists
    # the assistant turn (single writer); this path deliberately does not.
    if llm_config.provider == CLI_PROVIDER:
        from backend.models.task import register_task_ownership
        from backend.services.chat_relay import relay_sse_frames

        task = await dispatch_task(
            "backend.worker.tasks.meeting_chat_task",
            args=[recording.id, augmented_message, formatted_history],
        )
        await register_task_ownership(db, task.id, current_user.id)
        return StreamingResponse(
            relay_sse_frames(task.id), media_type="text/event-stream"
        )

    try:
        # Resolved through the package namespace at call time so tests can patch
        # ``backend.api.v1.endpoints.transcripts.get_llm_backend_with_secondary``.
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
    except Exception as e:  # noqa: BLE001 -- boundary: backend construction raises provider-specific errors; all of them are a 500
        logger.error(f"Failed to initialize LLM backend: {e}")
        raise HTTPException(status_code=500, detail="Failed to initialize AI service")

    def ask(diarized_transcript: ChatTranscript):
        # None: the backend reads the whole transcript itself via recording_id.
        return llm_backend.ask_question_streaming(
            user_question=augmented_message,
            meeting_notes=meeting_notes,
            diarized_transcript=diarized_transcript,
            conversation_history=formatted_history,
            recording_id=recording.id,
        )

    async def stream_generator():
        full_response = ""
        answered = False
        excerpts: TranscriptExcerpts | None = None
        try:
            if transcript_in_progress:
                from backend.services.live_chat import stream_live_chat

                generator = stream_live_chat(ask, recording.id)
            else:
                generator = ask(None)
            while True:
                try:
                    # Iterate over the generator response asynchronously using
                    # threadpool to prevent blocking the asyncio event loop
                    async for chunk in iterate_in_threadpool(generator):
                        answered = True
                        if (
                            isinstance(chunk, dict)
                            and chunk.get("type") == "notes_update"
                        ):
                            yield f"event: notes_update\ndata: {json.dumps({'status': 'success'})}\n\n"
                        else:
                            full_response += str(chunk)
                            yield f"data: {json.dumps({'token': str(chunk)})}\n\n"
                    break
                except ContextWindowExceededError as overflow:
                    # Refused before any text, by every provider configured:
                    # ask again from the passages relevant to the question.
                    if transcript_in_progress or answered or excerpts is not None:
                        raise
                    logger.info(
                        "Meeting %s is too long for chat; answering from excerpts: %s",
                        recording.id,
                        overflow,
                    )
                    excerpts = await _transcript_excerpts(recording.id, query_embedding)
                    generator = ask(excerpts)

        except Exception as e:  # noqa: BLE001 -- boundary: any provider error ends the stream with a friendly message
            logger.error(f"Streaming error: {e}")
            from backend.services.chat_relay import friendly_chat_error

            yield f"data: {json.dumps({'error': friendly_chat_error(e)})}\n\n"
            return

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
        except Exception as e:  # noqa: BLE001 -- boundary: the answer was already streamed; a failed save must not undo that
            logger.error(f"Failed to save assistant message: {e}")

        yield "data: [DONE]\n\n"

    return StreamingResponse(stream_generator(), media_type="text/event-stream")
