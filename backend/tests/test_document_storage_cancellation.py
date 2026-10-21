"""A cancelled document-file delete still removes the files.

The unlink runs in a worker thread. A cancellation that lands while the job is
still queued, behind a busy thread pool, would drop the job, and the files of
rows that have already been deleted would stay with nothing referencing them.
Each test runs on its own event loop with one thread in each pool, held busy,
so the job is queued when the cancellation lands.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path

import anyio
import anyio.to_thread
import pytest

from backend.utils.document_storage import delete_document_files

logger = logging.getLogger(__name__)


def _run_with_busy_thread_pools(
    scenario: Callable[[], Awaitable[None]],
) -> None:
    async def main() -> None:
        loop = asyncio.get_running_loop()
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        loop.set_default_executor(executor)
        limiter = anyio.to_thread.current_default_thread_limiter()
        limiter.total_tokens = 1
        release = threading.Event()
        busy = [
            loop.run_in_executor(None, release.wait),
            asyncio.ensure_future(anyio.to_thread.run_sync(release.wait)),
        ]
        while limiter.borrowed_tokens < 1:
            await asyncio.sleep(0)
        try:
            await scenario()
        finally:
            release.set()
            await asyncio.gather(*busy)

    asyncio.run(main())


async def _until_queued(task: asyncio.Task) -> None:
    """Let the delete run up to its wait for a thread."""
    limiter = anyio.to_thread.current_default_thread_limiter()
    for _ in range(100):
        if limiter.statistics().tasks_waiting:
            return
        await asyncio.sleep(0)
    assert not task.done()


@pytest.fixture
def document(tmp_path, monkeypatch) -> Path:
    documents_dir = tmp_path / "documents"
    documents_dir.mkdir()
    monkeypatch.setenv("DOCUMENTS_DIR", str(documents_dir))
    path = documents_dir / "agenda.md"
    path.write_text("# Agenda")
    return path


def test_a_cancelled_task_still_removes_the_files(document):
    async def scenario() -> None:
        task = asyncio.create_task(
            delete_document_files([str(document)], logger=logger)
        )
        await _until_queued(task)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not document.exists()

    _run_with_busy_thread_pools(scenario)


def test_a_cancelled_scope_still_removes_the_files(document):
    """The cancellation Starlette and anyio deliver, through a cancel scope."""

    async def scenario() -> None:
        scopes: list[anyio.CancelScope] = []

        async def delete_in_scope() -> None:
            with anyio.CancelScope() as scope:
                scopes.append(scope)
                await delete_document_files([str(document)], logger=logger)

        task = asyncio.create_task(delete_in_scope())
        await _until_queued(task)
        scopes[0].cancel()
        await task
        assert scopes[0].cancelled_caught
        assert not document.exists()

    _run_with_busy_thread_pools(scenario)
