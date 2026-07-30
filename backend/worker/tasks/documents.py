"""Document parsing: structural extraction, optional visual analysis, indexing.

Runs on its own Celery lane. Parsing is unbounded by design -- there is no page
cap -- so a large document can occupy a worker slot for a long time, and putting
that on the shared io lane would starve Meeting Edge and meeting chat during a
live recording.

Two properties make an unbounded parse survivable:

* Pages are persisted as they complete, so a worker restart resumes from the
  first missing page rather than repeating vision calls that were already paid
  for.
* Vision calls fan out a few at a time, which cuts wall-clock on a long document
  without tripping provider rate limits or burning a subscription window.
"""

import concurrent.futures
import logging
import os
from typing import List, Optional

from backend.models.context_chunk import ContextChunk
from backend.models.document import Document, DocumentParseMode, DocumentStatus
from backend.models.document_page import DocumentPage, PageParseMode
from backend.processing.documents import (
    UnsupportedDocumentError,
    chunk_page_content,
    is_rendered_page_format,
    is_vision_only_format,
    open_document,
)
from backend.processing.documents.vision import merge_page_content, transcribe_page
from backend.processing.text_embedding_version import TEXT_EMBEDDING_VERSION
from backend.utils.vision import VisionUnsupportedError

from .constants import *  # noqa: F403 - shared task imports, matching sibling modules

logger = logging.getLogger(__name__)

# Concurrent vision calls within one document. Small on purpose: roughly a 3x
# wall-clock win on a long document, while staying well clear of provider rate
# limits and of a CLI OAuth subscription's usage window.
VISION_FAN_OUT = 3

# Pages held in memory before flushing to the database. Bounds peak memory on a
# document with hundreds of pages while keeping the write count sane.
PAGE_FLUSH_BATCH = 8

# Stage labels, rendered verbatim in the UI. Kept short and in progress order.
STAGE_READING = "Reading pages"
STAGE_ANALYSING = "Analysing pages with AI"
STAGE_INDEXING = "Indexing for search"

_NO_VISION_WARNING = (
    "Parsed without visual analysis: {reason} Charts, diagrams and scanned "
    "pages may be missing. Re-parse this document after selecting a "
    "vision-capable model."
)


def _resolve_backend_for_document(session, document: Document):
    """The LLM backend to use for this document's owner, or None.

    Returns None when the owner has no usable AI configuration at all, which is
    a normal state for a fresh install and downgrades the parse rather than
    failing it.
    """
    recording = session.get(Recording, document.recording_id)  # noqa: F405
    if recording is None or recording.user_id is None:
        return None

    user = session.get(User, recording.user_id)  # noqa: F405
    user_settings = (user.settings if user else None) or {}
    llm_config = resolve_llm_config(  # noqa: F405
        session, user_settings, user_id=recording.user_id
    )
    if llm_config.missing_configuration_message():
        return None
    try:
        return get_llm_backend_with_secondary(  # noqa: F405
            llm_config, purpose="document_parsing"
        )
    except Exception as e:  # noqa: BLE001 - a bad config must not fail the parse
        logger.warning("Could not build an LLM backend for document parsing: %s", e)
        return None


def _existing_page_numbers(session, document_id: int) -> set[int]:
    """Page numbers already stored, so a resumed parse skips them."""
    rows = session.exec(
        select(DocumentPage.page_number).where(  # noqa: F405
            DocumentPage.document_id == document_id
        )
    ).all()
    return set(rows)


def _transcribe_batch(backend, pages, *, is_rendered: bool) -> dict[int, str]:
    """Vision-transcribe a batch of pages concurrently.

    Returns page number to Markdown for the ones that succeeded. A page that
    fails is simply absent, and falls back to its structural text --
    ``VisionUnsupportedError`` is re-raised because it condemns every remaining
    page, not just this one.
    """
    results: dict[int, str] = {}
    if not pages:
        return results

    with concurrent.futures.ThreadPoolExecutor(max_workers=VISION_FAN_OUT) as pool:
        futures = {
            pool.submit(
                transcribe_page, backend, page, is_rendered_page=is_rendered
            ): page
            for page in pages
        }
        unsupported: Optional[VisionUnsupportedError] = None
        for future in concurrent.futures.as_completed(futures):
            page = futures[future]
            try:
                text = future.result()
            except VisionUnsupportedError as exc:
                unsupported = exc
                continue
            except Exception as e:  # noqa: BLE001 - one page failing is survivable
                logger.warning(
                    "Vision transcription failed for page %s: %s", page.page_number, e
                )
                continue
            if text:
                results[page.page_number] = text

    if unsupported is not None:
        raise unsupported
    return results


