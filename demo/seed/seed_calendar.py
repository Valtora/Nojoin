#!/usr/bin/env python3
"""Give the demo's seed instance a calendar that fits its library.

The demo library is a space reporter's: six NASA briefings and news
conferences. A calendar has no API to write through, because real calendars
arrive by Google or Outlook sync, so this writes a synthetic connection
directly, the way ``scripts/seed_demo_instance.py`` does for the site's
screenshots. Each recording gets the diary entry it was recorded from, and the
weeks around them get the reporter's own meetings. None of the future entries
claims anything about NASA's schedule.

Run inside the seed instance's API container after the recordings have
processed::

    docker compose cp seed_calendar.py api:/app/scripts/
    docker compose exec api python scripts/seed_calendar.py

It replaces its own connection on each run and touches nothing else.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from sqlmodel import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core.db import async_session_maker  # noqa: E402
from backend.models.calendar import (  # noqa: E402
    CalendarConnection,
    CalendarEvent,
    CalendarSource,
)
from backend.models.recording import Recording  # noqa: E402
from backend.models.user import User  # noqa: E402
from backend.utils.time import utc_now  # noqa: E402

ACCOUNT_ID = "demo-seed-calendar"
USERNAME = "demo"

# title, days from this week's Monday, hour, minute, minutes (0 = all day), calendar
DIARY: tuple[tuple[str, int, int, int, int, str], ...] = (
    ("Space desk planning", -14, 10, 0, 30, "Space desk"),
    ("Space desk planning", -7, 10, 0, 30, "Space desk"),
    ("Edit review: asteroid exercise explainer", -6, 14, 0, 45, "Reporting"),
    ("Space desk planning", 0, 10, 0, 30, "Space desk"),
    ("Edit review: Artemis II splashdown piece", 1, 14, 0, 45, "Reporting"),
    ("Interview prep: Crew-9 follow-up", 2, 11, 0, 30, "Reporting"),
    ("Filing deadline: Artemis campaign analysis", 3, 0, 0, 0, "Space desk"),
    ("Space desk planning", 7, 10, 0, 30, "Space desk"),
    ("Editorial board", 8, 15, 0, 60, "Space desk"),
)

CALENDARS = (("Reporting", "blue", True), ("Space desk", "emerald", False))


async def main() -> int:
    async with async_session_maker() as session:
        user = (
            await session.exec(select(User).where(User.username == USERNAME))
        ).first()
        if user is None:
            print(f"No user named {USERNAME!r}.")
            return 1

        old = (
            await session.exec(
                select(CalendarConnection).where(
                    CalendarConnection.provider_account_id == ACCOUNT_ID
                )
            )
        ).first()
        if old is not None:
            await session.delete(old)
            await session.commit()

        now = utc_now()
        connection = CalendarConnection(
            user_id=user.id,
            provider="google",
            provider_account_id=ACCOUNT_ID,
            email="space.desk@example.com",
            display_name="Space desk",
            sync_status="idle",
            last_synced_at=now,
            last_sync_completed_at=now,
        )
        session.add(connection)
        await session.commit()

        sources: dict[str, CalendarSource] = {}
        for name, colour, primary in CALENDARS:
            source = CalendarSource(
                connection_id=connection.id,
                provider_calendar_id=f"{ACCOUNT_ID}:{name}",
                name=name,
                colour=colour,
                is_primary=primary,
                is_selected=True,
                time_zone="UTC",
                last_synced_at=now,
            )
            session.add(source)
            sources[name] = source
        await session.commit()

        def add_event(
            key: str, title: str, starts_at: datetime, minutes: int, calendar: str
        ) -> CalendarEvent:
            # Zero minutes is the all-day shape: a date rather than times.
            all_day = minutes == 0
            day = starts_at.date()
            event = CalendarEvent(
                calendar_id=sources[calendar].id,
                provider_event_id=f"{ACCOUNT_ID}:{key}",
                title=title,
                status="confirmed",
                is_all_day=all_day,
                starts_at=None if all_day else starts_at,
                ends_at=None if all_day else starts_at + timedelta(minutes=minutes),
                start_date=day if all_day else None,
                end_date=day if all_day else None,
                external_updated_at=now,
            )
            session.add(event)
            return event

        # The entry each recording was made from, at the time it was recorded.
        recordings = (
            await session.exec(select(Recording).where(Recording.user_id == user.id))
        ).all()
        linked = 0
        for recording in recordings:
            # An import's recorded_at is stored as created_at.
            started = recording.created_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            minutes = max(15, round((recording.duration_seconds or 0) / 60))
            event = add_event(
                f"rec:{recording.id}", recording.name, started, minutes, "Reporting"
            )
            await session.commit()
            recording.calendar_event_id = event.id
            session.add(recording)
            linked += 1

        monday = now.date() - timedelta(days=now.weekday())
        for index, (title, offset, hour, minute, minutes, calendar) in enumerate(DIARY):
            day = monday + timedelta(days=offset)
            starts_at = datetime.combine(day, time(hour, minute), tzinfo=timezone.utc)
            add_event(f"diary:{index}", title, starts_at, minutes, calendar)
        await session.commit()

    print(f"Calendar seeded: {linked} recordings linked, {len(DIARY)} diary entries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
