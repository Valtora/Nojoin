"""Public People meeting history and full-history statistics."""

from datetime import datetime

from pydantic import BaseModel


class PersonMeetingRead(BaseModel):
    recording_id: str
    title: str
    date: datetime
    duration_seconds: float | None
    talk_time_seconds: float


class PersonTalkMonthRead(BaseModel):
    month: str
    talk_time_seconds: float


class PersonMeetingsRead(BaseModel):
    meetings: list[PersonMeetingRead]
    meeting_count: int
    total_talk_time_seconds: float
    first_seen: datetime | None
    last_seen: datetime | None
    talk_time_by_month: list[PersonTalkMonthRead]
