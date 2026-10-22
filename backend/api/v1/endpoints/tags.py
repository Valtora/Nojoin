from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.api.deps import get_current_user, get_db
from backend.models.recording import Recording
from backend.models.tag import RecordingTag, Tag, TagCreate, TagRead, TagUpdate
from backend.models.user import User
from backend.services.recording_identity_service import (
    get_recording_by_public_id,
    get_recordings_by_public_ids,
)
from backend.services.tag_ownership import (
    commit_tag_name,
    find_tag_by_name,
    get_or_create_tag,
    link_recording_tag,
    require_own_parent,
    require_parent_outside_subtree,
    require_unused_name,
)

router = APIRouter()


async def _get_owned_recording(
    db: AsyncSession, recording_public_id: str, user_id: int
) -> Recording:
    recording = await get_recording_by_public_id(
        db, recording_public_id, user_id=user_id
    )
    if recording is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    return recording


# Far past any real selection and far under the statement bind ceiling, so it
# never fires in normal use. It exists so one request cannot ask for unbounded
# work: every id costs a row load and a link write. Without it an oversized
# payload is accepted and worked through; with it the client gets a 422.
MAX_BATCH_RECORDING_IDS = 1000


class BatchTagOperation(BaseModel):
    recording_ids: List[str] = Field(max_length=MAX_BATCH_RECORDING_IDS)
    tag_name: str


@router.get("", response_model=List[TagRead])
async def read_tags_root(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retrieve tags (root path).
    """
    return await read_tags(skip=skip, limit=limit, db=db, current_user=current_user)


@router.get("/", response_model=List[TagRead])
async def read_tags(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List all available tags.
    """
    statement = (
        select(Tag).where(Tag.user_id == current_user.id).offset(skip).limit(limit)
    )
    result = await db.execute(statement)
    return result.scalars().all()


@router.post("/", response_model=TagRead)
async def create_tag(
    tag_in: TagCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a new tag, or return the user's tag of that name.
    """
    await require_own_parent(db, Tag, tag_in.parent_id, current_user.id)

    tag, created = await get_or_create_tag(
        db,
        Tag,
        tag_in.name,
        current_user.id,
        color=tag_in.color,
        parent_id=tag_in.parent_id,
    )
    if created:
        await db.commit()
        await db.refresh(tag)
    return tag


@router.patch("/{tag_id}", response_model=TagRead)
async def update_tag(
    tag_id: int,
    tag_update: TagUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update a tag's name and/or color.
    """
    tag = await db.get(Tag, tag_id)
    if not tag or tag.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Tag not found")

    # Use model_fields_set to check for explicit updates, including explicit None
    parent_set = "parent_id" in tag_update.model_fields_set
    if parent_set:
        await require_own_parent(db, Tag, tag_update.parent_id, current_user.id)
        await require_parent_outside_subtree(
            db, Tag, tag_id, tag_update.parent_id, current_user.id
        )

    if tag_update.name is not None:
        if tag_update.name != tag.name:
            await require_unused_name(db, Tag, tag_update.name, current_user.id)
        tag.name = tag_update.name

    if tag_update.color is not None:
        tag.color = tag_update.color

    if parent_set:
        tag.parent_id = tag_update.parent_id

    await commit_tag_name(db, tag)
    await db.refresh(tag)
    return tag


@router.post("/recordings/{recording_id}", response_model=TagRead)
async def add_tag_to_recording(
    recording_id: str,
    tag_in: TagCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Add a tag to a recording. Creates the tag if it doesn't exist.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    tag, created = await get_or_create_tag(
        db, Tag, tag_in.name, current_user.id, color=tag_in.color
    )
    linked = await link_recording_tag(db, recording.id, tag.id)
    if created or linked:
        await db.commit()
    if created:
        await db.refresh(tag)
    return tag


@router.delete("/recordings/{recording_id}/{tag_name}")
async def remove_tag_from_recording(
    recording_id: str,
    tag_name: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Remove a tag from a recording.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    tag = await find_tag_by_name(db, Tag, tag_name, current_user.id)
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")

    # Every matching link, though the unique constraint leaves at most one.
    await db.execute(
        delete(RecordingTag).where(
            RecordingTag.recording_id == recording.id, RecordingTag.tag_id == tag.id
        )
    )
    await db.commit()

    return {"ok": True}


@router.delete("/{tag_id}")
async def delete_tag(
    tag_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Delete a global tag. This will also remove the tag from all recordings.
    """
    tag = await db.get(Tag, tag_id)
    if not tag or tag.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Tag not found")

    await db.execute(delete(RecordingTag).where(RecordingTag.tag_id == tag_id))

    await db.delete(tag)
    await db.commit()

    return {"ok": True}


@router.post("/batch/add")
async def batch_add_tag(
    batch: BatchTagOperation,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Add a tag to multiple recordings. Creates the tag if it doesn't exist.
    """
    tag, created = await get_or_create_tag(db, Tag, batch.tag_name, current_user.id)

    # Iterated rather than expressed as a single set-difference query: batches
    # are capped at MAX_BATCH_RECORDING_IDS, so the clearer loop is worth more
    # than the round trip it would save.
    recordings = await get_recordings_by_public_ids(
        db,
        batch.recording_ids,
        user_id=current_user.id,
    )
    recordings_by_public_id = {
        recording.public_id: recording for recording in recordings
    }

    count = 0
    for recording_public_id in batch.recording_ids:
        recording = recordings_by_public_id.get(recording_public_id)
        if recording is None:
            continue

        if await link_recording_tag(db, recording.id, tag.id):
            count += 1

    await db.commit()
    if created:
        await db.refresh(tag)
    return {"ok": True, "count": count, "tag": tag}


@router.post("/batch/remove")
async def batch_remove_tag(
    batch: BatchTagOperation,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Remove a tag from multiple recordings.
    """
    tag = await find_tag_by_name(db, Tag, batch.tag_name, current_user.id)
    if not tag:
        return {"ok": True, "count": 0}

    recordings = await get_recordings_by_public_ids(
        db,
        batch.recording_ids,
        user_id=current_user.id,
    )
    recording_db_ids = [recording.id for recording in recordings]
    if not recording_db_ids:
        return {"ok": True, "count": 0}

    stmt = select(RecordingTag).where(
        RecordingTag.recording_id.in_(recording_db_ids),
        RecordingTag.tag_id == tag.id,
    )
    result = await db.execute(stmt)
    links = result.scalars().all()

    for link in links:
        await db.delete(link)

    await db.commit()
    return {"ok": True, "count": len(links)}
