from typing import List, Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from backend.api.deps import get_current_user, get_db
from backend.models.tag import Tag, TagRead
from backend.models.task import (
    UserTask,
    UserTaskCreate,
    UserTaskRead,
    UserTaskRecording,
    UserTaskRecordingRead,
    UserTaskTag,
    UserTaskUpdate,
)
from backend.models.user import User
from backend.services.recording_identity_service import get_recordings_by_public_ids
from backend.utils.time import utc_now
from backend.utils.timezones import (
    convert_datetime_to_utc_naive,
    get_user_timezone_name,
    utc_naive_to_aware,
)
from backend.utils.user_tasks import normalise_task_title, sort_tasks_for_dashboard

router = APIRouter()
TaskStatusFilter = Literal["active", "open", "completed", "archived", "all"]


def _normalise_task_title(title: str) -> str:
    try:
        return normalise_task_title(title)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid task title.") from error


def _serialise_task(task: UserTask) -> UserTaskRead:
    tags: list[TagRead] = []
    for link in task.tag_links:
        if link.tag:
            tags.append(TagRead.model_validate(link.tag))

    linked_recordings: list[UserTaskRecordingRead] = []
    for link in task.recording_links:
        recording = link.recording
        if recording:
            linked_recordings.append(
                UserTaskRecordingRead(
                    id=recording.public_id,
                    name=recording.name,
                    created_at=utc_naive_to_aware(recording.created_at),
                    duration_seconds=recording.duration_seconds,
                    status=recording.status,
                    is_archived=recording.is_archived,
                    is_deleted=recording.is_deleted,
                )
            )

    return UserTaskRead(
        id=task.id,
        created_at=utc_naive_to_aware(task.created_at),
        updated_at=utc_naive_to_aware(task.updated_at),
        title=task.title,
        body=task.body,
        due_at=utc_naive_to_aware(task.due_at),
        completed_at=utc_naive_to_aware(task.completed_at),
        archived_at=utc_naive_to_aware(task.archived_at),
        tags=tags,
        linked_recordings=linked_recordings,
    )


async def _get_owned_task(
    task_id: int,
    *,
    db: AsyncSession,
    current_user: User,
) -> UserTask:
    result = await db.execute(
        select(UserTask)
        .where(UserTask.id == task_id)
        .options(
            selectinload(UserTask.tag_links).selectinload(UserTaskTag.tag),
            selectinload(UserTask.recording_links).selectinload(
                UserTaskRecording.recording
            ),
        )
        .execution_options(populate_existing=True)
    )
    task = result.scalar_one_or_none()
    if not task or task.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


async def _replace_task_tags(
    task: UserTask,
    tag_ids: list[int],
    *,
    db: AsyncSession,
    current_user: User,
) -> None:
    unique_tag_ids = list(dict.fromkeys(tag_ids))
    if not unique_tag_ids:
        for link in list(task.tag_links):
            await db.delete(link)
        task.tag_links = []
        return

    result = await db.execute(
        select(Tag).where(Tag.id.in_(unique_tag_ids), Tag.user_id == current_user.id)
    )
    owned_tags = result.scalars().all()
    owned_tag_ids = {tag.id for tag in owned_tags}
    missing_tag_ids = [
        tag_id for tag_id in unique_tag_ids if tag_id not in owned_tag_ids
    ]
    if missing_tag_ids:
        raise HTTPException(status_code=404, detail="Task tag not found")

    existing_links_by_tag_id = {link.tag_id: link for link in task.tag_links}
    for link in list(task.tag_links):
        if link.tag_id not in owned_tag_ids:
            await db.delete(link)

    for tag_id in unique_tag_ids:
        if tag_id not in existing_links_by_tag_id:
            db.add(UserTaskTag(task_id=task.id, tag_id=tag_id))


async def _replace_task_recordings(
    task: UserTask,
    recording_public_ids: list[str],
    *,
    db: AsyncSession,
    current_user: User,
) -> None:
    unique_public_ids = list(dict.fromkeys(recording_public_ids))
    if not unique_public_ids:
        for link in list(task.recording_links):
            await db.delete(link)
        task.recording_links = []
        return

    recordings = await get_recordings_by_public_ids(
        db,
        unique_public_ids,
        user_id=current_user.id,
    )
    recordings_by_public_id = {
        recording.public_id: recording for recording in recordings
    }
    missing_public_ids = [
        public_id
        for public_id in unique_public_ids
        if public_id not in recordings_by_public_id
    ]
    if missing_public_ids:
        raise HTTPException(status_code=404, detail="Task recording not found")

    recording_ids = {recording.id for recording in recordings}
    existing_links_by_recording_id = {
        link.recording_id: link for link in task.recording_links
    }
    for link in list(task.recording_links):
        if link.recording_id not in recording_ids:
            await db.delete(link)

    for recording in recordings:
        if recording.id not in existing_links_by_recording_id:
            db.add(UserTaskRecording(task_id=task.id, recording_id=recording.id))


