"""Which step a meeting's notes run is on, for the page to show while it waits.

The notes of a meeting too long for one model call take a call for each part
and more to combine them (``backend.utils.long_meeting_notes``), which can run
for many minutes. The worker sets a reporter for the length of the model call
(``backend.worker.tasks.notes_progress``), and the parts run reports each step
through it before the call that step makes.

The reporter is context-local rather than an argument because the calls pass
through every LLM backend's interface and the secondary-provider wrapper, and
only the parts run has steps to report. The reporter must not raise for a step
it could not record: progress is never worth failing the notes for.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

NotesProgressReporter = Callable[[str], None]

_reporter: ContextVar[NotesProgressReporter | None] = ContextVar(
    "notes_progress_reporter", default=None
)


@contextmanager
def reporting_notes_progress(report: NotesProgressReporter) -> Iterator[None]:
    """Send the steps reported inside the block to ``report``."""
    token = _reporter.set(report)
    try:
        yield
    finally:
        _reporter.reset(token)


def report_notes_progress(step: str) -> None:
    """Report ``step`` to the run's reporter, if it has one."""
    report = _reporter.get()
    if report is not None:
        report(step)
