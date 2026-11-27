"""Cooperative notes cancellation at provider request boundaries."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar


class NotesRunCancelled(Exception):
    """The notes run no longer owns its claim; stop without reporting failure."""


_check: ContextVar[Callable[[], None] | None] = ContextVar(
    "notes_run_check", default=None
)


@contextmanager
def checking_notes_run(check: Callable[[], None]) -> Iterator[None]:
    """Check ownership before each model request made within this context."""
    token = _check.set(check)
    try:
        yield
    finally:
        _check.reset(token)


def check_notes_run() -> None:
    """Raise cancellation when the current notes run has lost ownership."""
    check = _check.get()
    if check is not None:
        check()
