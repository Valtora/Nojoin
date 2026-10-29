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

# Canonical presence includes every active line, even when none belongs to this
# person: stale JSON must never give them speech that has been reassigned.
# EXISTS keeps multiple labels linked to the person from multiplying meetings
# or legacy segments. Only scalar aggregates leave Postgres, even for history
# beyond the fifty displayed meetings.
_MEETINGS = text("""
    WITH meetings AS (
        SELECT r.id, r.public_id, r.name, r.created_at, r.duration_seconds
        FROM recordings r
        WHERE r.user_id = :user_id AND NOT r.is_deleted
          AND EXISTS (
              SELECT 1 FROM recording_speakers rs
              WHERE rs.recording_id = r.id
                AND rs.global_speaker_id = :speaker_id
                AND rs.merged_into_id IS NULL AND rs.speaker_status <> 'merged'
          )
    ), canonical AS (
        SELECT u.recording_id,
               SUM(CASE WHEN EXISTS (
                   SELECT 1 FROM recording_speakers rs
                   WHERE rs.recording_id = u.recording_id
                     AND rs.global_speaker_id = :speaker_id
                     AND rs.id = u.recording_speaker_id
                     AND rs.merged_into_id IS NULL AND rs.speaker_status <> 'merged'
               ) THEN GREATEST(0, u.end_ms - u.start_ms) ELSE 0 END) / 1000.0
                   AS talk_time_seconds
        FROM transcript_utterances u
        JOIN meetings m ON m.id = u.recording_id
        WHERE u.state IN ('provisional', 'stable', 'finalized')
        GROUP BY u.recording_id
    )
    SELECT m.public_id AS recording_id, m.name AS title, m.created_at AS date,
           m.duration_seconds,
           COALESCE(c.talk_time_seconds, legacy.talk_time_seconds, 0)
               AS talk_time_seconds
    FROM meetings m
    LEFT JOIN canonical c ON c.recording_id = m.id
    LEFT JOIN transcripts t ON t.recording_id = m.id
    LEFT JOIN LATERAL (
        SELECT CASE WHEN jsonb_typeof(t.segments) = 'string'
                    THEN (t.segments #>> '{}')::jsonb ELSE t.segments END AS segments
    ) projection ON c.recording_id IS NULL
    LEFT JOIN LATERAL (
        SELECT SUM(GREATEST(0,
            (segment->>'end')::double precision -
            (segment->>'start')::double precision)) AS talk_time_seconds
        FROM jsonb_array_elements(
            CASE WHEN jsonb_typeof(projection.segments) = 'array'
                 THEN projection.segments ELSE '[]'::jsonb END
        ) AS segment
        WHERE c.recording_id IS NULL
          AND NOT (
              COALESCE(t.settling_state->>'eligible', 'false') = 'true'
              AND COALESCE(t.settling_state->'policy'->>'enabled', 'false') = 'true'
          )
          AND EXISTS (
              SELECT 1 FROM recording_speakers rs
              WHERE rs.recording_id = m.id AND rs.global_speaker_id = :speaker_id
                AND rs.merged_into_id IS NULL AND rs.speaker_status <> 'merged'
                AND (
                    (segment->>'recording_speaker_id') = rs.id::text
                    OR (segment->>'recording_speaker_id' IS NULL AND
                        segment->>'speaker' IN (
                            rs.diarization_label, rs.local_name, rs.name, :person_name
                        ))
                )
          )
    ) legacy ON TRUE
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
