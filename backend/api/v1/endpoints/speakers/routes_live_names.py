"""Name a live speaker while the meeting is still being recorded.

A live label is no speaker of the recording, so the name is kept as the
user's name event anchored on the label's speech
(``backend/utils/speaker_names/live.py``) and carried to the final speaker
when the recording is processed. A matching owned Person is linked. Explicit
``create_person`` intent can create a Person without learning a live voiceprint.

Names are taken only while the capture runs or is paused. The check holds the
transcript's row lock until the event commits: a stop that has not committed
yet waits for the name, which the final pass then reads, and a name sent while
a stop is committing waits for it and is refused, rather than written after
the final pass has read the events.
"""

import logging
from typing import Any

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlmodel import select

import backend.api.v1.endpoints.speakers as speakers_module
from backend.api.deps import get_current_user, get_db
from backend.models.recording_public import LiveSpeakerSuggestionsRead
from backend.models.speaker import GlobalSpeaker
from backend.models.speaker_name_event import SpeakerNameEvent
from backend.models.user import User
from backend.utils.live_speaker_labels import live_speaker_display_name
from backend.utils.speaker_name_suggestions import (
    SPEAKER_SUGGESTION_STATUS_ACCEPTED,
    SPEAKER_SUGGESTION_STATUS_REJECTED,
    SPEAKER_SUGGESTION_STATUS_SUPERSEDED,
    resolve_pending_transcript_speaker_suggestion,
)
from backend.utils.speaker_names.durability import persist_name_anchors
from backend.utils.speaker_names.events import UserNameAct
from backend.utils.speaker_names.live import (
    SURFACE_LIVE,
    live_speaker_names,
    record_live_speaker_name,
)
from backend.utils.speaker_names.live_suggestions import live_speaker_suggestions
from backend.utils.transcript_settling import (
    load_capture_publication,
    publication_is_current,
)

from .helpers import _get_owned_recording
from .router import router
from .routes_recording import PLACEHOLDER_NAME_PATTERN

logger = logging.getLogger(__name__)

NAMING_CLOSED_DETAIL = (
    "Live speakers can be named only while the meeting is recording or paused. "
    "Rename the speaker once processing finishes."
)
LIVE_SPEAKER_NOT_FOUND_DETAIL = "Live speaker not found in this recording"
# "Speaker 2" as a name would merge this voice into the final pass's own
# "Speaker 2", another voice.
PLACEHOLDER_NAME_DETAIL = "Pick a real name, or rename them to the right person"


class LiveSpeakerNameUpdate(BaseModel):
    name: str = Field(max_length=255)
    create_person: bool = False
    model_config = ConfigDict(extra="ignore")


class LiveSpeakerNamesRead(BaseModel):
    """Every live label the user has named, with the name it now shows."""

    live_speaker_names: dict[str, str]


def _create_person_for_event(session: Session, event: SpeakerNameEvent) -> None:
    """An accepted event and its owned Person share one commit."""
    person = GlobalSpeaker(name=event.name, user_id=event.created_by_user_id)
    session.add(person)
    session.flush()
    event.global_speaker_id = person.id
    session.add(event)
    session.flush()
    persist_name_anchors(session, event.recording_id)


def _validate_name_update(update: LiveSpeakerNameUpdate, label: str) -> str:
    name = update.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Enter a name")
    if PLACEHOLDER_NAME_PATTERN.match(name):
        raise HTTPException(status_code=422, detail=PLACEHOLDER_NAME_DETAIL)
    if live_speaker_display_name(label) is None:
        raise HTTPException(status_code=404, detail=LIVE_SPEAKER_NOT_FOUND_DETAIL)
    return name


@router.put(
    "/recordings/{recording_id}/live/{label}", response_model=LiveSpeakerNamesRead
)
async def name_live_speaker(
    recording_id: str,
    label: str,
    update: LiveSpeakerNameUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Name live speaker ``label`` ("Speaker 3") during capture."""
    name = _validate_name_update(update, label)

    recording = await _get_owned_recording(db, recording_id, current_user.id)
    transcript, recording = await db.run_sync(
        lambda session: load_capture_publication(session, recording.id)
    )
    if not publication_is_current(recording, transcript, None):
        raise HTTPException(status_code=409, detail=NAMING_CLOSED_DETAIL)

    person_id = (
        await db.execute(
            select(GlobalSpeaker.id).where(
                GlobalSpeaker.name == name,
                GlobalSpeaker.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()

    def _record(sync_session: Session) -> tuple[dict[str, Any], dict[str, str]] | None:
        event = record_live_speaker_name(
            sync_session,
            recording.id,
            label,
            UserNameAct(
                name=name,
                global_speaker_id=person_id,
                actor_user_id=current_user.id,
                surface=SURFACE_LIVE,
            ),
        )
        if event is None:
            return None
        if update.create_person and person_id is None:
            _create_person_for_event(sync_session, event)
        metric = {
            "event_id": event.id,
            "label": label,
            "linked_person": event.global_speaker_id is not None,
            "anchor_ms": event.anchor_end_ms - event.anchor_start_ms,
        }
        return metric, live_speaker_names(sync_session, recording.id)

    recorded = await db.run_sync(_record)
    if recorded is None:
        await db.rollback()
        raise HTTPException(status_code=404, detail=LIVE_SPEAKER_NOT_FOUND_DETAIL)
    metric, names = recorded
    suggestion = resolve_pending_transcript_speaker_suggestion(
        transcript,
        diarization_label=label,
        resolution=SPEAKER_SUGGESTION_STATUS_ACCEPTED,
        actor_user_id=current_user.id,
        reason="user_accepted",
    )
    if suggestion is not None:
        if suggestion["suggested_name"] != name:
            suggestion["status"] = SPEAKER_SUGGESTION_STATUS_SUPERSEDED
            suggestion["resolution_reason"] = "user_renamed"
        db.add(transcript)
    recording_db_id = recording.id
    await db.commit()
    speakers_module.record_pipeline_metric(
        stage="live_speaker_named",
        recording_id=recording_db_id,
        payload=metric,
        log=logger,
    )
    return LiveSpeakerNamesRead(live_speaker_names=names)


@router.post(
    "/recordings/{recording_id}/live/{label}/suggestions/reject",
    response_model=LiveSpeakerSuggestionsRead,
)
async def dismiss_live_speaker_suggestion(
    recording_id: str,
    label: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> LiveSpeakerSuggestionsRead:
    """Hide a label's proposal for the rest of this live recording."""
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    transcript, recording = await db.run_sync(
        lambda session: load_capture_publication(session, recording.id)
    )
    if not publication_is_current(recording, transcript, None):
        raise HTTPException(status_code=409, detail=NAMING_CLOSED_DETAIL)
    pending = await db.run_sync(
        lambda session: live_speaker_suggestions(session, recording, transcript)
    )
    if label not in pending:
        raise HTTPException(status_code=404, detail="Live speaker suggestion not found")
    resolve_pending_transcript_speaker_suggestion(
        transcript,
        diarization_label=label,
        resolution=SPEAKER_SUGGESTION_STATUS_REJECTED,
        actor_user_id=current_user.id,
        reason="user_rejected",
    )
    db.add(transcript)
    names = await db.run_sync(lambda session: live_speaker_names(session, recording.id))
    pending.pop(label)
    await db.commit()
    return LiveSpeakerSuggestionsRead(
        live_speaker_names=names, live_speaker_suggestions=pending
    )
