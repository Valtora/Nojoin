"""Find and replace writes through the text lock a person's edit left (#842).

Every line find and replace changes is marked as edited, which locks it, and
the canonical write kept a locked line's text. A second replace then skipped
the lines the first had changed, and a replace over a line edited by hand
left it alone, both still answering 200. The lock is right for anything that
sends back a snapshot, such as the bulk segment write a speaker change makes,
and for the pipeline, but a find and replace is the person's own edit.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from backend.api.v1.endpoints.transcripts.helpers import _apply_find_replace
from backend.models.transcript import Transcript
from backend.tests.test_canonical_transcript_phase1 import api_app as api_app
from backend.tests.test_canonical_transcript_phase1 import client as client
from backend.tests.test_canonical_transcript_phase1 import (
    test_session_maker as test_session_maker,
)
from backend.utils.canonical_pipeline import update_utterance_text

REC = "replace-rec"
NOW = "2026-10-30 00:00:00"
LINES = ["the plan is ready", "we ship the build", "nothing to see"]


async def _seed(session_maker: sessionmaker, client: AsyncClient) -> list[str]:
    """A processed meeting with three lines; returns their utterance ids."""
    segments = [
        {
            "start": float(index),
            "end": float(index) + 0.9,
            "speaker": "SPEAKER_00",
            "text": line,
            "segment_source": "legacy",
        }
        for index, line in enumerate(LINES)
    ]
    async with session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, status, upload_progress, processing_progress,
                    pipeline_generation, is_archived, is_deleted, user_id
                ) VALUES (1, :now, :now, 'Planning', :public_id, 'uid-replace',
                          '/tmp/replace.wav', 'PROCESSED', 0, 100, 'unified',
                          0, 0, 1)
                """
            ),
            {"now": NOW, "public_id": REC},
        )
        await session.execute(
            text(
                """
                INSERT INTO transcripts (
                    id, created_at, updated_at, recording_id, text, segments,
                    notes, meeting_edge_status, notes_status, transcript_status
                ) VALUES (1, :now, :now, 1, :text, :segments, NULL, 'idle',
                          'pending', 'completed')
                """
            ),
            {"now": NOW, "text": " ".join(LINES), "segments": json.dumps(segments)},
        )
        await session.execute(
            text(
                """
                INSERT INTO recording_speakers (
                    id, created_at, updated_at, public_id, recording_id,
                    diarization_label, name, speaker_status, speaker_kind,
                    identity_locked
                ) VALUES (1, :now, :now, 'speaker-replace', 1, 'SPEAKER_00',
                          'Speaker 1', 'active', 'automated', 0)
                """
            ),
            {"now": NOW},
        )
        await session.commit()
    # The first read backfills the canonical lines.
    response = await client.get(f"/api/v1/transcripts/{REC}/utterances")
    assert response.status_code == 200, response.text
    return [line["id"] for line in response.json()["utterances"]]


async def _replace(client: AsyncClient, find: str, replace: str) -> list[str]:
    response = await client.post(
        f"/api/v1/transcripts/{REC}/replace",
        json={"find_text": find, "replace_text": replace, "case_sensitive": True},
    )
    assert response.status_code == 200, response.text
    return [segment["text"] for segment in response.json()["segments"]]


async def _rows(session_maker: sessionmaker) -> list[tuple[Any, ...]]:
    """Each active line's text, text lock, edit source and revision."""
    async with session_maker() as session:
        rows = await session.execute(
            text(
                "SELECT text, manual_text_locked, text_last_edit_source, revision "
                "FROM transcript_utterances WHERE UPPER(state) != 'SUPERSEDED' "
                "ORDER BY sort_key"
            )
        )
        return [
            (line, bool(locked), source, revision)
            for line, locked, source, revision in rows.all()
        ]


@pytest.mark.anyio
async def test_a_second_replace_changes_the_lines_the_first_changed(
    client: AsyncClient, test_session_maker: sessionmaker
) -> None:
    await _seed(test_session_maker, client)

    assert await _replace(client, "the", "THE") == [
        "THE plan is ready",
        "we ship THE build",
        "nothing to see",
    ]
    # Before #842 this answered 200 and changed nothing: both lines were
    # locked by the first replace.
    assert await _replace(client, "THE", "the") == LINES

    assert await _rows(test_session_maker) == [
        ("the plan is ready", True, "api", 3),
        ("we ship the build", True, "api", 3),
        # Never matched, so never locked or touched.
        ("nothing to see", False, None, 1),
    ]


@pytest.mark.anyio
async def test_a_replace_changes_a_line_edited_by_hand_and_keeps_it_locked(
    client: AsyncClient, test_session_maker: sessionmaker
) -> None:
    ids = await _seed(test_session_maker, client)
    edited = await client.patch(
        f"/api/v1/transcripts/{REC}/utterances/{ids[0]}/text",
        json={"text": "the plan is nearly ready"},
    )
    assert edited.status_code == 200, edited.text

    assert (await _replace(client, "plan", "roadmap"))[0] == (
        "the roadmap is nearly ready"
    )

    # The line stays locked: a snapshot written back over it, as a speaker
    # change writes the whole transcript, still leaves the person's text.
    stale = [
        {
            "id": utterance_id,
            "start": float(index),
            "end": float(index) + 0.9,
            "speaker": "SPEAKER_00",
            "text": line,
            "segment_source": "legacy",
        }
        for index, (utterance_id, line) in enumerate(zip(ids, LINES, strict=True))
    ]
    response = await client.put(
        f"/api/v1/transcripts/{REC}/segments", json={"segments": stale}
    )
    assert response.status_code == 200, response.text
    assert [row[:3] for row in await _rows(test_session_maker)] == [
        ("the roadmap is nearly ready", True, "api"),
        ("we ship the build", False, None),
        ("nothing to see", False, None),
    ]


@pytest.mark.anyio
async def test_a_replace_over_an_assistants_correction_is_the_persons_edit(
    client: AsyncClient, test_session_maker: sessionmaker
) -> None:
    ids = await _seed(test_session_maker, client)
    async with test_session_maker() as session:
        await session.run_sync(
            lambda sync: update_utterance_text(
                sync,
                recording_id=1,
                utterance_public_id=ids[1],
                text="we ship the new build",
                source="mcp",
            )
        )
        await session.commit()

    await _replace(client, "new build", "release")

    # The app's "AI corrected text" badge gives way to a plain "Edited".
    response = await client.get(f"/api/v1/transcripts/{REC}/utterances")
    line = response.json()["utterances"][1]
    assert (line["text"], line["text_manually_edited"], line["text_edit_source"]) == (
        "we ship the release",
        True,
        "api",
    )


def test_a_replace_counts_and_marks_only_the_lines_it_changes() -> None:
    """A case-insensitive match the replacement leaves as it was is not an
    edit: counting it overstated the change, and marking it locked the line."""
    transcript = Transcript(
        segments=[{"text": "the plan"}, {"text": "The build"}, {"text": "no match"}],
        notes=None,
    )

    assert _apply_find_replace(transcript, "the", "the") == 1

    assert transcript.segments == [
        {"text": "the plan"},
        {"text": "the build", "text_manually_edited": True, "text_edit_source": "api"},
        {"text": "no match"},
    ]