def _index_page(session, document: Document, page: DocumentPage) -> int:
    """Embed and store the chunks for one page. Returns the chunk count."""
    chunks = chunk_page_content(page.content)
    if not chunks:
        return 0

    from backend.processing.text_embedding import get_text_embedding_service

    vectors = get_text_embedding_service().embed(chunks)
    if not vectors or len(vectors) != len(chunks):
        logger.warning(
            "Embedding returned %s vectors for %s chunks on page %s",
            len(vectors or []),
            len(chunks),
            page.page_number,
        )
        return 0

    for index, (content, vector) in enumerate(zip(chunks, vectors)):
        session.add(
            ContextChunk(
                recording_id=document.recording_id,
                document_id=document.id,
                document_page_id=page.id,
                content=content,
                embedding=vector,
                embedding_version=TEXT_EMBEDDING_VERSION,
                meta={
                    "source": "document",
                    "chunk_index": index,
                    "page_number": page.page_number,
                    "page_title": page.title,
                    "document_title": document.title,
                },
            )
        )
    return len(chunks)


def _clear_previous_parse(session, document: Document) -> None:
    """Drop pages and chunks from an earlier parse of the same document.

    Used by an explicit re-parse. A resumed parse must NOT call this: the whole
    point of persisting pages incrementally is that they survive a restart.
    """
    for chunk in session.exec(
        select(ContextChunk).where(ContextChunk.document_id == document.id)  # noqa: F405
    ).all():
        session.delete(chunk)
    for page in session.exec(
        select(DocumentPage).where(DocumentPage.document_id == document.id)  # noqa: F405
    ).all():
        session.delete(page)
    document.pages_parsed = 0
    session.commit()


@celery_app.task(  # noqa: F405
    name="backend.worker.tasks.process_document_task",
    base=DatabaseTask,  # noqa: F405
    bind=True,
)
def process_document_task(self, document_id: int, force_reparse: bool = False):
    """Parse an uploaded document into pages, then embed those pages.

    ``force_reparse`` discards any previous result first; without it the task
    resumes, skipping pages already stored.
    """
    session = self.session
    document = session.get(Document, document_id)
    if not document:
        logger.error("Document %s not found.", document_id)
        return

    try:
        document.status = DocumentStatus.PROCESSING
        document.error_message = None
        document.parse_warning = None
        session.add(document)
        session.commit()

        if force_reparse:
            _clear_previous_parse(session, document)

        if not os.path.exists(document.file_path):
            raise FileNotFoundError("The uploaded file is no longer on disk.")

        _parse_document(session, document)

    except UnsupportedDocumentError as e:
        _fail(session, document, str(e))
    except Exception as e:  # noqa: BLE001 - any parse failure marks the document
        logger.error("Failed to process document %s: %s", document_id, e, exc_info=True)
        _fail(session, document, str(e))


def _fail(session, document: Document, message: str) -> None:
    document.status = DocumentStatus.ERROR
    document.error_message = message
    document.parse_stage = None
    session.add(document)
    session.commit()


