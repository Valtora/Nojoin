-- Screen study (#653): the speech timeline of each study meeting.
--
-- A single read-only SELECT. It reads no transcript text, no speaker or
-- person names and no recording titles. One JSON object per recording
-- tagged study-teams, study-zoom or study-meet:
--
--   recording       the recording's public id, which names its frame folder
--   platform        teams, zoom or meet, from the tag
--   platform_order  1 for the platform's first study meeting by creation
--                   time (its calibration meeting), then 2, 3, ...
--   chunks          [sequence_no, absolute_start_ms] for every audio chunk
--   utterances      [start_ms, end_ms, diarization, pipeline, reviewed,
--                    overlap_rank] for every current line, where
--                     diarization is the diariser's cluster for the line,
--                     pipeline is the speaker label the pipeline gave it at
--                       its latest finalize, from transcript_utterance_events,
--                     both as numbers that stand for the label within this
--                       recording, so no label text leaves the database,
--                     reviewed is the person the line belongs to after review:
--                       the People id when the meeting speaker is linked to
--                       one, else minus the meeting speaker's id.
--
-- Run it on live with the transaction forced read-only:
--
--   docker exec -i -e PGOPTIONS='-c default_transaction_read_only=on' nojoin-db \
--     psql -X -q -At -v ON_ERROR_STOP=1 -U postgres -d nojoin \
--     < scripts/speaker_frames/export_timeline.sql > timeline.jsonl

WITH study AS (
    SELECT
        r.id,
        r.public_id,
        r.created_at,
        CASE t.name
            WHEN 'study-teams' THEN 'teams'
            WHEN 'study-zoom' THEN 'zoom'
            WHEN 'study-meet' THEN 'meet'
        END AS platform
    FROM recordings AS r
    JOIN recording_tags AS rt ON rt.recording_id = r.id
    JOIN tags AS t ON t.id = rt.tag_id
    WHERE t.name IN ('study-teams', 'study-zoom', 'study-meet')
      AND NOT r.is_deleted
),
current_lines AS (
    SELECT u.*
    FROM transcript_utterances AS u
    JOIN study AS s ON s.id = u.recording_id
    WHERE u.state NOT IN ('superseded', 'deleted')
),
finalized AS (
    SELECT DISTINCT ON (e.utterance_id)
        e.utterance_id,
        e.new_values ->> 'speaker' AS label
    FROM transcript_utterance_events AS e
    JOIN current_lines AS u ON u.id = e.utterance_id
    WHERE e.event_type = 'finalize'
    ORDER BY e.utterance_id, e.id DESC
),
labels AS (
    SELECT
        recording_id,
        label,
        dense_rank() OVER (PARTITION BY recording_id ORDER BY label) AS label_key
    FROM (
        SELECT u.recording_id, u.diarization_label AS label
        FROM current_lines AS u
        WHERE u.diarization_label IS NOT NULL
        UNION
        SELECT u.recording_id, f.label
        FROM finalized AS f
        JOIN current_lines AS u ON u.id = f.utterance_id
        WHERE f.label IS NOT NULL
    ) AS used
),
lines AS (
    SELECT
        u.recording_id,
        u.id,
        u.start_ms,
        u.end_ms,
        dl.label_key AS diarization,
        pl.label_key AS pipeline,
        COALESCE(rs.global_speaker_id, -rs.id) AS reviewed,
        COALESCE(u.overlap_rank, 0) AS overlap_rank
    FROM current_lines AS u
    LEFT JOIN labels AS dl
        ON dl.recording_id = u.recording_id AND dl.label = u.diarization_label
    LEFT JOIN finalized AS f ON f.utterance_id = u.id
    LEFT JOIN labels AS pl
        ON pl.recording_id = u.recording_id AND pl.label = f.label
    LEFT JOIN LATERAL (
        SELECT s.id, s.global_speaker_id
        FROM recording_speakers AS s
        WHERE s.recording_id = u.recording_id
          AND (
              s.id = u.recording_speaker_id
              OR (u.recording_speaker_id IS NULL AND s.diarization_label = u.speaker_label)
          )
        ORDER BY (s.id = u.recording_speaker_id) DESC, s.id
        LIMIT 1
    ) AS rs ON true
)
SELECT json_build_object(
    'recording', s.public_id,
    'platform', s.platform,
    'platform_order', row_number() OVER (PARTITION BY s.platform ORDER BY s.created_at, s.id),
    'chunks', (
        SELECT COALESCE(
            json_agg(json_build_array(c.sequence_no, c.absolute_start_ms) ORDER BY c.sequence_no),
            '[]'::json
        )
        FROM recording_audio_chunks AS c
        WHERE c.recording_id = s.id
    ),
    'utterances', (
        SELECT COALESCE(
            json_agg(
                json_build_array(l.start_ms, l.end_ms, l.diarization, l.pipeline, l.reviewed, l.overlap_rank)
                ORDER BY l.start_ms, l.id
            ),
            '[]'::json
        )
        FROM lines AS l
        WHERE l.recording_id = s.id
    )
)
FROM study AS s
ORDER BY s.platform, s.created_at, s.id;
