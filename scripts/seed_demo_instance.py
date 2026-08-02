#!/usr/bin/env python3
"""Populate a demonstration Nojoin instance with enough density to photograph.

The marketing site needs screenshots of a Nojoin install that looks lived in
rather than freshly booted. This script supplies that surrounding density --
people, tags, tasks, calendar events and a recordings list -- so the only
recording that has to be processed for real is the one the screenshots
actually open.

Deliberately not the same thing as ``backend/seed_demo.py``. That module seeds
the single "Welcome to Nojoin" recording every new install gets, and it runs
inside the application. This is an operator tool for a private demonstration
stack, and it is never invoked by the running app.

What it does NOT do, by design:

* No GPU work and no transcription. A demonstration stack shares its hardware
  with whatever else is on the host.
* No invented transcripts. Seeded recordings are list-view fixtures with no
  ``Transcript`` row, because writing plausible dialogue and presenting it as
  pipeline output would be a lie told in a screenshot. The recordings that get
  opened on camera are imported and processed normally.
* No audio synthesis beyond silence. Each seeded recording gets a silent WAV of
  the right length so ``audio_path`` points at a real file of honest duration.

Run it inside the API container, which already has the application on its path
and its database environment configured. ``/app`` is baked into the image
rather than mounted, so copy it in first::

    docker compose -p <project> cp scripts/seed_demo_instance.py api:/app/scripts/
    docker compose -p <project> exec api python scripts/seed_demo_instance.py

It refuses to run twice. ``--reset`` deletes exactly the rows this script
created -- matched by name, and by an audio path under its own directory -- and
then reseeds. Nothing else in the database is touched, so a real recording that
cost GPU time to process is never at risk.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
import wave
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlmodel import select

# Running a file puts that file's directory on sys.path, not the working
# directory, so ``python scripts/seed_demo_instance.py`` cannot see the
# application package without help. Adding the repository root keeps the
# documented command working from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core.db import async_session_maker  # noqa: E402
from backend.models.calendar import (  # noqa: E402
    CalendarConnection,
    CalendarEvent,
    CalendarSource,
)
from backend.models.people_tag import PeopleTag, PeopleTagLink  # noqa: E402
from backend.models.recording import Recording, RecordingStatus  # noqa: E402
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker  # noqa: E402
from backend.models.tag import RecordingTag, Tag  # noqa: E402
from backend.models.task import UserTask, UserTaskRecording, UserTaskTag  # noqa: E402
from backend.models.user import User  # noqa: E402
from backend.utils.path_manager import PathManager  # noqa: E402
from backend.utils.time import utc_now  # noqa: E402

# Everything this script creates lives under a directory of its own, which is
# what makes --reset able to identify its own recordings without guessing.
SEED_DIRNAME = "seed-demo"

# The synthetic calendar account. Its provider_account_id is the marker used to
# find and remove the connection, and cascades take the sources and events.
SEED_CALENDAR_ACCOUNT_ID = "seed-demo-calendar"

# Silence at 8 kHz, 8-bit mono is 8 KB per second, so an hour-long meeting costs
# about 29 MB. Cheap enough to make the file's real length match the duration
# the interface displays, which a 30-second placeholder would not.
WAV_FRAME_RATE = 8000
WAV_SAMPLE_WIDTH = 1
# Silence in unsigned 8-bit PCM is the midpoint, not zero. Zero is full-scale
# negative: a click on playback and a waveform that is not flat.
WAV_SILENCE_BYTE = b"\x80"


@dataclass(frozen=True)
class Person:
    name: str
    title: str
    colour: str
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Meeting:
    """A seeded recording. ``days_ago`` places it relative to the run date."""

    name: str
    days_ago: int
    # Time of day the meeting started. Without it every seeded recording
    # inherits the moment the script ran, and a library where each meeting
    # begins at the same odd minute reads as generated.
    hour: int
    minute: int
    minutes: int
    tags: tuple[str, ...]
    speakers: tuple[str, ...]


@dataclass(frozen=True)
class Task:
    title: str
    body: str | None = None
    due_in_days: int | None = None
    completed: bool = False
    tags: tuple[str, ...] = ()
    meetings: tuple[str, ...] = ()


@dataclass(frozen=True)
class Event:
    """A calendar entry, placed relative to Monday of the current week."""

    title: str
    weekday_offset: int
    hour: int
    minute: int
    minutes: int
    calendar: str
    location: str | None = None
    attendees: tuple[str, ...] = ()


# People carry a role but no employer. Every plausible company name belongs to
# a real business somewhere, and a screenshot is a poor place to discover that.
PEOPLE: tuple[Person, ...] = (
    Person("James Smith", "Managing Director", "blue", ("Leadership",)),
    Person(
        "Alice Brennan", "Operations Manager", "emerald", ("Leadership", "Operations")
    ),
    Person("Tom Okafor", "Finance Lead", "amber", ("Leadership",)),
    Person("Priya Raman", "Marketing Lead", "violet", ("Leadership",)),
    Person("Daniel Fisher", "Sales", "teal", ()),
    Person("Sofia Marchetti", "Customer Success", "rose", ()),
    Person("Ravi Shah", "Warehouse Supervisor", "lime", ("Operations",)),
    Person("Ellie Whitcombe", "Office Manager", "cyan", ("Operations",)),
    Person("Marcus Bell", "Accountant", "orange", ("External",)),
    Person("Hannah Ford", "Recruitment Consultant", "pink", ("External",)),
)

PEOPLE_TAGS: tuple[tuple[str, str], ...] = (
    ("Leadership", "indigo"),
    ("Operations", "emerald"),
    ("External", "amber"),
)

RECORDING_TAGS: tuple[tuple[str, str], ...] = (
    ("Board", "indigo"),
    ("Finance", "amber"),
    ("Hiring", "pink"),
    ("Marketing", "violet"),
    ("Operations", "emerald"),
    ("Suppliers", "teal"),
)

# Eight meetings across three weeks. The repeated operations sync is deliberate:
# a real library is mostly recurring meetings, and a list of eight unique titles
# reads as a demo.
MEETINGS: tuple[Meeting, ...] = (
    Meeting(
        "Monthly board update",
        days_ago=19,
        hour=10,
        minute=0,
        minutes=58,
        tags=("Board", "Finance"),
        speakers=("James Smith", "Tom Okafor", "Alice Brennan", "Priya Raman"),
    ),
    Meeting(
        "Supplier review: packaging costs",
        days_ago=17,
        hour=15,
        minute=30,
        minutes=42,
        tags=("Suppliers", "Operations"),
        speakers=("Alice Brennan", "Ravi Shah", "James Smith"),
    ),
    Meeting(
        "Weekly operations sync",
        days_ago=14,
        hour=9,
        minute=30,
        minutes=24,
        tags=("Operations",),
        speakers=("Alice Brennan", "Ravi Shah", "Ellie Whitcombe"),
    ),
    Meeting(
        "Hiring debrief: operations manager",
        days_ago=11,
        hour=11,
        minute=0,
        minutes=31,
        tags=("Hiring",),
        speakers=("James Smith", "Hannah Ford", "Alice Brennan"),
    ),
    Meeting(
        "Autumn campaign planning",
        days_ago=8,
        hour=14,
        minute=0,
        minutes=47,
        tags=("Marketing",),
        speakers=("Priya Raman", "Daniel Fisher", "James Smith"),
    ),
    Meeting(
        "Weekly operations sync",
        days_ago=6,
        hour=9,
        minute=30,
        minutes=26,
        tags=("Operations",),
        speakers=("Alice Brennan", "Ravi Shah", "Ellie Whitcombe"),
    ),
    Meeting(
        "Cashflow review",
        days_ago=3,
        hour=14,
        minute=0,
        minutes=38,
        tags=("Finance",),
        speakers=("Tom Okafor", "Marcus Bell", "James Smith"),
    ),
    Meeting(
        "Customer renewal call",
        days_ago=1,
        hour=11,
        minute=0,
        minutes=26,
        tags=("Operations",),
        speakers=("Sofia Marchetti", "Daniel Fisher"),
    ),
)

TASKS: tuple[Task, ...] = (
    Task(
        "Circulate the board pack before Friday",
        body="Include the revised cashflow forecast and the packaging quotes.",
        due_in_days=2,
        tags=("Board",),
        meetings=("Monthly board update",),
    ),
    Task(
        "Get three packaging quotes",
        due_in_days=5,
        tags=("Suppliers",),
        meetings=("Supplier review: packaging costs",),
    ),
    Task(
        "Draft the operations manager offer",
        completed=True,
        tags=("Hiring",),
        meetings=("Hiring debrief: operations manager",),
    ),
    Task(
        "Book the autumn campaign photography",
        due_in_days=9,
        tags=("Marketing",),
        meetings=("Autumn campaign planning",),
    ),
    Task(
        "Chase the overdue invoice from March",
        body="Second reminder. Escalate to Tom if there's no reply by Friday.",
        due_in_days=-1,
        tags=("Finance",),
        meetings=("Cashflow review",),
    ),
    Task(
        "Send the renewal paperwork",
        due_in_days=1,
        meetings=("Customer renewal call",),
    ),
    Task(
        "Update the stock reorder thresholds",
        completed=True,
        tags=("Operations",),
    ),
    Task(
        "Confirm the accountant's year-end dates",
        due_in_days=14,
        tags=("Finance",),
    ),
)

CALENDARS: tuple[tuple[str, str, bool], ...] = (
    # name, colour, is_primary
    ("James Smith", "blue", True),
    ("Team diary", "emerald", False),
)

# Placed relative to Monday of the current week, so the dashboard always has a
# populated week whenever the screenshots are retaken.
EVENTS: tuple[Event, ...] = (
    # Previous week.
    Event("Weekly operations sync", -7, 9, 30, 30, "Team diary", "Meeting room"),
    Event("Cashflow review", -5, 14, 0, 45, "James Smith", "Video call"),
    Event("Customer renewal call", -3, 11, 0, 30, "James Smith", "Video call"),
    # The week in view.
    Event("Weekly operations sync", 0, 9, 30, 30, "Team diary", "Meeting room"),
    Event("Warehouse walkthrough", 0, 13, 0, 60, "Team diary", "Unit 4"),
    Event(
        "Monthly board update",
        1,
        10,
        0,
        90,
        "James Smith",
        "Boardroom",
        ("Tom Okafor", "Alice Brennan", "Priya Raman"),
    ),
    Event("One to one: Alice", 1, 15, 0, 30, "James Smith"),
    Event("Autumn campaign review", 2, 11, 0, 45, "Team diary", "Video call"),
    Event("Supplier call: packaging", 2, 15, 30, 30, "James Smith", "Video call"),
    Event("Year-end planning with Marcus", 3, 10, 0, 60, "James Smith", "Video call"),
    Event("Sales pipeline review", 3, 14, 30, 45, "Team diary"),
    Event("Office closed: bank holiday", 4, 9, 0, 0, "Team diary"),
    # Following week.
    Event("Weekly operations sync", 7, 9, 30, 30, "Team diary", "Meeting room"),
    Event(
        "Interview: warehouse assistant", 8, 11, 0, 45, "James Smith", "Meeting room"
    ),
    Event("Quarterly stock count", 9, 9, 0, 180, "Team diary", "Unit 4"),
)

# Recordings that correspond to a diary entry, so the dashboard can show a
# meeting next to the recording of it.
MEETING_EVENT_LINKS: dict[str, tuple[str, int]] = {
    # recording name -> (event title, event weekday_offset)
    "Cashflow review": ("Cashflow review", -5),
    "Customer renewal call": ("Customer renewal call", -3),
}


@dataclass
class Created:
    """Counts for the run summary."""

    counts: dict[str, int] = field(default_factory=dict)

    def add(self, kind: str, n: int = 1) -> None:
        self.counts[kind] = self.counts.get(kind, 0) + n

    def report(self) -> str:
        return ", ".join(f"{n} {kind}" for kind, n in self.counts.items())


@dataclass
class Run:
    """Everything a seeding step needs, so no step takes six parameters."""

    session: Any
    user: User
    now: datetime
    created: Created


def seed_directory() -> Path:
    return PathManager().recordings_directory / SEED_DIRNAME


def write_silent_wav(path: Path, seconds: int) -> int:
    """Write a silent WAV of the given length and return its size in bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(WAV_SAMPLE_WIDTH)
        handle.setframerate(WAV_FRAME_RATE)
        handle.writeframes(WAV_SILENCE_BYTE * (WAV_FRAME_RATE * seconds))
    return path.stat().st_size