class _PageWriter:
    """Batches page sources through the vision pass and into the database.

    A class rather than a closure because the vision state is genuinely
    mutable: the first ``VisionUnsupportedError`` switches the rest of the
    document to structural and records why, and pages transcribed before that
    point keep their richer content.
    """

    def __init__(  # noqa: PLR0913 - one argument per piece of parse state
        self,
        session,
        document: Document,
        *,
        backend,
        use_vision: bool,
        is_rendered: bool,
        already_parsed: int,
    ) -> None:
        self._session = session
        self._document = document
        self._backend = backend
        self._is_rendered = is_rendered
        self._already_parsed = already_parsed
        self.use_vision = use_vision
        self.warning: Optional[str] = None
        self.stored: List[DocumentPage] = []

    def _transcriptions(self, batch: List) -> dict[int, str]:
        if not self.use_vision:
            return {}
        try:
            return _transcribe_batch(
                self._backend,
                [page for page in batch if page.images],
                is_rendered=self._is_rendered,
            )
        except VisionUnsupportedError as exc:
            self.use_vision = False
            self.warning = _NO_VISION_WARNING.format(reason=f"{exc}")
            logger.info(
                "Vision unavailable for document %s: %s", self._document.id, exc
            )
            return {}

    def flush(self, batch: List) -> None:
        if not batch:
            return
        transcriptions = self._transcriptions(batch)

        for page_source in batch:
            was_visual = page_source.page_number in transcriptions
            page = DocumentPage(
                document_id=self._document.id,
                page_number=page_source.page_number,
                title=page_source.title,
                content=merge_page_content(
                    page_source,
                    transcriptions.get(page_source.page_number),
                    is_rendered_page=self._is_rendered,
                ),
                parse_mode=(
                    PageParseMode.VISUAL if was_visual else PageParseMode.STRUCTURAL
                ),
            )
            self._session.add(page)
            self.stored.append(page)

        self._document.pages_parsed = self._already_parsed + len(self.stored)
        self._session.add(self._document)
        self._session.commit()


def _flag_notes_stale(session, document: Document) -> None:
    """Mark existing notes as not reflecting this document.

    Only when notes already exist. Documents feed notes generation now, so one
    that finishes parsing afterwards leaves the notes incomplete -- but
    regenerating spends the user's quota and overwrites their edits, so this
    raises a prompt rather than acting.
    """
    transcript = session.exec(
        select(Transcript).where(  # noqa: F405
            Transcript.recording_id == document.recording_id  # noqa: F405
        )
    ).first()
    if transcript is None or transcript.notes_status != "completed":
        return
    if not (transcript.notes or "").strip():
        return
    transcript.notes_stale_documents = True
    session.add(transcript)
    session.commit()


def _resolve_vision(session, document: Document):
    """(backend, use_vision, warning) for this document's requested parse mode."""
    if document.parse_mode != DocumentParseMode.VISUAL:
        return None, False, None
    backend = _resolve_backend_for_document(session, document)
    if backend is None:
        return (
            None,
            False,
            _NO_VISION_WARNING.format(
                reason="no AI provider is configured for this account."
            ),
        )
    return backend, True, None


def _parse_document(session, document: Document) -> None:
    """Walk the document's pages, persist each, then index it."""
    backend, use_vision, warning = _resolve_vision(session, document)

    source = open_document(document.file_path, want_images=use_vision)
    is_rendered = is_rendered_page_format(document.file_path)

    document.page_count = source.page_count
    document.parse_stage = STAGE_ANALYSING if use_vision else STAGE_READING
    session.add(document)
    session.commit()

    already_parsed = _existing_page_numbers(session, document.id)
    writer = _PageWriter(
        session,
        document,
        backend=backend,
        use_vision=use_vision,
        is_rendered=is_rendered,
        already_parsed=len(already_parsed),
    )

    pending: List = []
    for page_source in source.pages():
        if page_source.page_number in already_parsed:
            continue
        pending.append(page_source)
        if len(pending) >= PAGE_FLUSH_BATCH:
            writer.flush(pending)
            pending = []
    writer.flush(pending)

    # An image upload has no text layer at all, so a structural fallback
    # produces nothing. That is an error, not an empty success.
    if is_vision_only_format(document.file_path) and not any(
        (page.content or "").strip() for page in writer.stored
    ):
        raise RuntimeError(
            "This image could not be read because no vision-capable AI model "
            "was available. Select a model that supports images, then re-parse."
        )

    document.parse_stage = STAGE_INDEXING
    session.add(document)
    session.commit()

    total_chunks = 0
    for page in writer.stored:
        total_chunks += _index_page(session, document, page)
    session.commit()

    document.status = DocumentStatus.READY
    document.parse_warning = warning or writer.warning
    document.parse_stage = None
    session.add(document)
    session.commit()

    _flag_notes_stale(session, document)

    logger.info(
        "Parsed document %s: %s pages, %s chunks%s",
        document.id,
        document.pages_parsed,
        total_chunks,
        " (structural only)" if document.parse_warning else "",
    )


