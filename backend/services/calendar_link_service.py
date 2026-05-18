"""Service for linking recordings to overlapping calendar events.

The matching logic (:func:`score_event_match`) is a pure, unit-testable
function. :func:`auto_link_recording` is the conservative worker-side hook
that runs at the PROCESSED transition; it never overwrites an existing link
and never raises out of the worker.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import sqlalchemy as sa
from sqlmodel import select

from backend.models.calendar import CalendarConnection, CalendarEvent, CalendarSource
from backend.models.recording import Recording

logger = logging.getLogger(__name__)

# Minimum overlap fraction (relative to the shorter interval) required before a
# candidate may be auto-linked.
AUTO_LINK_MIN_SCORE = 0.5
# A candidate is only unambiguous when the runner-up either scores below the
# threshold or trails the best score by at least this margin.
AUTO_LINK_AMBIGUITY_MARGIN = 0.2
# How far outside the recording window a candidate event may sit and still be
# offered to the manual-linking candidates endpoint.
CANDIDATE_WINDOW_PADDING = timedelta(hours=2)


def score_event_match(
    rec_start: datetime,
    rec_end: datetime,
    event_start: Optional[datetime],
    event_end: Optional[datetime],
) -> float:
    """Return the temporal overlap fraction of a recording and a calendar event.

    The fraction is relative to the *shorter* of the two intervals, so a short
    recording fully inside a long meeting scores 1.0 and vice versa. Returns
    0.0 when either interval is missing or non-positive (the zero-duration
    event guard).
    """
    if event_start is None or event_end is None:
        return 0.0

    rec_duration = (rec_end - rec_start).total_seconds()
    event_duration = (event_end - event_start).total_seconds()
    if rec_duration <= 0 or event_duration <= 0:
        return 0.0

    overlap_start = max(rec_start, event_start)
    overlap_end = min(rec_end, event_end)
    overlap = (overlap_end - overlap_start).total_seconds()
    if overlap <= 0:
        return 0.0

    shorter = min(rec_duration, event_duration)
    return min(1.0, overlap / shorter)


def _recording_window(recording: Recording) -> Optional[tuple[datetime, datetime]]:
    """Resolve the ``[start, end]`` UTC window for a recording, or None."""
    if not recording.duration_seconds or recording.duration_seconds <= 0:
        return None
    if recording.created_at is None:
        return None
    start = recording.created_at
    return start, start + timedelta(seconds=recording.duration_seconds)


def _selected_timed_events_overlapping(
    session,
    user_id: int,
    window_start: datetime,
    window_end: datetime,
) -> list[CalendarEvent]:
    """Return the user's timed events on selected calendars overlapping a window.

    Every event is owner-scoped by joining
    ``CalendarEvent -> CalendarSource -> CalendarConnection`` to ``user_id``.
    """
    statement = (
        select(CalendarEvent)
        .join(CalendarSource, CalendarEvent.calendar_id == CalendarSource.id)
        .join(CalendarConnection, CalendarSource.connection_id == CalendarConnection.id)
        .where(
            CalendarConnection.user_id == user_id,
            CalendarSource.is_selected.is_(True),
            CalendarEvent.is_all_day.is_(False),
            CalendarEvent.starts_at.is_not(None),
            CalendarEvent.ends_at.is_not(None),
            CalendarEvent.starts_at < window_end,
            CalendarEvent.ends_at > window_start,
        )
    )
    return list(session.exec(statement).all())


def auto_link_recording(session, recording: Recording) -> None:
    """Conservatively auto-link a recording to its calendar event.

    No-op when the recording already has a link (a reprocess must never
    clobber a manual link), when its duration is missing/zero, or when the
    user has no selected calendars. Links only on a single, clear,
    high-confidence match. Never raises out of the worker.
    """
    try:
        if recording.calendar_event_id is not None:
            return
        if recording.user_id is None:
            return

        window = _recording_window(recording)
        if window is None:
            return
        window_start, window_end = window

        events = _selected_timed_events_overlapping(
            session, recording.user_id, window_start, window_end
        )
        if not events:
            return

        scored = sorted(
            (
                (event, score_event_match(window_start, window_end, event.starts_at, event.ends_at))
                for event in events
            ),
            key=lambda pair: pair[1],
            reverse=True,
        )

        best_event, best_score = scored[0]
        if best_score < AUTO_LINK_MIN_SCORE:
            return

        if len(scored) > 1:
            runner_up_score = scored[1][1]
            ambiguous = (
                runner_up_score >= AUTO_LINK_MIN_SCORE
                and (best_score - runner_up_score) < AUTO_LINK_AMBIGUITY_MARGIN
            )
            if ambiguous:
                logger.info(
                    "Recording %s: ambiguous calendar match (%.2f vs %.2f), not auto-linking",
                    recording.id,
                    best_score,
                    runner_up_score,
                )
                return

        recording.calendar_event_id = best_event.id
        session.add(recording)
        logger.info(
            "Recording %s auto-linked to calendar event %s (score %.2f)",
            recording.id,
            best_event.id,
            best_score,
        )
    except Exception:  # noqa: BLE001 - linking must never break the pipeline
        logger.exception("Auto-linking failed for recording %s", getattr(recording, "id", None))
