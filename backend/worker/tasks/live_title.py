"""Generate one delayed title/gist from committed live text on the IO lane."""

import logging

from celery import Task
from sqlmodel import Session, select

from backend.celery_app import celery_app
from backend.core.single_flight import single_flight
from backend.models.recording import Recording, RecordingStatus, is_live_capture
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.utils.canonical_pipeline import build_transcript_segments_for_read
from backend.utils.languages import (
    apply_recording_language,
    resolve_language_preferences,
)
from backend.utils.llm_config import resolve_llm_config
from backend.utils.meeting_brief import (
    build_meeting_brief_prompt,
    get_live_title_delay_minutes,
    parse_meeting_brief_response,
)
from backend.utils.meeting_notes import format_segments_for_llm
from backend.utils.transcript_change import change_sequence_bump
from backend.worker.tasks.constants import DatabaseTask, _llm_backend_from_config

logger = logging.getLogger(__name__)
LIVE_TITLE_TIMEOUT_SECONDS = 120
LIVE_TITLE_SINGLE_FLIGHT_TTL_SECONDS = 300


def _eligible_for_live_brief(recording: Recording) -> bool:
    return (
        recording.status in {RecordingStatus.UPLOADING, RecordingStatus.PAUSED}
        and is_live_capture(recording)
        and not recording.is_deleted
        and recording.gist is None
    )


def _publish_live_title(
    session: Session,
    recording_id: int,
    *,
    capture_epoch: int,
    title: str,
    gist: str,
) -> bool:
    """Discard work superseded by Stop, takeover or an already-published gist."""
    transcript = session.exec(
        select(Transcript)
        .where(Transcript.recording_id == recording_id)
        .with_for_update()
    ).first()
    recording = session.get(Recording, recording_id)
    if transcript is None or recording is None:
        return False
    session.refresh(recording, with_for_update=True)
    if (
        not _eligible_for_live_brief(recording)
        or recording.capture_epoch != capture_epoch
    ):
        return False
    if not recording.title_user_edited:
        recording.name = title
    recording.gist = gist
    recording.gist_live = True
    session.add(recording)
    session.execute(change_sequence_bump(recording_id))
    session.commit()
    return True


def _generate_live_title(task: Task, recording_id: int) -> None:
    """Run the brief outside database locks; only publication takes row locks."""
    session = task.session
    recording = session.get(Recording, recording_id)
    if recording is None or not _eligible_for_live_brief(recording):
        return
    user = session.get(User, recording.user_id) if recording.user_id else None
    settings = (user.settings if user else None) or {}
    # Live dispatch already waited for the delay; a setting turned off since wins.
    if not get_live_title_delay_minutes(settings):
        return
    segments = [
        segment
        for segment in build_transcript_segments_for_read(session, recording.id)
        if str(segment.get("text", "")).strip()
    ]
    if not segments:
        return
    config = resolve_llm_config(session, settings, user_id=recording.user_id)
    if config.missing_configuration_message():
        return
    language = resolve_language_preferences(
        apply_recording_language(config.merged_config, recording)
    )
    prompt = build_meeting_brief_prompt(
        format_segments_for_llm(segments, {}),
        prefer_short_titles=config.merged_config.get("prefer_short_titles", True),
        output_language_instruction=language.notes_language_instruction,
    )
    epoch = recording.capture_epoch
    session.commit()
    title, gist = parse_meeting_brief_response(
        _llm_backend_from_config(config).generate_text(
            prompt, timeout=LIVE_TITLE_TIMEOUT_SECONDS
        )
    )
    _publish_live_title(
        session, recording_id, capture_epoch=epoch, title=title, gist=gist
    )


@celery_app.task(
    name="backend.worker.tasks.generate_live_title_task", base=DatabaseTask, bind=True
)
def generate_live_title_task(self: Task, recording_id: int) -> None:
    """Drop overlapping triggers; a successful gist also skips future triggers."""
    with single_flight(
        f"live-title:{recording_id}", ttl_seconds=LIVE_TITLE_SINGLE_FLIGHT_TTL_SECONDS
    ) as acquired:
        if not acquired:
            return
        try:
            _generate_live_title(self, recording_id)
        except Exception:
            # Optional intelligence must never fail capture or its final notes.
            self.session.rollback()
            logger.exception("Live meeting brief failed for recording %s", recording_id)
