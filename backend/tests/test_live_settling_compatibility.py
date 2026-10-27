"""Explicit live reads and edits work while compatibility JSON is deferred."""

from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy import text

from backend.models.pipeline import ProcessingRunKind, TranscriptUtteranceState
from backend.models.speaker import RecordingSpeaker
from backend.models.transcript import Transcript
from backend.tests.live_naming_fixtures import ONE
from backend.tests.sqlite_schemas import P_TAGS_SCHEMA, PEOPLE_TAGS_SCHEMA
from backend.tests.test_live_settling_publication import _request
from backend.tests.test_live_speaker_naming_api import REC, _seed
from backend.tests.test_live_speaker_naming_api import client as client
from backend.tests.test_live_speaker_naming_api import session_maker as session_maker
from backend.utils.canonical_pipeline import (
    append_utterances_from_segments,
    apply_compatibility_segment_replace,
    build_transcript_segments_for_read,
    list_active_utterances,
)
from backend.utils.canonical_pipeline.live_compatibility import (
    prepare_live_compatibility_mutation,
)
from backend.utils.canonical_pipeline.live_settling import (
    close_live_settling,
    publish_live_settling,
)

pytestmark = pytest.mark.anyio


async def _defer(session_maker: Any) -> None:
    await _seed(session_maker, people=((1, 1, "Alex"),))
    async with session_maker() as session:
        for schema in (PEOPLE_TAGS_SCHEMA, P_TAGS_SCHEMA):
            await session.execute(text(schema))
        transcript = await session.get(Transcript, 1)
        transcript.segments = []
        transcript.text = ""
        transcript.settling_state = {
            "version": 1,
            "eligible": True,
            "phase": "live",
            "policy": {"enabled": True},
        }
        # These edit fixtures represent completed whole lines. Unlocked
        # sources are refused by the shared action guard in commit 6.
        for row in await session.run_sync(list_active_utterances, 1):
            row.state = TranscriptUtteranceState.STABLE
            session.add(row)
        session.add(
            RecordingSpeaker(recording_id=1, diarization_label=ONE, global_speaker_id=1)
        )
        await session.commit()


@pytest.mark.parametrize("surface", ["index", "public_id", "replace", "bulk"])
async def test_live_edits_materialise_current_lines_before_mutating(
    client: Any, session_maker: Any, surface: str
) -> None:
    await _defer(session_maker)
    if surface == "index":
        response = await client.put(
            f"/api/v1/transcripts/{REC}/segments/0/text", json={"text": "Edited words"}
        )
    elif surface == "public_id":
        response = await client.patch(
            f"/api/v1/transcripts/{REC}/utterances/live-1-0/text",
            json={"text": "Edited words"},
        )
    elif surface == "replace":
        response = await client.post(
            f"/api/v1/transcripts/{REC}/replace",
            json={"find_text": "live line 0", "replace_text": "Edited words"},
        )
    else:
        async with session_maker() as session:
            segments = await session.run_sync(build_transcript_segments_for_read, 1)
        segments[0]["text"] = "Edited words"
        segments[0]["text_manually_edited"] = True
        for segment in segments:
            segment.pop("id")
        response = await client.put(
            f"/api/v1/transcripts/{REC}/segments", json={"segments": segments}
        )
    assert response.status_code == 200, response.text
    assert len(response.json()["segments"]) == 9
    assert response.json()["segments"][0]["text"].startswith("Edited words")
    async with session_maker() as session:
        canonical = await session.run_sync(build_transcript_segments_for_read, 1)
    assert canonical[0]["text"].startswith("Edited words")
    assert canonical[0]["text_manually_edited"] is True


async def test_people_samples_read_current_live_lines_without_materialising_snapshot(
    client: Any, session_maker: Any
) -> None:
    await _defer(session_maker)
    response = await client.get("/api/v1/speakers/1/segments")
    assert response.status_code == 200, response.text
    assert [segment["start"] for segment in response.json()] == [0.0, 4.0]
    async with session_maker() as session:
        assert (await session.get(Transcript, 1)).segments == []
    forbidden = await client.get("/api/v1/speakers/999/segments")
    assert forbidden.status_code == 404


