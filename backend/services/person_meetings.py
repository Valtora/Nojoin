"""Aggregate People history without loading transcript text or voiceprints."""

from collections import defaultdict
from collections.abc import Sequence

from sqlalchemy import RowMapping, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import ScalarSelect
from sqlmodel import col, select

from backend.models.recording import Recording
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.speaker_public import (
    PersonMeetingRead,
    PersonMeetingsRead,
    PersonTalkMonthRead,
)

# `links` holds the person's active speakers in meetings the user still owns;
# matching against it keeps several labels for one person from multiplying
# meetings or speech. A meeting with any active line is canonical, even when
# none is this person's, so stale JSON never gives them reassigned speech.
# Transcripts with no lines yet (the demo meeting until its lines are first
# read, a restore still rebuilding) fall back to their JSON segments. Only segments
# with numeric times are summed, so one malformed row counts as no speech
# instead of failing the whole history. Only scalar aggregates leave Postgres,
# even for history beyond the fifty displayed meetings.
_MEETINGS = text("""
    WITH links AS (
        SELECT rs.id, rs.recording_id, rs.diarization_label, rs.local_name, rs.name
        FROM recording_speakers rs
        JOIN recordings r ON r.id = rs.recording_id
        WHERE rs.global_speaker_id = :speaker_id
          AND rs.merged_into_id IS NULL AND rs.speaker_status <> 'merged'
          AND r.user_id = :user_id AND NOT r.is_deleted
    ), meetings AS (
        SELECT r.id, r.public_id, r.name, r.created_at, r.duration_seconds
        FROM recordings r
        WHERE r.id IN (SELECT recording_id FROM links)
    ), canonical AS (
        SELECT u.recording_id,
               SUM(CASE WHEN l.id IS NULL THEN 0
                        ELSE GREATEST(0, u.end_ms - u.start_ms) END) / 1000.0
                   AS talk_time_seconds
        FROM transcript_utterances u
        JOIN meetings m ON m.id = u.recording_id
        LEFT JOIN links l ON l.id = u.recording_speaker_id
        WHERE u.state IN ('provisional', 'stable', 'finalized')
        GROUP BY u.recording_id
    ), legacy AS (
        SELECT t.recording_id,
               SUM(GREATEST(0, (s->>'end')::double precision
                               - (s->>'start')::double precision))
                   AS talk_time_seconds
        FROM transcripts t
        JOIN meetings m ON m.id = t.recording_id
        CROSS JOIN LATERAL jsonb_array_elements(
            CASE WHEN jsonb_typeof(t.segments) = 'array'
                 THEN t.segments ELSE '[]'::jsonb END
        ) AS s
        WHERE t.recording_id NOT IN (SELECT recording_id FROM canonical)
          AND NOT (
              COALESCE(t.settling_state->>'eligible', 'false') = 'true'
              AND COALESCE(t.settling_state->'policy'->>'enabled', 'false') = 'true'
          )
          AND jsonb_typeof(s->'start') = 'number'
          AND jsonb_typeof(s->'end') = 'number'
          AND EXISTS (
              SELECT 1 FROM links l
              WHERE l.recording_id = t.recording_id
                AND (s->>'recording_speaker_id' = l.id::text
                     OR (s->>'recording_speaker_id' IS NULL AND s->>'speaker' IN (
                         l.diarization_label, l.local_name, l.name, :person_name
                     )))
          )
        GROUP BY t.recording_id
    )
    SELECT m.public_id AS recording_id, m.name AS title, m.created_at AS date,
           m.duration_seconds,
           COALESCE(c.talk_time_seconds, legacy.talk_time_seconds, 0)
               AS talk_time_seconds
    FROM meetings m
    LEFT JOIN canonical c ON c.recording_id = m.id
    LEFT JOIN legacy ON legacy.recording_id = m.id
    ORDER BY m.created_at DESC, m.id DESC
""")


def person_meeting_count(user_id: int | None) -> ScalarSelect[int]:
    """Use the same distinct, active owned-meeting scope on the People list."""
    return (
        select(func.count(col(RecordingSpeaker.recording_id).distinct()))
        .join(Recording, col(Recording.id) == col(RecordingSpeaker.recording_id))
        .where(
            RecordingSpeaker.global_speaker_id == GlobalSpeaker.id,
            Recording.user_id == user_id,
            col(Recording.is_deleted).is_(False),
            col(RecordingSpeaker.merged_into_id).is_(None),
            RecordingSpeaker.speaker_status != "merged",
        )
        .correlate(GlobalSpeaker)
        .scalar_subquery()
    )


def _summarise(rows: Sequence[RowMapping]) -> PersonMeetingsRead:
    """Keep totals over all meetings, capping only the visible history."""
    monthly: dict[str, float] = defaultdict(float)
    for row in rows:
        monthly[row["date"].strftime("%Y-%m")] += float(row["talk_time_seconds"])
    return PersonMeetingsRead(
        meetings=[PersonMeetingRead.model_validate(row) for row in rows[:50]],
        meeting_count=len(rows),
        total_talk_time_seconds=sum(monthly.values()),
        first_seen=rows[-1]["date"] if rows else None,
        last_seen=rows[0]["date"] if rows else None,
        talk_time_by_month=[
            PersonTalkMonthRead(month=month, talk_time_seconds=monthly[month])
            for month in sorted(monthly, reverse=True)
        ],
    )


async def person_meetings(
    db: AsyncSession, *, speaker_id: int, user_id: int, person_name: str
) -> PersonMeetingsRead:
    """Read distinct owned, non-deleted meetings and current attributed speech."""
    result = await db.execute(
        _MEETINGS,
        {"speaker_id": speaker_id, "user_id": user_id, "person_name": person_name},
    )
    return _summarise(result.mappings().all())
