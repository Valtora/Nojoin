from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from backend.utils.user_tasks import normalise_task_title, sort_tasks_for_dashboard


def make_task(
    *,
    task_id: int,
    title: str,
    created_at: datetime,
    due_at: datetime | None = None,
    completed_at: datetime | None = None,
):
    return SimpleNamespace(
        id=task_id,
        title=title,
        due_at=due_at,
        completed_at=completed_at,
        created_at=created_at,
    )


def test_normalise_task_title_trims_whitespace():
    assert normalise_task_title("  Follow up with product  ") == "Follow up with product"


def test_normalise_task_title_rejects_blank_values():
    with pytest.raises(ValueError) as exc_info:
        normalise_task_title("   ")

    assert str(exc_info.value) == "Task title cannot be empty"


def test_sort_user_tasks_orders_open_then_completed():
    now = datetime(2026, 4, 11, 9, 0, 0)
    tasks = [
        make_task(
            task_id=1,
            title="No due date",
            created_at=now - timedelta(hours=1),
        ),
        make_task(
            task_id=2,
            title="Due later",
            created_at=now - timedelta(hours=2),
            due_at=datetime(2026, 4, 12, 15, 0, 0),
        ),
        make_task(
            task_id=3,
            title="Due first",
            created_at=now - timedelta(hours=3),
            due_at=datetime(2026, 4, 12, 9, 0, 0),
        ),
        make_task(
            task_id=4,
            title="Completed",
            created_at=now - timedelta(days=1),
            completed_at=now - timedelta(minutes=10),
        ),
    ]

    sorted_tasks = sort_tasks_for_dashboard(tasks)

    assert [task.id for task in sorted_tasks] == [3, 2, 1, 4]