async def test_speaker_mutation_updates_canonical_live_lines_from_deferred_snapshot(
    client: Any, session_maker: Any
) -> None:
    await _defer(session_maker)
    response = await client.delete(f"/api/v1/speakers/recordings/{REC}/speakers/{ONE}")
    assert response.status_code == 200, response.text
    async with session_maker() as session:
        segments = await session.run_sync(build_transcript_segments_for_read, 1)
        remaining = (
            await session.execute(text("SELECT COUNT(*) FROM recording_speakers"))
        ).scalar()
    assert len(segments) == 9
    assert all(segment["speaker"] != ONE for segment in segments)
    assert remaining == 0


async def test_live_replacement_keeps_capture_order_when_next_window_adds_a_line(
    session_maker: Any,
) -> None:
    await _seed(session_maker, lines=[])
    async with session_maker() as session:
        transcript = await session.get(Transcript, 1)
        transcript.settling_state = {
            "version": 1,
            "eligible": True,
            "generation": "order-test",
            "capture_epoch": 0,
            "phase": "live",
            "policy": {"enabled": True, "k": 1, "window_s": 10},
            "speaker_enabled": False,
        }
        await session.commit()
        await session.run_sync(_replace_then_publish_next_line)


def _replace_then_publish_next_line(session: Any) -> None:
    words = [
        {"word": "First", "start_ms": 100, "end_ms": 500},
        {"word": "Second", "start_ms": 2100, "end_ms": 2500},
    ]
    for step in range(2):
        assert publish_live_settling(
            session,
            # Both edit targets are completed speech; the final silence closes
            # the second line independently of its agreement count.
            request=replace(
                _request(session, 1, step, end_ms=3000), silence_start_ms=2500
            ),
            words=words,
        ).accepted
        session.commit()
    original_keys = [row.sort_key for row in list_active_utterances(session, 1)]
    prepare_live_compatibility_mutation(session, 1)
    segments = build_transcript_segments_for_read(session, 1)
    assert [line["text"] for line in segments] == ["First", "Second"]
    segments[0].update(text="Edited", text_manually_edited=True)
    apply_compatibility_segment_replace(session, recording_id=1, segments=segments)
    session.commit()
    assert [row.sort_key for row in list_active_utterances(session, 1)] == original_keys
    assert publish_live_settling(
        session,
        request=_request(session, 1, 2, end_ms=6000),
        words=[{"word": "Third", "start_ms": 4100, "end_ms": 4500}],
    ).accepted
    session.commit()
    actual = build_transcript_segments_for_read(session, 1)
    assert [line["text"] for line in actual] == ["Edited", "Second", "Third"]


async def test_marked_cpu_cuda_and_cpu_fallback_lines_share_capture_order(
    session_maker: Any,
) -> None:
    await _seed(session_maker, lines=[])
    async with session_maker() as session:
        transcript = await session.get(Transcript, 1)
        transcript.settling_state = {
            "version": 1,
            "eligible": True,
            "generation": "mixed-devices",
            "capture_epoch": 0,
            "phase": "live",
            "policy": {"enabled": False, "k": 1, "window_s": 10},
        }
        await session.commit()
        await session.run_sync(_append_across_devices)


def _append_across_devices(session: Any) -> None:
    _append_cpu_lines(session, [("CPU first", 0.1), ("CPU second", 2.1)])
    transcript = session.get(Transcript, 1)
    transcript.settling_state = {
        **transcript.settling_state,
        "policy": {"enabled": True, "k": 1, "window_s": 10},
    }
    session.commit()
    assert publish_live_settling(
        session,
        request=_request(session, 1, 0, end_ms=6000),
        words=[{"word": "CUDA third", "start_ms": 4100, "end_ms": 4500}],
    ).accepted
    session.commit()
    close_live_settling(session, recording_id=1)
    transcript.settling_state = {
        **transcript.settling_state,
        "policy": {"enabled": False, "k": 1, "window_s": 10},
    }
    session.commit()
    _append_cpu_lines(session, [("CPU fourth", 6.1)])
    lines = build_transcript_segments_for_read(session, 1)
    assert [line["text"] for line in lines] == [
        "CPU first",
        "CPU second",
        "CUDA third",
        "CPU fourth",
    ]


def _append_cpu_lines(session: Any, lines: list[tuple[str, float]]) -> None:
    append_utterances_from_segments(
        session,
        recording_id=1,
        segments=[
            {"text": label, "start": start, "end": start + 0.4, "speaker": "UNKNOWN"}
            for label, start in lines
        ],
        run_kind=ProcessingRunKind.LIVE,
        source="live",
        state_override=TranscriptUtteranceState.PROVISIONAL,
    )
    session.commit()
