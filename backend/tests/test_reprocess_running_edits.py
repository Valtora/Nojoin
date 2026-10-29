"""Edits to a retained transcript wait until the reprocess replacing it ends.

The pass deletes the retained lines, speakers and edit history when it
publishes, so an edit accepted while it runs would answer 200 and then vanish.
Each transcript and per-recording speaker route, which the MCP tools call
too, answers 409 instead.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import Engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import Session, select

from backend.api.v1.endpoints.speakers import (
    routes_name_review,
    routes_recording,
    routes_voiceprint,
)
from backend.api.v1.endpoints.speakers.helpers import (
    MergeRequestLabels,
    SegmentSelection,
    SpeakerColorUpdate,
    SpeakerSplitRequest,
    SpeakerUpdate,
    VoiceprintAction,
)
from backend.api.v1.endpoints.transcripts import (
    routes_line_split,
    routes_segments,
    routes_utterances,
)
from backend.api.v1.endpoints.transcripts.helpers import (
    FindReplaceRequest,
    TranscriptSegmentSpeakerUpdate,
    TranscriptSegmentsUpdate,
    TranscriptSegmentTextUpdate,
    TranscriptUtteranceSpeakerPatch,
    TranscriptUtteranceSplit,
    TranscriptUtteranceTextPatch,
)
from backend.models.pipeline import TranscriptUtterance
from backend.models.recording import Recording
from backend.models.speaker import RecordingSpeaker
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.tests.test_canonical_publication_fence import SCHEMA, Database
from backend.tests.test_canonical_publication_fence import (
    publication_database as publication_database,
)
from backend.tests.test_reprocess_retention_api import _reset, _seed_previous
from backend.utils.reprocess_publication import REPROCESS_RUNNING_MESSAGE

Edit = Callable[[str, AsyncSession, User], Awaitable[Any]]

LINE = "fence-a"
SPEAKER = "SPEAKER_00"

EDITS: dict[str, Edit] = {
    # MCP's correct_utterance_text and correct_utterance_speaker share these.
    "utterance text": lambda rid, db, user: (
        routes_utterances.update_transcript_utterance_text(
            rid,
            LINE,
            TranscriptUtteranceTextPatch(text="Mid-pass fix"),
            db=db,
            current_user=user,
        )
    ),
    "utterance speaker": lambda rid, db, user: (
        routes_utterances.update_transcript_utterance_speaker(
            rid,
            LINE,
            TranscriptUtteranceSpeakerPatch(new_speaker_name="Alice Example"),
            db=db,
            current_user=user,
        )
    ),
    # MCP's unlock_utterance.
    "lock clear": lambda rid, db, user: routes_utterances.clear_utterance_locks(
        rid, LINE, expected_revision=None, db=db, current_user=user
    ),
    "line split": lambda rid, db, user: routes_line_split.split_transcript_utterance(
        rid,
        LINE,
        TranscriptUtteranceSplit(before_word_id="word-1", expected_revision=1),
        db=db,
        current_user=user,
    ),
    "segment text": lambda rid, db, user: (
        routes_segments.update_transcript_segment_text(
            rid, 0, TranscriptSegmentTextUpdate(text="Mid-pass fix"), db, user
        )
    ),
    "segment speaker": lambda rid, db, user: routes_segments.update_segment_speaker(
        rid,
        0,
        TranscriptSegmentSpeakerUpdate(new_speaker_name="Alice Example"),
        db,
        user,
    ),
    "find and replace": lambda rid, db, user: routes_segments.find_and_replace(
        rid, FindReplaceRequest(find_text="first", replace_text="fixed"), db, user
    ),
    "bulk segments": lambda rid, db, user: routes_segments.update_transcript_segments(
        rid, TranscriptSegmentsUpdate(segments=[]), db, user
    ),
    # Every per-recording speaker route shares one guard. MCP's
    # set_speaker_name calls the first.
    "speaker name": lambda rid, db, user: (
        routes_recording.apply_recording_speaker_update(
            rid,
            SpeakerUpdate(
                diarization_label=SPEAKER, global_speaker_name="Alice Example"
            ),
            db=db,
            current_user=user,
        )
    ),
    "add to People": lambda rid, db, user: routes_recording.promote_speaker_to_global(
        rid, SPEAKER, db, user
    ),
    "speaker merge": lambda rid, db, user: routes_recording.merge_recording_speakers(
        rid,
        MergeRequestLabels(target_speaker_label=SPEAKER, source_speaker_label="X"),
        db,
        user,
    ),
    "speaker unmerge": lambda rid, db, user: (
        routes_recording.unmerge_merged_recording_speaker(rid, SPEAKER, db, user)
    ),
    "speaker delete": lambda rid, db, user: routes_recording.delete_recording_speaker(
        rid, SPEAKER, db, user
    ),
    "speaker colour": lambda rid, db, user: routes_recording.update_speaker_color(
        rid, SPEAKER, SpeakerColorUpdate(color="blue"), db, user
    ),
    "speaker split": lambda rid, db, user: routes_recording.split_local_speaker(
        rid,
        SPEAKER,
        SpeakerSplitRequest(
            new_speaker_name="Alice Example",
            segments=[SegmentSelection(recording_id=rid, start=0, end=1)],
        ),
        db,
        user,
    ),
    "name confirm": lambda rid, db, user: routes_name_review.confirm_speaker_name(
        rid, SPEAKER, db, user
    ),
    "name dismiss": lambda rid, db, user: routes_name_review.dismiss_speaker_name(
        rid, SPEAKER, db, user
    ),
    "voiceprint extract": lambda rid, db, user: routes_voiceprint.extract_voiceprint(
        rid, SPEAKER, db, user
    ),
    "voiceprint apply": lambda rid, db, user: routes_voiceprint.apply_voiceprint_action(
        rid, SPEAKER, VoiceprintAction(action="local_only"), db, user
    ),
    "voiceprint delete": lambda rid, db, user: routes_voiceprint.delete_voiceprint(
        rid, SPEAKER, db, user
    ),
    "voiceprints extract all": lambda rid, db, user: (
        routes_voiceprint.extract_all_voiceprints(rid, db, user)
    ),
}


def _snapshot(engine: Engine, recording_id: int) -> dict[str, Any]:
    with Session(engine) as session:
        transcript = session.exec(
            select(Transcript).where(Transcript.recording_id == recording_id)
        ).one()
        return {
            "text": transcript.text,
            "segments": transcript.segments,
            "lines": sorted(
                (line.public_id, line.text, line.speaker_label, line.revision)
                for line in session.exec(
                    select(TranscriptUtterance).where(
                        TranscriptUtterance.recording_id == recording_id
                    )
                ).all()
            ),
            "speakers": sorted(
                (speaker.diarization_label, speaker.name, speaker.local_name)
                for speaker in session.exec(
                    select(RecordingSpeaker).where(
                        RecordingSpeaker.recording_id == recording_id
                    )
                ).all()
            ),
        }


async def _attempt(engine: Engine, recording_id: int, edit: Edit) -> HTTPException:
    async_engine = create_async_engine(
        engine.url.set(drivername="postgresql+asyncpg"),
        connect_args={"server_settings": {"search_path": SCHEMA}},
    )
    try:
        async with AsyncSession(async_engine, expire_on_commit=False) as session:
            recording = await session.get(Recording, recording_id)
            owner = User(
                id=recording.user_id, username="fence-owner", hashed_password="x"
            )
            with pytest.raises(HTTPException) as refused:
                await edit(recording.public_id, session, owner)
            return refused.value
    finally:
        await async_engine.dispose()


@pytest.mark.parametrize("edit", list(EDITS))
def test_edits_wait_for_a_running_reprocess(
    publication_database: Database, edit: str
) -> None:
    engine, recording_id, _ = publication_database
    _seed_previous(engine, recording_id)
    asyncio.run(_reset(engine, recording_id))
    before = _snapshot(engine, recording_id)

    refused = asyncio.run(_attempt(engine, recording_id, EDITS[edit]))

    assert (refused.status_code, refused.detail) == (409, REPROCESS_RUNNING_MESSAGE)
    assert _snapshot(engine, recording_id) == before
