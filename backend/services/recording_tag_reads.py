"""The tags of a page of recordings, read in one statement.

The recordings list returns each meeting's tags. Loading them per recording
would issue one query for every row on the page, so a list of 200 meetings
would cost 200 round trips. This reads every link on the page in a single
join, whatever the page's size, and builds each distinct tag's read model once
however many meetings carry it.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from backend.models.tag import RecordingTag, Tag, TagRead
from backend.utils.db_batching import bind_batches


async def load_tags_by_recording(
    db: AsyncSession, recording_ids: Sequence[int]
) -> dict[int, list[TagRead]]:
    """Each recording's tags, keyed by its internal id, in the order applied.

    A recording with no tags has no key. An empty ``recording_ids`` runs no
    query. Each id is one bind parameter, so a page larger than the bind limit
    is read in slices (backend/utils/db_batching.py); any real page is one.
    """
    tags: dict[int, list[TagRead]] = {}
    reads: dict[int, TagRead] = {}
    unique_ids = list(dict.fromkeys(recording_ids))
    for id_batch in bind_batches(unique_ids):
        result = await db.execute(
            select(RecordingTag.recording_id, RecordingTag.tag_id, Tag)
            .join(Tag, col(Tag.id) == col(RecordingTag.tag_id))
            .where(col(RecordingTag.recording_id).in_(id_batch))
            .order_by(col(RecordingTag.recording_id), col(RecordingTag.id))
        )
        for recording_id, tag_id, tag in result.all():
            read = reads.get(tag_id)
            if read is None:
                read = reads[tag_id] = TagRead.model_validate(tag)
            tags.setdefault(recording_id, []).append(read)
    return tags
