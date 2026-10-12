"""Which indexed chunks chat retrieval may return.

Chat sends the meeting's full canonical transcript with every turn, so the
retrieval step must not add excerpts of that same transcript: they repeat it,
and because the index is rebuilt only after processing they can carry text and
speaker names from before an edit. The pgvector ranking cannot run on SQLite,
so this exercises the filters the search applies.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlmodel import select

from backend.api.v1.endpoints.transcripts.routes_chat import _chat_retrieval_filters
from backend.models.context_chunk import ContextChunk
from backend.tests.test_chat_tag_context_ownership import _make_session_maker

CURRENT_RECORDING = 1
OWNER = 1

# (chunk id, recording id, document id): recordings 1 and 2 carry the owner's
# tag 1; recording 3 belongs to another user and carries their tag 2.
CHUNKS = (
    (1, 1, None),  # this meeting's transcript
    (2, 1, 10),  # this meeting's attached document
    (3, 2, None),  # a tagged meeting's transcript
    (4, 2, 20),  # a tagged meeting's attached document
    (5, 3, None),  # another user's transcript
)


def _matching_chunk_ids(tag_ids: list[int]) -> tuple[list[int] | None, list[int]]:
    async def _run():
        engine, maker = await _make_session_maker()
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "CREATE TABLE context_chunks (id INTEGER PRIMARY KEY, "
                    "recording_id INTEGER NOT NULL, document_id INTEGER)"
                )
            )
            for chunk_id, recording_id, document_id in CHUNKS:
                await connection.execute(
                    text("INSERT INTO context_chunks VALUES (:id, :rec, :doc)"),
                    {"id": chunk_id, "rec": recording_id, "doc": document_id},
                )
        transcript_filter, document_filter = _chat_retrieval_filters(
            CURRENT_RECORDING, tag_ids, OWNER
        )
        async with maker() as session:

            async def _ids(where) -> list[int]:
                result = await session.execute(select(ContextChunk.id).where(where))
                return sorted(result.scalars().all())

            transcript_ids = (
                None if transcript_filter is None else await _ids(transcript_filter)
            )
            document_ids = await _ids(document_filter)
        await engine.dispose()
        return transcript_ids, document_ids

    return asyncio.run(_run())


def test_without_tags_chat_retrieves_only_this_meetings_documents():
    transcript_ids, document_ids = _matching_chunk_ids([])

    assert transcript_ids is None
    assert document_ids == [2]


def test_tags_add_other_meetings_transcripts_but_never_this_one():
    # Recording 1 carries the tag itself, so the tag alone would pull its own
    # transcript back in; it must stay out.
    transcript_ids, document_ids = _matching_chunk_ids([1])

    assert transcript_ids == [3]
    assert document_ids == [2, 4]


def test_another_users_tag_widens_to_nothing_of_theirs():
    transcript_ids, document_ids = _matching_chunk_ids([1, 2])

    assert transcript_ids == [3]
    assert document_ids == [2, 4]