@router.get("", response_model=List[UserTaskRead])
async def read_tasks_root(
    status: TaskStatusFilter = "active",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await read_tasks(status=status, db=db, current_user=current_user)


@router.get("/", response_model=List[UserTaskRead])
async def read_tasks(
    status: TaskStatusFilter = "active",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    statement = (
        select(UserTask)
        .where(UserTask.user_id == current_user.id)
        .options(
            selectinload(UserTask.tag_links).selectinload(UserTaskTag.tag),
            selectinload(UserTask.recording_links).selectinload(
                UserTaskRecording.recording
            ),
        )
    )

    if status == "active":
        statement = statement.where(UserTask.archived_at.is_(None))
    elif status == "open":
        statement = statement.where(
            UserTask.archived_at.is_(None),
            UserTask.completed_at.is_(None),
        )
    elif status == "completed":
        statement = statement.where(
            UserTask.archived_at.is_(None),
            UserTask.completed_at.is_not(None),
        )
    elif status == "archived":
        statement = statement.where(UserTask.archived_at.is_not(None))

    result = await db.execute(statement)
    tasks = result.scalars().all()
    return [_serialise_task(task) for task in sort_tasks_for_dashboard(list(tasks))]


@router.post("/", response_model=UserTaskRead)
async def create_task(
    task_in: UserTaskCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user_timezone = get_user_timezone_name(current_user.settings or {})
    task = UserTask(
        title=_normalise_task_title(task_in.title),
        body=task_in.body.strip() if task_in.body else None,
        due_at=convert_datetime_to_utc_naive(
            task_in.due_at,
            timezone_name=user_timezone,
        ),
        user_id=current_user.id,
    )
    db.add(task)
    await db.flush()
    await db.refresh(task, attribute_names=["tag_links", "recording_links"])
    await _replace_task_tags(task, task_in.tag_ids, db=db, current_user=current_user)
    await _replace_task_recordings(
        task,
        task_in.recording_ids,
        db=db,
        current_user=current_user,
    )
    await db.commit()
    task = await _get_owned_task(task.id, db=db, current_user=current_user)
    return _serialise_task(task)


@router.patch("/{task_id}", response_model=UserTaskRead)
async def update_task(
    task_id: int,
    task_update: UserTaskUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    task = await _get_owned_task(task_id, db=db, current_user=current_user)
    user_timezone = get_user_timezone_name(current_user.settings or {})

    if "title" in task_update.model_fields_set:
        if task_update.title is None:
            raise HTTPException(status_code=400, detail="Task title cannot be empty")
        task.title = _normalise_task_title(task_update.title)

    if "body" in task_update.model_fields_set:
        task.body = task_update.body.strip() if task_update.body else None

    if "due_at" in task_update.model_fields_set:
        task.due_at = convert_datetime_to_utc_naive(
            task_update.due_at,
            timezone_name=user_timezone,
        )

    if "completed" in task_update.model_fields_set:
        if task_update.completed:
            if task.completed_at is None:
                task.completed_at = utc_now()
        else:
            task.completed_at = None

    if "archived" in task_update.model_fields_set:
        if task_update.archived:
            if task.archived_at is None:
                task.archived_at = utc_now()
        else:
            task.archived_at = None

    if "tag_ids" in task_update.model_fields_set:
        await _replace_task_tags(
            task,
            task_update.tag_ids or [],
            db=db,
            current_user=current_user,
        )

    if "recording_ids" in task_update.model_fields_set:
        await _replace_task_recordings(
            task,
            task_update.recording_ids or [],
            db=db,
            current_user=current_user,
        )

    await db.commit()
    task = await _get_owned_task(task.id, db=db, current_user=current_user)
    return _serialise_task(task)


@router.delete("/{task_id}")
async def delete_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    task = await _get_owned_task(task_id, db=db, current_user=current_user)
    await db.delete(task)
    await db.commit()
    return {"ok": True}