def anchor_monday(today: date) -> date:
    """Monday of the working week the calendar is showing.

    Run at a weekend, "this week" is the one that has just finished, which
    would leave every seeded event in the past and the agenda empty. At a
    weekend the week in view is the one about to start, so anchor forwards.
    """
    weekday = today.weekday()
    if weekday >= 5:
        return today + timedelta(days=7 - weekday)
    return today - timedelta(days=weekday)


async def resolve_user(session, username: str | None) -> User:
    if username:
        statement = select(User).where(User.username == username)
    else:
        statement = select(User).order_by(User.id).limit(1)
    result = await session.execute(statement)
    user = result.scalars().first()
    if user is None:
        raise SystemExit(
            f"No user found{f' named {username}' if username else ''}. "
            "Complete the first-run wizard before seeding."
        )
    return user


async def already_seeded(session, user: User) -> bool:
    marker = f"{SEED_DIRNAME}/"
    statement = select(Recording).where(
        Recording.user_id == user.id,
        Recording.audio_path.contains(marker),  # type: ignore[attr-defined]
    )
    result = await session.execute(statement)
    return result.scalars().first() is not None


async def reset(session, user: User) -> None:
    """Delete only what this script creates, leaving everything else alone."""
    marker = f"{SEED_DIRNAME}/"

    recordings = (
        (
            await session.execute(
                select(Recording).where(
                    Recording.user_id == user.id,
                    Recording.audio_path.contains(marker),  # type: ignore[attr-defined]
                )
            )
        )
        .scalars()
        .all()
    )
    for recording in recordings:
        await session.delete(recording)

    task_titles = [task.title for task in TASKS]
    tasks = (
        (
            await session.execute(
                select(UserTask).where(
                    UserTask.user_id == user.id,
                    UserTask.title.in_(task_titles),  # type: ignore[attr-defined]
                )
            )
        )
        .scalars()
        .all()
    )
    for task in tasks:
        await session.delete(task)

    connections = (
        (
            await session.execute(
                select(CalendarConnection).where(
                    CalendarConnection.user_id == user.id,
                    CalendarConnection.provider_account_id == SEED_CALENDAR_ACCOUNT_ID,
                )
            )
        )
        .scalars()
        .all()
    )
    for connection in connections:
        await session.delete(connection)

    speaker_names = [person.name for person in PEOPLE]
    speakers = (
        (
            await session.execute(
                select(GlobalSpeaker).where(
                    GlobalSpeaker.user_id == user.id,
                    GlobalSpeaker.name.in_(speaker_names),  # type: ignore[attr-defined]
                )
            )
        )
        .scalars()
        .all()
    )
    for speaker in speakers:
        await session.delete(speaker)

    for model, names in (
        (Tag, [name for name, _ in RECORDING_TAGS]),
        (PeopleTag, [name for name, _ in PEOPLE_TAGS]),
    ):
        rows = (
            (
                await session.execute(
                    select(model).where(
                        model.user_id == user.id,
                        model.name.in_(names),  # type: ignore[attr-defined]
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            await session.delete(row)

    await session.commit()
    shutil.rmtree(seed_directory(), ignore_errors=True)


def meeting_key(meeting: Meeting) -> str:
    """Meeting names repeat, so the key carries the date that separates them."""
    return f"{meeting.name}-{meeting.days_ago}"


async def seed_tags(run: Run) -> tuple[dict[str, PeopleTag], dict[str, Tag]]:
    people_tags: dict[str, PeopleTag] = {}
    for name, colour in PEOPLE_TAGS:
        people_tag = PeopleTag(name=name, color=colour, user_id=run.user.id)
        run.session.add(people_tag)
        people_tags[name] = people_tag

    tags: dict[str, Tag] = {}
    for name, colour in RECORDING_TAGS:
        tag = Tag(name=name, color=colour, user_id=run.user.id)
        run.session.add(tag)
        tags[name] = tag

    await run.session.commit()
    run.created.add("tags", len(tags) + len(people_tags))
    return people_tags, tags


async def seed_people(
    run: Run, people_tags: dict[str, PeopleTag]
) -> dict[str, GlobalSpeaker]:
    speakers: dict[str, GlobalSpeaker] = {}
    for person in PEOPLE:
        speaker = GlobalSpeaker(
            name=person.name,
            title=person.title,
            color=person.colour,
            user_id=run.user.id,
        )
        run.session.add(speaker)
        speakers[person.name] = speaker
    await run.session.commit()

    for person in PEOPLE:
        for tag_name in person.tags:
            run.session.add(
                PeopleTagLink(
                    global_speaker_id=speakers[person.name].id,
                    tag_id=people_tags[tag_name].id,
                )
            )
    await run.session.commit()
    run.created.add("people", len(speakers))
    return speakers


async def seed_recordings(
    run: Run, tags: dict[str, Tag], speakers: dict[str, GlobalSpeaker]
) -> dict[str, Recording]:
    audio_dir = seed_directory()
    recordings: dict[str, Recording] = {}

    for meeting in MEETINGS:
        seconds = meeting.minutes * 60
        slug = meeting.name.lower().replace(" ", "-").replace(":", "")
        path = audio_dir / f"{slug}-{meeting.days_ago}d.wav"
        size = write_silent_wav(path, seconds)

        recorded_at = (run.now - timedelta(days=meeting.days_ago)).replace(
            hour=meeting.hour, minute=meeting.minute, second=0, microsecond=0
        )
        recording = Recording(
            name=meeting.name,
            audio_path=str(path),
            duration_seconds=float(seconds),
            file_size_bytes=size,
            status=RecordingStatus.PROCESSED,
            user_id=run.user.id,
            created_at=recorded_at,
            updated_at=recorded_at,
            processing_started_at=recorded_at,
            processing_completed_at=recorded_at + timedelta(minutes=4),
            last_activity_at=recorded_at,
            processing_progress=100,
        )
        run.session.add(recording)
        recordings[meeting_key(meeting)] = recording
    await run.session.commit()
    run.created.add("recordings", len(recordings))

    for meeting in MEETINGS:
        recording = recordings[meeting_key(meeting)]
        for tag_name in meeting.tags:
            run.session.add(
                RecordingTag(recording_id=recording.id, tag_id=tags[tag_name].id)
            )
        for index, speaker_name in enumerate(meeting.speakers):
            run.session.add(
                RecordingSpeaker(
                    recording_id=recording.id,
                    global_speaker_id=speakers[speaker_name].id,
                    diarization_label=f"SPEAKER_{index:02d}",
                    local_name=speaker_name,
                    color=speakers[speaker_name].color,
                )
            )
    await run.session.commit()
    return recordings


async def seed_calendar(run: Run, recordings: dict[str, Recording]) -> None:
    connection = CalendarConnection(
        user_id=run.user.id,
        provider="google",
        provider_account_id=SEED_CALENDAR_ACCOUNT_ID,
        email="james.smith@example.com",
        display_name="James Smith",
        sync_status="idle",
        last_synced_at=run.now,
        last_sync_completed_at=run.now,
    )
    run.session.add(connection)
    await run.session.commit()

    sources: dict[str, CalendarSource] = {}
    for name, colour, is_primary in CALENDARS:
        source = CalendarSource(
            connection_id=connection.id,
            provider_calendar_id=f"{SEED_CALENDAR_ACCOUNT_ID}:{name}",
            name=name,
            colour=colour,
            is_primary=is_primary,
            is_selected=True,
            time_zone="Europe/London",
            last_synced_at=run.now,
        )
        run.session.add(source)
        sources[name] = source
    await run.session.commit()

    monday = anchor_monday(run.now.date())
    events: dict[tuple[str, int], CalendarEvent] = {}
    for entry in EVENTS:
        day = monday + timedelta(days=entry.weekday_offset)
        starts_at = datetime.combine(day, datetime.min.time()).replace(
            hour=entry.hour, minute=entry.minute
        )
        # A zero-length entry is the all-day shape: dates rather than times.
        is_all_day = entry.minutes == 0
        event = CalendarEvent(
            calendar_id=sources[entry.calendar].id,
            provider_event_id=(
                f"{SEED_CALENDAR_ACCOUNT_ID}:{entry.title}:{entry.weekday_offset}"
            ),
            title=entry.title,
            status="confirmed",
            is_all_day=is_all_day,
            starts_at=None if is_all_day else starts_at,
            ends_at=(
                None if is_all_day else starts_at + timedelta(minutes=entry.minutes)
            ),
            start_date=day if is_all_day else None,
            end_date=day if is_all_day else None,
            location_text=entry.location,
            attendees=[{"name": name} for name in entry.attendees] or None,
            external_updated_at=run.now,
        )
        run.session.add(event)
        events[(entry.title, entry.weekday_offset)] = event
    await run.session.commit()
    run.created.add("calendar events", len(events))

    for recording_name, key in MEETING_EVENT_LINKS.items():
        event = events.get(key)
        recording = find_recording(recordings, recording_name)
        if event is None or recording is None:
            continue
        recording.calendar_event_id = event.id
        run.session.add(recording)
    await run.session.commit()


def find_recording(recordings: dict[str, Recording], name: str) -> Recording | None:
    for key, recording in recordings.items():
        if key.startswith(f"{name}-"):
            return recording
    return None


async def seed_tasks(
    run: Run, tags: dict[str, Tag], recordings: dict[str, Recording]
) -> None:
    for spec in TASKS:
        task = UserTask(
            title=spec.title,
            body=spec.body,
            user_id=run.user.id,
            due_at=(
                None
                if spec.due_in_days is None
                else run.now + timedelta(days=spec.due_in_days)
            ),
            completed_at=run.now - timedelta(days=1) if spec.completed else None,
        )
        run.session.add(task)
        await run.session.commit()

        for tag_name in spec.tags:
            run.session.add(UserTaskTag(task_id=task.id, tag_id=tags[tag_name].id))
        for meeting_name in spec.meetings:
            recording = find_recording(recordings, meeting_name)
            if recording is not None:
                run.session.add(
                    UserTaskRecording(task_id=task.id, recording_id=recording.id)
                )
    await run.session.commit()
    run.created.add("tasks", len(TASKS))


async def seed(session, user: User, created: Created) -> None:
    run = Run(session=session, user=user, now=utc_now(), created=created)
    people_tags, tags = await seed_tags(run)
    speakers = await seed_people(run, people_tags)
    recordings = await seed_recordings(run, tags, speakers)
    await seed_calendar(run, recordings)
    await seed_tasks(run, tags, recordings)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete this script's own rows and files, then seed again.",
    )
    parser.add_argument(
        "--user",
        help="Username to own the seeded data. Defaults to the first user.",
    )
    args = parser.parse_args()

    async with async_session_maker() as session:
        user = await resolve_user(session, args.user)

        if await already_seeded(session, user):
            if not args.reset:
                print(
                    "This instance is already seeded. Re-run with --reset to "
                    "replace the seeded rows, which leaves every other "
                    "recording untouched.",
                    file=sys.stderr,
                )
                return 1
            print("Removing previously seeded data...")
            await reset(session, user)
        elif args.reset:
            await reset(session, user)

        created = Created()
        await seed(session, user, created)
        print(f"Seeded {user.username}: {created.report()}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
