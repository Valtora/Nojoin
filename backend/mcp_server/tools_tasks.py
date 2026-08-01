"""MCP tools for the user's task workspace.

Tasks are the user's own to-do items, linkable to recordings and tags.
All four tools require mcp:write; deletion stays in the write tier
because archiving is the soft path and a task is a lightweight note, not
recorded meeting data.
"""

import logging
from typing import Any, Literal, Optional

from mcp.server.fastmcp.exceptions import ToolError

from backend.mcp_server.auth import get_current_mcp_user
from backend.mcp_server.server import (
    _parse_iso_datetime,
    _require_write_scope,
    mcp_tool,
)

logger = logging.getLogger(__name__)


def _compact_task(task: Any) -> dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "body": task.body,
        "due_at": task.due_at.isoformat() if task.due_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "archived_at": task.archived_at.isoformat() if task.archived_at else None,
        "tags": [tag.name for tag in task.tags],
        "recordings": [
            {"id": linked.id, "name": linked.name} for linked in task.linked_recordings
        ],
    }


@mcp_tool()
async def list_tasks(
    status: Literal["active", "open", "completed", "archived", "all"] = "active",
) -> list[dict[str, Any]]:
    """List the user's tasks from the Task workspace.

    Args:
        status: Which tasks to return: "active" (open plus recently
            completed, the dashboard view), "open", "completed",
            "archived", or "all".
    """
    from backend.api.v1.endpoints.tasks import read_tasks
    from backend.core.db import async_session_maker

    user = get_current_mcp_user()
    async with async_session_maker() as db:
        tasks = await read_tasks(status=status, db=db, current_user=user)
    return [_compact_task(task) for task in tasks]


@mcp_tool()
async def create_task(
    title: str,
    body: Optional[str] = None,
    due_at: Optional[str] = None,
    tag_ids: Optional[list[int]] = None,
    recording_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Create a task in the user's Task workspace.

    Use this to turn meeting outcomes into actionable items, linking the
    task back to the meetings it came from. Requires the mcp:write scope.

    Args:
        title: The task title.
        body: Optional task detail text.
        due_at: Optional ISO 8601 due date/datetime.
        tag_ids: Optional tag ids (from list_tags) to label the task with.
        recording_ids: Optional recording ids (from list_recordings) to
            link the task to.
    """
    from backend.api.v1.endpoints.tasks import create_task as api_create_task
    from backend.core.db import async_session_maker
    from backend.models.task import UserTaskCreate

    user = get_current_mcp_user()
    _require_write_scope("task creation")
    if not title.strip():
        raise ToolError("title must not be empty.")

    async with async_session_maker() as db:
        task = await api_create_task(
            UserTaskCreate(
                title=title.strip(),
                body=body,
                due_at=_parse_iso_datetime(due_at, "due_at"),
                tag_ids=tag_ids or [],
                recording_ids=recording_ids or [],
            ),
            db=db,
            current_user=user,
        )
    return _compact_task(task)


@mcp_tool()
async def update_task(  # noqa: PLR0913 - each parameter is a documented tool argument
    task_id: int,
    title: Optional[str] = None,
    body: Optional[str] = None,
    due_at: Optional[str] = None,
    completed: Optional[bool] = None,
    archived: Optional[bool] = None,
    tag_ids: Optional[list[int]] = None,
    recording_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Update a task: edit fields, complete, reopen, archive, or restore.

    Only the arguments you pass change; omitted fields are left as they
    are. Passing tag_ids or recording_ids replaces the full set of links.
    Requires the mcp:write scope.

    Args:
        task_id: The task's integer id from list_tasks.
        title: New title.
        body: New detail text.
        due_at: New ISO 8601 due date/datetime.
        completed: True marks the task complete, False reopens it.
        archived: True archives the task, False restores it.
        tag_ids: Replacement tag ids for the task.
        recording_ids: Replacement linked recording ids for the task.
    """
    from backend.api.v1.endpoints.tasks import update_task as api_update_task
    from backend.core.db import async_session_maker
    from backend.models.task import UserTaskUpdate

    user = get_current_mcp_user()
    _require_write_scope("task updates")

    # The PATCH endpoint honours pydantic's fields-set semantics, so only
    # arguments the caller actually supplied may appear in the update.
    changes: dict[str, Any] = {}
    if title is not None:
        changes["title"] = title
    if body is not None:
        changes["body"] = body
    if due_at is not None:
        changes["due_at"] = _parse_iso_datetime(due_at, "due_at")
    if completed is not None:
        changes["completed"] = completed
    if archived is not None:
        changes["archived"] = archived
    if tag_ids is not None:
        changes["tag_ids"] = tag_ids
    if recording_ids is not None:
        changes["recording_ids"] = recording_ids
    if not changes:
        raise ToolError("Nothing to update: pass at least one field.")

    async with async_session_maker() as db:
        task = await api_update_task(
            task_id,
            UserTaskUpdate(**changes),
            db=db,
            current_user=user,
        )
    return _compact_task(task)


@mcp_tool()
async def delete_task(task_id: int) -> dict[str, Any]:
    """Delete a task from the Task workspace.

    Deletion is immediate and not recoverable; use update_task with
    archived=true when the task should merely leave the active list.
    Requires the mcp:write scope.

    Args:
        task_id: The task's integer id from list_tasks.
    """
    from backend.api.v1.endpoints.tasks import delete_task as api_delete_task
    from backend.core.db import async_session_maker

    user = get_current_mcp_user()
    _require_write_scope("task deletion")
    async with async_session_maker() as db:
        await api_delete_task(task_id, db=db, current_user=user)
    return {"id": task_id, "deleted": True}
