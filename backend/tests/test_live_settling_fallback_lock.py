"""Whole live lines on a settling recording read as locked, and the guard agrees.

After a GPU-to-CPU fallback mid-capture, or a Resume once settling is off, the
live lane writes ordinary whole lines with no settling metadata, while the
recording keeps requiring settling through ``settled``. Nothing will ever lock
those lines, so a read that left ``locked`` out held their actions back until
finalisation, and for good if processing then failed.
"""

from typing import Any

import pytest

from backend.models.pipeline import ProcessingRunKind, TranscriptUtteranceState
from backend.models.recording_public import serialize_transcript
from backend.models.transcript import Transcript
from backend.tests.test_live_settling_publication import _request
from backend.tests.test_live_speaker_naming_api import REC, _seed
from backend.tests.test_live_speaker_naming_api import client as client
from backend.tests.test_live_speaker_naming_api import session_maker as session_maker
from backend.utils.canonical_pipeline import (
    append_utterances_from_segments,
    build_transient_utterance_payloads_from_segments,
    list_active_utterances,
    serialize_canonical_utterances,
)
from backend.utils.canonical_pipeline.guards import (
    line_still_settling,
    settling_required,
)
from backend.utils.canonical_pipeline.live_settling import (
    close_live_settling,
    publish_live_settling,
)
from backend.utils.transcript_settling import resolve_capture_settling_policy

pytestmark = pytest.mark.anyio

CPU_LINE = "CPU whole line"


async def test_whole_lines_after_a_cpu_fallback_read_locked_and_the_guard_agrees(
    client: Any, session_maker: Any
) -> None:
    await _seed(session_maker, lines=[])
    async with session_maker() as session:
        transcript = await session.get(Transcript, 1)
        transcript.settling_state = {
            "version": 1,
            "eligible": True,
            "generation": "fallback",
            "capture_epoch": 0,
            "phase": "live",
            "policy": {"enabled": True},
        }
        await session.commit()
        await session.run_sync(_settle_then_fall_back_to_the_cpu)

    response = await client.get(f"/api/v1/transcripts/{REC}/utterances")
    assert response.status_code == 200, response.text
    lines = {line["text"]: line for line in response.json()["utterances"]}
    assert lines[CPU_LINE]["provisional"] is True
    assert lines[CPU_LINE]["locked"] is True


def _settle_then_fall_back_to_the_cpu(session: Any) -> None:
    assert publish_live_settling(
        session,
        request=_request(session, 1, 0, end_ms=2000),
        words=[{"word": "Settling", "start_ms": 100, "end_ms": 500}],
    ).accepted
    session.commit()
    transcript = session.get(Transcript, 1)
    [open_line] = list_active_utterances(session, 1)
    assert serialize_canonical_utterances(session, 1)[0]["locked"] is False
    assert line_still_settling(transcript, open_line)

    # What the worker does on finding itself on the CPU under Auto
    # (``_disable_cpu_episode``); the lane then writes whole lines.
    close_live_settling(session, recording_id=1)
    resolve_capture_settling_policy(transcript, device="cpu", disable_on_cpu=True)
    session.commit()
    append_utterances_from_segments(
        session,
        recording_id=1,
        segments=[{"text": CPU_LINE, "start": 4.1, "end": 4.5, "speaker": "UNKNOWN"}],
        run_kind=ProcessingRunKind.LIVE,
        source="live",
        state_override=TranscriptUtteranceState.PROVISIONAL,
    )
    session.commit()

    transcript = session.get(Transcript, 1)
    assert transcript.settling_state["policy"]["enabled"] is False
    assert transcript.settling_state["settled"] is True
    assert settling_required(transcript)
    cpu_line = next(
        row for row in list_active_utterances(session, 1) if row.text == CPU_LINE
    )
    assert cpu_line.state == TranscriptUtteranceState.PROVISIONAL
    assert "live_text_settling" not in (cpu_line.confidence_payload or {})
    read = {line["text"]: line for line in serialize_canonical_utterances(session, 1)}
    assert read[CPU_LINE]["locked"] is True
    assert not line_still_settling(transcript, cpu_line)


def test_snapshot_reads_lock_whole_live_lines_only_on_settling_recordings() -> None:
    cpu_line = {
        "id": "cpu",
        "text": CPU_LINE,
        "start": 4.1,
        "end": 4.5,
        "speaker": "UNKNOWN",
        "provisional": True,
    }
    settled = Transcript(
        id=1,
        recording_id=1,
        settling_state={"settled": True, "policy": {"enabled": False}},
        segments=[cpu_line],
    )
    legacy = Transcript(id=2, recording_id=2, segments=[cpu_line])
    public = serialize_transcript(settled, recording_public_id="settled").segments
    assert public[0]["locked"] is True
    assert build_transient_utterance_payloads_from_segments(settled)[0]["locked"]
    # An unmarked recording's live lines keep their legacy fields.
    legacy_public = serialize_transcript(legacy, recording_public_id="legacy")
    assert "locked" not in legacy_public.segments[0]
    assert "locked" not in build_transient_utterance_payloads_from_segments(legacy)[0]
