"""Meeting-edge stage: the refresh_meeting_edge_task and its helpers.

Extracted from backend.worker.tasks.pipeline as a pure decomposition. The shared
surface comes from .constants, so the constants.py shim wrappers keep resolving
the ``*_impl`` functions here via the ``backend.worker.tasks`` package namespace
with no call-site changes.
"""

import logging

from backend.services.timeline_note_service import (
    RecordingTimelineNotes,
    load_recording_timeline_notes,
)
from backend.utils.meeting_edge import normalize_meeting_edge_items
from backend.utils.speaker_names.live import with_live_speaker_names
from backend.utils.speaker_names.live_suggestions import (
    refresh_live_speaker_suggestions,
)
from backend.utils.timeline_notes import (
    iter_timeline,
    render_note_line,
    timeline_notes_signature,
)
from backend.worker.tasks.constants import *  # noqa: F401,F403 -- shared task surface

logger = logging.getLogger(__name__)

_MEETING_EDGE_RUN_STATUSES = frozenset(
    {
        RecordingStatus.UPLOADING,
        RecordingStatus.PAUSED,
        RecordingStatus.QUEUED,
        RecordingStatus.PROCESSING,
    }
)


def _count_meeting_edge_words(segments: Sequence[dict]) -> int:
    total = 0
    for segment in segments:
        total += len(str(segment.get("text", "")).split())
    return total


def _hash_meeting_edge_text(value: str | None) -> str:
    cleaned = (value or "").strip()
    return hashlib.sha1(cleaned.encode("utf-8")).hexdigest()


def _read_meeting_edge_payload_items(payload: dict | None, key: str) -> tuple[str, ...]:
    """The previous refresh's items, as the next prompt shows them.

    Read through the same rule as a fresh response, so an item stored as the
    repr of an object is unwrapped or dropped rather than shown to the model
    as its own earlier suggestion.
    """
    if not isinstance(payload, dict):
        return ()
    value = payload.get(key)
    if not isinstance(value, list):
        return ()
    return normalize_meeting_edge_items(value)


def _parse_meeting_edge_generated_at(payload: dict | None) -> datetime | None:
    if not isinstance(payload, dict):
        return None

    raw_value = payload.get("generated_at")
    if not isinstance(raw_value, str) or not raw_value.strip():
        return None

    try:
        return datetime.fromisoformat(raw_value.replace("Z", "+00:00")).replace(
            tzinfo=None
        )
    except ValueError:
        return None


def _build_recent_meeting_edge_transcript(
    segments: Sequence[dict],
    speaker_map: dict[str, str],
    timeline_notes: RecordingTimelineNotes,
) -> str:
    """The latest lines, each with the body notes written after it.

    A note travels with the line it follows, so the window either shows both
    or neither. Notes before the first line show only once the window reaches
    back to it. Head and tail notes are not here: the prompt's notes section
    carries them on every refresh.
    """
    segment_list = list(segments)
    # Index -1 holds the notes before the first line.
    units: dict[int, list[str]] = {}
    current = -1
    for item in iter_timeline(len(segment_list), timeline_notes.place(segment_list)):
        if isinstance(item, int):
            current = item
            rendered = format_segments_for_llm([segment_list[item]], speaker_map)
        elif item.slot == "body":
            rendered = render_note_line(item.note, timeline_notes.author_names)
        else:
            continue
        if rendered:
            units.setdefault(current, []).append(rendered)

    blocks: list[str] = []
    total_chars = 0
    window_start = max(len(segment_list) - MEETING_EDGE_RECENT_SEGMENTS, 0)
    earliest = len(segment_list)
    for index in range(len(segment_list) - 1, window_start - 1, -1):
        if index not in units:
            continue
        rendered = "\n".join(units[index])
        rendered_length = len(rendered) + 1
        if blocks and total_chars + rendered_length > MEETING_EDGE_MAX_TRANSCRIPT_CHARS:
            break
        blocks.append(rendered)
        total_chars += rendered_length
        earliest = index
    if earliest == 0 and -1 in units:
        blocks.append("\n".join(units[-1]))

    return "\n".join(reversed(blocks)).strip()


