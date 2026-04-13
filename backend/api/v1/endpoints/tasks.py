from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.api.deps import get_db, get_current_user
from backend.models.task import UserTask, UserTaskCreate, UserTaskRead, UserTaskUpdate
from backend.models.user import User
from backend.utils.time import utc_now
from backend.utils.timezones import (
    convert_datetime_to_utc_naive,
    get_user_timezone_name,
    utc_naive_to_aware,
)
from backend.utils.user_tasks import normalise_task_title, sort_tasks_for_dashboard

router = APIRouter()


def _normalise_task_title(title: str) -> str:
    try:
        return normalise_task_title(title)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


def _serialise_task(task: UserTask) -> UserTaskRead:
    return UserTaskRead(
        id=task.id,
        created_at=utc_naive_to_aware(task.created_at),
        updated_at=utc_naive_to_aware(task.updated_at),
        title=task.title,
        due_at=utc_naive_to_aware(task.due_at),
        completed_at=utc_naive_to_aware(task.completed_at),
    )


async def _get_owned_task(
    task_id: int,
    *,
    db: AsyncSession,
    current_user: User,
) -> UserTask:
    task = await db.get(UserTask, task_id)
    if not task or task.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("", response_model=List[UserTaskRead])
async def read_tasks_root(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await read_tasks(db=db, current_user=current_user)


@router.get("/", response_model=List[UserTaskRead])
async def read_tasks(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    statement = select(UserTask).where(UserTask.user_id == current_user.id)
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
        due_at=convert_datetime_to_utc_naive(
            task_in.due_at,
            timezone_name=user_timezone,
        ),
        user_id=current_user.id,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
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

    await db.commit()
    await db.refresh(task)
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