# ---------------------------------------------------------------------------
# Post-upgrade rebuild
# ---------------------------------------------------------------------------
#
# The embedding model change is a hard cutover: the migration empties
# context_chunks, because vectors from two models are not comparable. Search and
# meeting chat therefore return nothing until each recording is re-indexed,
# which is what these two tasks do.
#
# Re-embedding is free local inference and never re-parses. A document whose
# pages are already stored is re-embedded straight from them; only a document
# predating document_pages -- whose text exists solely as old chunks that the
# migration deleted -- has to be parsed again, and then only structurally, so no
# provider quota is spent without the user asking.

# Recordings dispatched per sweep tick. The parse lane is isolated, but a large
# library should still converge over several ticks rather than flooding the
# queue in one.
REBUILD_BATCH_LIMIT = 50


@celery_app.task(  # noqa: F405
    name="backend.worker.tasks.rebuild_recording_index_task",
    base=DatabaseTask,  # noqa: F405
    bind=True,
)
def rebuild_recording_index_task(self, recording_id: int):
    """Re-embed one recording's transcript and documents at the current version."""
    session = self.session

    celery_app.send_task(  # noqa: F405
        "backend.worker.tasks.index_transcript_task", args=[recording_id]
    )

    documents = session.exec(
        select(Document).where(Document.recording_id == recording_id)  # noqa: F405
    ).all()
    for document in documents:
        pages = session.exec(
            select(DocumentPage)  # noqa: F405
            .where(DocumentPage.document_id == document.id)
            .order_by(DocumentPage.page_number)
        ).all()

        if not pages:
            # Parsed before pages were stored, so there is no text to re-embed.
            # Structural only: a silent re-run of visual parsing on upgrade
            # would spend the user's provider quota without being asked.
            document.parse_mode = DocumentParseMode.STRUCTURAL
            session.add(document)
            session.commit()
            celery_app.send_task(  # noqa: F405
                "backend.worker.tasks.process_document_task",
                args=[document.id, True],
            )
            continue

        for chunk in session.exec(
            select(ContextChunk).where(  # noqa: F405
                ContextChunk.document_id == document.id
            )
        ).all():
            session.delete(chunk)
        session.commit()

        for page in pages:
            _index_page(session, document, page)
        session.commit()

    logger.info("Rebuilt RAG index for recording %s", recording_id)


@celery_app.task(  # noqa: F405
    name="backend.worker.tasks.rebuild_text_embeddings_task",
    base=DatabaseTask,  # noqa: F405
    bind=True,
)
def rebuild_text_embeddings_task(self):
    """Find recordings with no current-version vectors and re-index them.

    Idempotent and re-entrant: it selects only recordings that still lack
    chunks at ``TEXT_EMBEDDING_VERSION``, so repeated ticks converge and a
    partially-completed sweep resumes where it left off.
    """
    session = self.session

    indexed = select(ContextChunk.recording_id).where(  # noqa: F405
        ContextChunk.embedding_version == TEXT_EMBEDDING_VERSION
    )
    pending = session.exec(
        select(Recording.id)  # noqa: F405
        .where(Recording.id.not_in(indexed))  # noqa: F405
        .limit(REBUILD_BATCH_LIMIT)
    ).all()

    for recording_id in pending:
        celery_app.send_task(  # noqa: F405
            "backend.worker.tasks.rebuild_recording_index_task", args=[recording_id]
        )

    logger.info(
        "Text embedding rebuild: dispatched %s recording(s)%s",
        len(pending),
        " (more remain)" if len(pending) == REBUILD_BATCH_LIMIT else "",
    )
    return len(pending)
