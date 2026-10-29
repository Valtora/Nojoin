"""Name a live speaker while the meeting is still being recorded.

A live label is no speaker of the recording, so the name is kept as the
user's name event anchored on the label's speech
(``backend/utils/speaker_names/live.py``) and carried to the final speaker
when the recording is processed. As a rename does, a name that is exactly a
person's in People links that person; nothing creates one.

Names are taken only while the capture runs or is paused. The check holds the
transcript's row lock until the event commits: a stop that has not committed
yet waits for the name, which the final pass then reads, and a name sent while
a stop is committing waits for it and is refused, rather than written after
the final pass has read the events.
"""

import logging

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

import backend.api.v1.endpoints.speakers as speakers_module
from backend.api.deps import get_current_user, get_db
from backend.models.speaker import GlobalSpeaker
from backend.models.user import User
from backend.utils.live_speaker_labels import live_speaker_display_name
from backend.utils.speaker_names.events import UserNameAct
from backend.utils.speaker_names.live import (
    SURFACE_LIVE,
    live_speaker_names,
    record_live_speaker_name,
)
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
    model_config = ConfigDict(extra="ignore")


class LiveSpeakerNamesRead(BaseModel):
    """Every live label the user has named, with the name it now shows."""

    live_speaker_names: dict[str, str]


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
    name = update.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Enter a name")
    if PLACEHOLDER_NAME_PATTERN.match(name):
        raise HTTPException(status_code=422, detail=PLACEHOLDER_NAME_DETAIL)
    if live_speaker_display_name(label) is None:
        raise HTTPException(status_code=404, detail=LIVE_SPEAKER_NOT_FOUND_DETAIL)

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

    def _record(sync_session):
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
        metric = {
            "event_id": event.id,
            "label": label,
            "linked_person": person_id is not None,
            "anchor_ms": event.anchor_end_ms - event.anchor_start_ms,
        }
        return metric, live_speaker_names(sync_session, recording.id)

    recorded = await db.run_sync(_record)
    if recorded is None:
        await db.rollback()
        raise HTTPException(status_code=404, detail=LIVE_SPEAKER_NOT_FOUND_DETAIL)
    metric, names = recorded
    recording_db_id = recording.id
    await db.commit()
    speakers_module.record_pipeline_metric(
        stage="live_speaker_named",
        recording_id=recording_db_id,
        payload=metric,
        log=logger,
    )
    return LiveSpeakerNamesRead(live_speaker_names=names)
