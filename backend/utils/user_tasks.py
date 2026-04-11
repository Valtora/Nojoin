from datetime import date, datetime
from typing import Sequence, TypeVar

TaskItem = TypeVar("TaskItem")


def normalise_task_title(title: str) -> str:
    normalised = title.strip()
    if not normalised:
        raise ValueError("Task title cannot be empty")
    return normalised


def sort_tasks_for_dashboard(tasks: Sequence[TaskItem]) -> list[TaskItem]:
    active_tasks = [task for task in tasks if getattr(task, "completed_at", None) is None]
    completed_tasks = [task for task in tasks if getattr(task, "completed_at", None) is not None]

    active_tasks.sort(key=lambda task: getattr(task, "created_at"), reverse=True)
    active_tasks.sort(key=lambda task: getattr(task, "due_on") or date.max)
    active_tasks.sort(key=lambda task: getattr(task, "due_on") is None)

    completed_tasks.sort(
        key=lambda task: getattr(task, "completed_at") or datetime.min,
        reverse=True,
    )

    return [*active_tasks, *completed_tasks]