def _build_meeting_edge_source_signature(
    *,
    recent_transcript: str,
    focus_text: str | None,
    user_notes: str | None,
    config_signature: str,
    context_level: int | None = None,
) -> str:
    parts = [
        recent_transcript.strip(),
        (focus_text or "").strip(),
        (user_notes or "").strip(),
        config_signature,
    ]
    if context_level is not None:
        parts.append(str(context_level))
    payload = "\n||\n".join(parts)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _has_meeting_edge_signal_impl(
    *,
    segment_count: int,
    word_count: int,
    focus_text: str | None,
) -> bool:
    min_segments = (
        MEETING_EDGE_FOCUSED_MIN_SEGMENTS if focus_text else MEETING_EDGE_MIN_SEGMENTS
    )
    min_words = MEETING_EDGE_FOCUSED_MIN_WORDS if focus_text else MEETING_EDGE_MIN_WORDS
    return word_count >= min_words or (
        segment_count >= min_segments and word_count >= max(18, min_words // 2)
    )


def _should_refresh_meeting_edge_impl(
    *,
    transcript: Transcript,
    source_signature: str,
    current_segment_count: int,
    current_word_count: int,
    focus_text: str | None,
    notes_signature: str,
    context_level: int | None = None,
) -> bool:
    if (
        transcript.meeting_edge_source_signature == source_signature
        and transcript.meeting_edge_status
        in {
            MEETING_EDGE_STATUS_READY,
            MEETING_EDGE_STATUS_UPDATING,
            MEETING_EDGE_STATUS_ERROR,
        }
    ):
        return False

    previous_payload = (
        transcript.meeting_edge_payload
        if isinstance(transcript.meeting_edge_payload, dict)
        else {}
    )
    previous_generated_at = _parse_meeting_edge_generated_at(previous_payload)
    previous_segment_count = int(previous_payload.get("source_segment_count") or 0)
    previous_word_count = int(previous_payload.get("source_word_count") or 0)
    focus_changed = previous_payload.get("focus_hash") != _hash_meeting_edge_text(
        focus_text
    )
    # Any note added, deleted or changed in what the prompts show of it
    # refreshes at once, wherever it sits: the user asked for it to be taken
    # into account. A revision that changed nothing (a write token's fence)
    # leaves the signature as it was.
    notes_changed = previous_payload.get("notes_hash") != _hash_meeting_edge_text(
        notes_signature
    )
    context_level_changed = (
        context_level is not None
        and previous_payload.get("context_level") is not None
        and previous_payload.get("context_level") != context_level
    )

    if (
        focus_changed
        or notes_changed
        or context_level_changed
        or not previous_generated_at
    ):
        return True

    elapsed_seconds = max((utc_now() - previous_generated_at).total_seconds(), 0.0)
    new_segment_count = max(current_segment_count - previous_segment_count, 0)
    new_word_count = max(current_word_count - previous_word_count, 0)

    if elapsed_seconds < MEETING_EDGE_MIN_REFRESH_SECONDS:
        return False

    return (
        new_segment_count >= MEETING_EDGE_MIN_NEW_SEGMENTS
        or new_word_count >= MEETING_EDGE_MIN_NEW_WORDS
    )


def _set_meeting_edge_state(
    session,
    transcript: Transcript,
    *,
    status: str,
    error_message: str | None = None,
    source_signature: str | None = None,
    payload: dict | None = None,
) -> None:
    transcript.meeting_edge_status = status
    transcript.meeting_edge_error_message = error_message
    if source_signature is not None:
        transcript.meeting_edge_source_signature = source_signature
    if payload is not None:
        transcript.meeting_edge_payload = payload
        flag_modified(transcript, "meeting_edge_payload")
    session.add(transcript)
    session.commit()


def _meeting_edge_run_key(recording: Recording | None) -> tuple[bool, int] | None:
    """Whether a run started during capture, and which tab held the capture."""
    if recording is None or recording.status not in _MEETING_EDGE_RUN_STATUSES:
        return None
    live = recording.status in {RecordingStatus.UPLOADING, RecordingStatus.PAUSED}
    return live, recording.capture_epoch


def _finish_meeting_edge_run(
    session,
    transcript: Transcript,
    run_key: tuple[bool, int] | None,
    **state,
) -> bool:
    """Store a run's outcome unless Stop or a capture takeover came first.

    The model call runs for up to a minute outside the transcript lock. An
    outcome that arrives after the capture it described has ended only retires
    the run's "updating" state, so the finished recording keeps its last guidance.
    """
    recording = session.get(Recording, transcript.recording_id)
    if run_key is not None and _meeting_edge_run_key(recording) == run_key:
        _set_meeting_edge_state(session, transcript, **state)
        return True
    _set_meeting_edge_state(
        session,
        transcript,
        status=(
            MEETING_EDGE_STATUS_READY
            if transcript.meeting_edge_payload
            else MEETING_EDGE_STATUS_IDLE
        ),
    )
    return False


@celery_app.task(
    name="backend.worker.tasks.refresh_meeting_edge_task", base=DatabaseTask, bind=True
)
def refresh_meeting_edge_task(self, recording_id: int):
    """Refresh Meeting Edge for one recording, one run at a time.

    A refresh is queued as the transcript grows, so during a live meeting a
    trigger arrives every few seconds while a run takes 20-45 seconds. The
    existing checks cannot stop them overlapping: the staleness check reads a
    timestamp only written when a run *finishes*, and the signature check only
    suppresses an input identical to one already in flight, which a live
    transcript never is. Unguarded, several runs overlap on one recording, each
    spawning its own LLM subprocess and all but the last immediately superseded.

    Dropping the surplus rather than queueing it is deliberate. Another trigger
    is seconds away and will carry fresher transcript than the one skipped, so
    waiting would spend an LLM call to produce a staler answer.
    """
    try:
        refresh_live_speaker_suggestions(self.session, recording_id)
    except Exception:
        self.session.rollback()
        logger.exception(
            "Live speaker suggestions failed for recording %s", recording_id
        )

    with single_flight(
        f"meeting-edge:{recording_id}",
        ttl_seconds=MEETING_EDGE_SINGLE_FLIGHT_TTL_SECONDS,
    ) as acquired:
        if not acquired:
            record_pipeline_metric(
                stage="meeting_edge_refresh_skipped",
                recording_id=recording_id,
                payload={"reason": "already_running"},
                log=logger,
            )
            return None

        return _refresh_meeting_edge(self, recording_id)


def _refresh_meeting_edge(self, recording_id: int):
    session = self.session
    run_key = None

    try:
        recording = session.get(Recording, recording_id)
        if not recording:
            return None

        if recording.status not in {
            RecordingStatus.UPLOADING,
            RecordingStatus.QUEUED,
            RecordingStatus.PROCESSING,
        }:
            return None
        run_key = _meeting_edge_run_key(recording)

        transcript = session.exec(
            select(Transcript)
            .where(Transcript.recording_id == recording_id)
            .with_for_update()
        ).first()
        if transcript is None:
            return None

        user_settings = {}
        if recording.user_id:
            user = session.get(User, recording.user_id)
            if user and user.settings:
                user_settings = user.settings

        if not is_meeting_edge_enabled(user_settings):
            if (
                transcript.meeting_edge_status != MEETING_EDGE_STATUS_IDLE
                or transcript.meeting_edge_error_message
            ):
                _set_meeting_edge_state(
                    session,
                    transcript,
                    status=MEETING_EDGE_STATUS_IDLE,
                    error_message=None,
                )
            return None

        segments = [
            dict(segment)
            for segment in build_transcript_segments_for_read(
                session,
                recording_id,
                transcript=transcript,
            )
            if str(segment.get("text", "")).strip()
        ]
        focus_text = transcript.meeting_edge_focus
        timeline_notes = load_recording_timeline_notes(session, recording_id)
        user_notes = timeline_notes.user_notes_context()
        notes_signature = timeline_notes_signature(timeline_notes.notes)

        if not segments:
            if transcript.meeting_edge_status != MEETING_EDGE_STATUS_IDLE:
                _set_meeting_edge_state(
                    session,
                    transcript,
                    status=MEETING_EDGE_STATUS_IDLE,
                    error_message=None,
                )
            return None

        segment_count = len(segments)
        word_count = _count_meeting_edge_words(segments)
        if not _has_meeting_edge_signal(
            segment_count=segment_count,
            word_count=word_count,
            focus_text=focus_text,
        ):
            if transcript.meeting_edge_status not in {
                MEETING_EDGE_STATUS_IDLE,
                MEETING_EDGE_STATUS_READY,
            }:
                _set_meeting_edge_state(
                    session,
                    transcript,
                    status=MEETING_EDGE_STATUS_IDLE,
                    error_message=None,
                )
            return None

        llm_config = resolve_llm_config(
            session,
            user_settings,
            purpose=LLM_PURPOSE_MEETING_EDGE,
            user_id=recording.user_id,
        )
        # The glossary sits in the prompt, so a change to it must invalidate the
        # refresh signature; otherwise an edit mid-meeting would not take effect
        # until the transcript moved on by itself.
        glossary = resolve_glossary(llm_config.merged_config)
        config_signature = ":".join(
            [
                llm_config.provider,
                llm_config.model or "",
                llm_config.api_url or "",
                hashlib.sha1(glossary.encode("utf-8")).hexdigest() if glossary else "",
            ]
        )

        speakers = session.exec(
            select(RecordingSpeaker).where(
                RecordingSpeaker.recording_id == recording_id
            )
        ).all()
        speaker_map = with_live_speaker_names(
            session, recording_id, build_recording_speaker_map(speakers)
        )
        recent_transcript = _build_recent_meeting_edge_transcript(
            segments, speaker_map, timeline_notes
        )
        context_level = get_meeting_edge_context_level(user_settings)
        source_signature = _build_meeting_edge_source_signature(
            recent_transcript=recent_transcript,
            focus_text=focus_text,
            user_notes=user_notes,
            config_signature=config_signature,
            context_level=context_level,
        )

        if not _should_refresh_meeting_edge(
            transcript=transcript,
            source_signature=source_signature,
            current_segment_count=segment_count,
            current_word_count=word_count,
            focus_text=focus_text,
            notes_signature=notes_signature,
            context_level=context_level,
        ):
            return None

        missing_llm_config = llm_config.missing_configuration_message()
        if missing_llm_config:
            _set_meeting_edge_state(
                session,
                transcript,
                status=MEETING_EDGE_STATUS_ERROR,
                error_message=missing_llm_config,
                source_signature=source_signature,
            )
            return None

        previous_payload = (
            transcript.meeting_edge_payload
            if isinstance(transcript.meeting_edge_payload, dict)
            else {}
        )
        request = MeetingEdgeRequest(
            recent_transcript=recent_transcript,
            rolling_summary=(
                (previous_payload or {}).get("rolling_summary")
                or (previous_payload or {}).get("summary")
            ),
            focus_text=focus_text,
            user_notes=user_notes,
            meeting_context=_resolve_meeting_event_context(session, recording),
            context_level=context_level,
            glossary=glossary or None,
            previous_questions=_read_meeting_edge_payload_items(
                previous_payload, "questions"
            ),
            previous_points=_read_meeting_edge_payload_items(
                previous_payload, "points"
            ),
        )

        _set_meeting_edge_state(
            session,
            transcript,
            status=MEETING_EDGE_STATUS_UPDATING,
            error_message=None,
            source_signature=source_signature,
        )

        llm = _llm_backend_from_config(llm_config)
        result = llm.generate_meeting_edge(
            request,
            timeout=MEETING_EDGE_TIMEOUT_SECONDS,
        )
        payload = serialize_meeting_edge_result(result)
        payload.update(
            {
                "generated_at": utc_now().isoformat(),
                "source_segment_count": segment_count,
                "source_word_count": word_count,
                "source_last_end": float(segments[-1].get("end", 0.0)),
                "focus_hash": _hash_meeting_edge_text(focus_text),
                "notes_hash": _hash_meeting_edge_text(notes_signature),
                "context_level": context_level,
            }
        )
        previous_context_level_value = previous_payload.get(
            "context_level",
            MEETING_EDGE_CONTEXT_LEVEL_MAX if previous_payload else None,
        )
        try:
            previous_context_level = int(previous_context_level_value)
        except (TypeError, ValueError):
            previous_context_level = (
                MEETING_EDGE_CONTEXT_LEVEL_MAX if previous_payload else None
            )
        payload["concept_history"] = merge_meeting_edge_concept_history(
            previous_payload,
            payload,
            reset_history=previous_context_level is not None
            and previous_context_level > context_level,
        )
        if not _finish_meeting_edge_run(
            session,
            transcript,
            run_key,
            status=MEETING_EDGE_STATUS_READY,
            error_message=None,
            source_signature=source_signature,
            payload=payload,
        ):
            return None
        return payload
    except Exception as exc:
        logger.error(
            "Meeting Edge refresh failed for recording %s: %s",
            recording_id,
            exc,
            exc_info=True,
        )

        transcript = session.exec(
            select(Transcript).where(Transcript.recording_id == recording_id)
        ).first()
        if transcript is not None:
            _finish_meeting_edge_run(
                session,
                transcript,
                run_key,
                status=MEETING_EDGE_STATUS_ERROR,
                error_message=str(exc).strip()[:500]
                or "Meeting Edge could not be updated.",
            )
        return None


__all__ = [name for name in globals() if not name.startswith("__")]
