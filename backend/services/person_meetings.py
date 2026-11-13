"""Aggregate People history without loading transcript text or voiceprints."""

from collections import defaultdict
from collections.abc import Sequence

from sqlalchemy import RowMapping, Select, and_, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Subquery
from sqlmodel import col, select

from backend.models.recording import Recording
from backend.models.speaker import RecordingSpeaker
from backend.models.speaker_public import (
    PersonMeetingRead,
    PersonMeetingsRead,
    PersonTalkMonthRead,
)
from backend.models.transcript import Transcript
from backend.utils.timezones import utc_naive_to_timezone

# A meeting counts for a person only through a speaker that is neither merged
# away nor inactive (every line reassigned to someone else). The status is an
# allow-list, so a status added later counts nowhere until someone decides it
# should. Every reader of a person's meetings shares it: the history and the
# People list count here, the recordings list's person filter, the MCP
# get_person tool, the voiceprint sample lines and the delivery baselines
# through counted_speaker().
COUNTED_SPEAKER_STATUS = "active"


def counted_speaker() -> ColumnElement[bool]:
    """A ``RecordingSpeaker`` row through which its meeting counts for its person.

    The caller joins the recording and decides which recordings it reads; the
    raw-SQL history query applies the same two tests to ``rs``.
    """
    return and_(
        col(RecordingSpeaker.merged_into_id).is_(None),
        col(RecordingSpeaker.speaker_status) == COUNTED_SPEAKER_STATUS,
    )


def person_sample_sources(person_id: int, user_id: int, scan: int) -> Select:
    """The person's counted speakers in the user's newest ``scan`` meetings.

    Each row is the speaker, its recording and the recording's transcript, or
    ``None`` where it has none yet. The voiceprint sample lines read these, so a
    speaker merged away, or one whose lines all moved to someone else, offers
    no line, and nor does a recording in the bin or one the user does not own.
    """
    return (
        select(RecordingSpeaker, Recording, Transcript)
        .join(Recording, col(Recording.id) == col(RecordingSpeaker.recording_id))
        .outerjoin(
            Transcript,
            col(Transcript.recording_id) == col(RecordingSpeaker.recording_id),
        )
        .where(col(RecordingSpeaker.global_speaker_id) == person_id)
        .where(col(Recording.user_id) == user_id)
        .where(col(Recording.is_deleted).is_(False))
        .where(counted_speaker())
        .order_by(col(Recording.created_at).desc())
        .limit(scan)
    )


# `links` holds the person's active speakers in meetings the user still owns;
# matching against it keeps several labels for one person from multiplying
# meetings or speech. A meeting with any active line is canonical, even when
# none is this person's, so stale JSON never gives them reassigned speech.
# Transcripts with no lines yet (the demo meeting until its lines are first
# read, a restore still rebuilding) fall back to their JSON segments. A speaker
# only becomes inactive while its meeting has lines, and keeps that status when
# the lines go (a restore, every line deleted), so its JSON speech stays out as
# the lines last had it; new lines set the status afresh. Only segments
# with numeric times are summed, so one malformed row counts as no speech
# instead of failing the whole history. Only scalar aggregates leave Postgres,
# even for history beyond the fifty displayed meetings.
_MEETINGS = text("""
    WITH links AS (
        SELECT rs.id, rs.recording_id, rs.diarization_label, rs.local_name, rs.name
        FROM recording_speakers rs
        JOIN recordings r ON r.id = rs.recording_id
        WHERE rs.global_speaker_id = :speaker_id
          AND rs.merged_into_id IS NULL AND rs.speaker_status = :counted_status
          AND r.user_id = :user_id AND NOT r.is_deleted
    ), meetings AS (
        SELECT r.id, r.public_id, r.name, r.created_at, r.duration_seconds
        FROM recordings r
        WHERE r.id IN (SELECT recording_id FROM links)
    ), canonical AS (
        SELECT m.id AS recording_id,
               COALESCE(SUM(GREATEST(0, u.end_ms - u.start_ms)), 0) / 1000.0
                   AS talk_time_seconds
        FROM meetings m
        JOIN links l ON l.recording_id = m.id
        LEFT JOIN transcript_utterances u ON u.recording_speaker_id = l.id
             AND u.state IN ('provisional', 'stable', 'finalized')
        WHERE EXISTS (
            SELECT 1 FROM transcript_utterances a
            WHERE a.recording_id = m.id
              AND a.state IN ('provisional', 'stable', 'finalized'))
        GROUP BY m.id
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
""").bindparams(counted_status=COUNTED_SPEAKER_STATUS)


def person_meeting_counts(user_id: int | None) -> Subquery:
    """Each person's distinct, active owned meetings, counted in one pass.

    The People list joins this once; a subquery per person rescans
    recording_speakers for every row of the page.
    """
    return (
        select(
            col(RecordingSpeaker.global_speaker_id).label("global_speaker_id"),
            func.count(col(RecordingSpeaker.recording_id).distinct()).label(
                "meeting_count"
            ),
        )
        .join(Recording, col(Recording.id) == col(RecordingSpeaker.recording_id))
        .where(
            Recording.user_id == user_id,
            col(Recording.is_deleted).is_(False),
            counted_speaker(),
        )
        .group_by(col(RecordingSpeaker.global_speaker_id))
        .subquery()
    )


def _summarise(rows: Sequence[RowMapping], timezone_name: str) -> PersonMeetingsRead:
    """Keep totals over all meetings, capping only the visible history.

    Months follow the user's time-zone setting, as every shown date does.
    """
    monthly: dict[str, float] = defaultdict(float)
    for row in rows:
        local = utc_naive_to_timezone(row["date"], timezone_name)
        assert local is not None  # recordings.created_at is NOT NULL
        monthly[local.strftime("%Y-%m")] += float(row["talk_time_seconds"])
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
    db: AsyncSession,
    *,
    speaker_id: int,
    user_id: int,
    person_name: str,
    timezone_name: str,
) -> PersonMeetingsRead:
    """Read distinct owned, non-deleted meetings and current attributed speech."""
    result = await db.execute(
        _MEETINGS,
        {"speaker_id": speaker_id, "user_id": user_id, "person_name": person_name},
    )
    return _summarise(result.mappings().all(), timezone_name)
