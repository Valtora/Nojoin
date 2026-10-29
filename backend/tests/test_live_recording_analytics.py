"""Live analytics reads estimates without starting final processing."""

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlmodel import select

from backend.api.v1.endpoints.transcripts import routes_analytics
from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceState
from backend.models.recording import Recording, RecordingStatus
from backend.models.speaker import RecordingSpeaker
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.tests.test_live_speaker_naming_readers import session as session


class ReadSession:
    def __init__(self, session: Session) -> None:
        self.session = session

    async def run_sync(self, callback: Callable[[Session], Any]) -> Any:
        return callback(self.session)

    async def execute(self, statement: Any) -> Any:
        return self.session.execute(statement)

    async def commit(self) -> None:
        pytest.fail("A live read must not commit")


def _live(session: Session) -> dict[str, Any]:
    from backend.services.meeting_analytics.live import compute_live_recording_analytics

    recording = session.get(Recording, 1)
    assert recording is not None
    transcript = session.execute(
        select(Transcript).where(Transcript.recording_id == 1)
    ).scalar_one()
    return compute_live_recording_analytics(session, recording, transcript)


def test_live_canonical_names_and_read_only_estimates(session: Session) -> None:
    before_new, before_dirty = set(session.new), set(session.dirty)
    result = _live(session)
    assert result["provisional"] is True
    # The shared fixture also has a third speaker and provisional UNKNOWN lines.
    assert {speaker["name"] for speaker in result["speakers"]} == {
        "Alex",
        "Speaker 2",
        "Speaker 3",
        "UNKNOWN",
    }
    assert result["metrics"]["utterance_count"] > 0
    assert set(result["metrics"]) == {"utterance_count", "talk_time", "turn_structure"}
    assert (
        not {"silence", "delivery", "ai", "audio_overlap", "ai_budget"} & result.keys()
    )
    assert set(session.new) == before_new
    assert set(session.dirty) == before_dirty
    # Reassignment changes the grouping; a later rename keeps that stable key.
    speaker = RecordingSpeaker(
        id=10, recording_id=1, diarization_label="SPEAKER_10", local_name="Renamed"
    )
    session.add(speaker)
    line = session.execute(
        select(TranscriptUtterance).order_by(TranscriptUtterance.id).limit(1)
    ).scalar_one()
    line.recording_speaker_id = 10
    session.commit()
    reassigned = _live(session)
    assert reassigned["metrics"]["talk_time"]["rs:10"]["speech_ms"] == 4_000
    speaker.local_name = "Newest name"
    session.commit()
    renamed = _live(session)
    assert (
        next(row for row in renamed["speakers"] if row["speaker_key"] == "rs:10")[
            "name"
        ]
        == "Newest name"
    )
    assert renamed["metrics"] == reassigned["metrics"]


def test_live_projection_fallback_and_authoritative_empty(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import backend.utils.canonical_pipeline.core as core

    transcript = session.execute(
        select(Transcript).where(Transcript.recording_id == 1)
    ).scalar_one()
    transcript.segments = [
        {
            "start": 1.0,
            "end": 4.0,
            "text": "one two three",
            "speaker": "LIVE_PROVISIONAL_1",
        }
    ]
    session.commit()
    monkeypatch.setattr(core, "serialize_canonical_utterances", lambda *args: [])
    result = _live(session)
    assert result["speakers"][0]["name"] == "Alex"
    assert (
        result["metrics"]["talk_time"]["label:LIVE_PROVISIONAL_1"]["speech_ms"] == 3_000
    )
    transcript.settling_state = {"eligible": True, "policy": {"enabled": True}}
    session.commit()
    assert _live(session)["metrics"]["utterance_count"] == 0


def test_live_pending_tail_counts_under_the_previous_speaker(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import backend.utils.canonical_pipeline.core as core

    line = {"text": "one two", "speaker": "LIVE_PROVISIONAL_1", "provisional": True}
    pending = {**line, "speaker": "UNKNOWN", "diarisation_pending": True}
    monkeypatch.setattr(
        core,
        "serialize_canonical_utterances",
        lambda *args: [
            {**pending, "start": 0.0, "end": 1.0},
            {**line, "start": 1.0, "end": 4.0},
            {**pending, "start": 4.0, "end": 6.0},
            {**pending, "start": 6.0, "end": 7.0},
        ],
    )
    talk_time = _live(session)["metrics"]["talk_time"]
    assert talk_time["label:LIVE_PROVISIONAL_1"]["speech_ms"] == 6_000
    assert talk_time["label:UNKNOWN"]["speech_ms"] == 1_000


@pytest.mark.parametrize(
    ("cleo_start_ms", "cleo_end_ms", "ana_share", "cleo_share"),
    [(15_000, 20_000, 0.75, 0.25), (10_000, 20_000, 0.6, 0.4)],
)
def test_live_reassignment_unions_each_speakers_overlapping_speech(
    session: Session,
    cleo_start_ms: int,
    cleo_end_ms: int,
    ana_share: float,
    cleo_share: float,
) -> None:
    session.add_all(
        [
            RecordingSpeaker(
                id=90, recording_id=1, diarization_label="SPEAKER_90", local_name="Ana"
            ),
            RecordingSpeaker(
                id=91, recording_id=1, diarization_label="SPEAKER_91", local_name="Cleo"
            ),
        ]
    )
    lines = (
        session.execute(select(TranscriptUtterance).order_by(TranscriptUtterance.id))
        .scalars()
        .all()
    )
    for line in lines[3:]:
        line.state = TranscriptUtteranceState.SUPERSEDED
    for line, (speaker_id, start, end) in zip(
        lines[:3],
        [(90, 0, 10_000), (90, 5_000, 15_000), (91, cleo_start_ms, cleo_end_ms)],
    ):
        line.recording_speaker_id = speaker_id
        line.start_ms, line.end_ms = start, end
        line.manual_speaker_locked = True
    session.commit()

    result = _live(session)

    assert {speaker["name"] for speaker in result["speakers"]} == {"Ana", "Cleo"}
    assert result["metrics"]["talk_time"] == {
        "rs:90": {
            "speech_ms": 15_000,
            "share_of_speech": ana_share,
            "share_of_duration": 0.75,
        },
        "rs:91": {
            "speech_ms": cleo_end_ms - cleo_start_ms,
            "share_of_speech": cleo_share,
            "share_of_duration": (cleo_end_ms - cleo_start_ms) / 20_000,
        },
    }
    assert result["metrics"]["utterance_count"] == 3


def test_live_projection_unions_resolved_aliases_without_filling_gaps(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import backend.utils.canonical_pipeline.core as core

    session.add(
        RecordingSpeaker(
            id=90, recording_id=1, diarization_label="SPEAKER_90", local_name="Ana"
        )
    )
    transcript = session.execute(
        select(Transcript).where(Transcript.recording_id == 1)
    ).scalar_one()
    intervals = [
        (5, 15),
        (0, 10),
        (0, 10),
        (2, 3),
        (15, 17),
        (20, 22),
        (23, 23),
        (25, 24),
    ]
    transcript.segments = [
        {
            "speaker": "SPEAKER_90",
            "recording_speaker_id": 90 if index % 2 else None,
            "start": start,
            "end": end,
            "text": "speech",
        }
        for index, (start, end) in enumerate(intervals)
    ] + [{"speaker": "B", "start": 10, "end": 25, "text": "other speech"}]
    session.commit()
    monkeypatch.setattr(core, "serialize_canonical_utterances", lambda *args: [])

    result = _live(session)

    assert result["metrics"]["talk_time"] == {
        "rs:90": {
            "speech_ms": 19_000,
            "share_of_speech": 0.5588,
            "share_of_duration": 0.76,
        },
        "label:B": {
            "speech_ms": 15_000,
            "share_of_speech": 0.4412,
            "share_of_duration": 0.6,
        },
    }
    assert result["metrics"]["utterance_count"] == 9


def test_live_wpm_uses_median_threshold_and_exclusions() -> None:
    from backend.services.meeting_analytics.live import transcript_wpm

    segments = [
        {
            "start_ms": index * 10_000,
            "end_ms": index * 10_000 + duration,
            "text": "one two three four",
            "speaker": "A",
        }
        for index, duration in enumerate([1_500, 2_000, 2_000, 2_000, 8_000])
    ]
    assert transcript_wpm(segments)["label:A"]["words_per_minute"] == 120
    assert transcript_wpm(segments[:4])["label:A"]["words_per_minute"] is None
    segments += [
        {"start_ms": 60_000, "end_ms": 60_100, "text": "short", "speaker": "A"},
        {
            "start_ms": 70_000,
            "end_ms": 72_000,
            "text": "overlap",
            "speaker": "A",
            "overlapping_speakers": ["B"],
        },
        {"start_ms": 80_000, "end_ms": 82_000, "text": "both", "speaker": "A"},
        {"start_ms": 81_000, "end_ms": 83_000, "text": "both", "speaker": "B"},
    ]
    assert transcript_wpm(segments)["label:A"] == {
        "words_per_minute": 120,
        "sample_count": 5,
        "excluded_short": 1,
        "excluded_overlapping": 2,
    }


def test_live_names_filter_effective_projection_labels(session: Session) -> None:
    from backend.utils.speaker_names.events import UserNameAct
    from backend.utils.speaker_names.live import (
        live_speaker_names,
        record_live_speaker_name,
    )

    record_live_speaker_name(
        session,
        1,
        "LIVE_PROVISIONAL_1",
        UserNameAct("New Alex", None, actor_user_id=1, surface="live"),
    )
    for line in session.execute(select(TranscriptUtterance)).scalars():
        line.state = TranscriptUtteranceState.SUPERSEDED
    session.commit()
    assert live_speaker_names(session, 1) == {}
    assert live_speaker_names(session, 1, labels=["LIVE_PROVISIONAL_1"]) == {
        "LIVE_PROVISIONAL_1": "New Alex"
    }
    assert live_speaker_names(session, 1, labels=[]) == {}


def test_projection_labels_use_current_speaker_identity_without_retired_matches(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import backend.utils.canonical_pipeline.core as core

    session.add(
        RecordingSpeaker(
            id=91,
            recording_id=1,
            diarization_label="SPEAKER_91",
            local_name="Current name",
        )
    )
    session.add(
        RecordingSpeaker(
            id=92,
            recording_id=1,
            diarization_label="SPEAKER_92",
            local_name="Retired name",
            merged_into_id=91,
            speaker_status="merged",
        )
    )
    transcript = session.execute(
        select(Transcript).where(Transcript.recording_id == 1)
    ).scalar_one()
    transcript.segments = [
        {"speaker": label, "start": start, "end": start + 4, "text": "hello there"}
        for label, start in [("SPEAKER_91", 0), ("SPEAKER_92", 5)]
    ]
    session.commit()
    monkeypatch.setattr(core, "serialize_canonical_utterances", lambda *args: [])
    result = _live(session)
    assert result["speakers"][0]["name"] == "Current name"
    assert result["speakers"][0]["speaker_key"] == "rs:91"
    assert set(result["metrics"]["talk_time"]) == {"rs:91", "label:SPEAKER_92"}
    assert set(result["transcript_wpm"]) == {"rs:91", "label:SPEAKER_92"}


def test_final_get_rechecks_status_after_its_read_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording = Recording(
        id=1, name="Meeting", audio_path="", user_id=1, status=RecordingStatus.PROCESSED
    )
    transcript = Transcript(recording_id=1)

    class ChangedRecording:
        async def refresh(self, row: Recording) -> None:
            row.status = RecordingStatus.PROCESSING

    async def owned(*args: Any) -> Recording:
        return recording

    async def get_transcript(*args: Any) -> Transcript:
        return transcript

    async def read(*args: Any) -> dict[str, Any]:
        return {"delivery_stale": True}

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail(
            "A new processing pass must prevent final dispatch and budget access"
        )

    monkeypatch.setattr(routes_analytics, "_get_owned_recording", owned)
    monkeypatch.setattr(routes_analytics, "_get_recording_transcript", get_transcript)
    monkeypatch.setattr(routes_analytics, "read_recording_analytics", read)
    monkeypatch.setattr(routes_analytics, "_auto_remeasure_delivery", forbidden)
    monkeypatch.setattr(routes_analytics, "get_budget", forbidden)
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            routes_analytics.get_recording_analytics(
                "rec-1",
                ChangedRecording(),
                User(id=1, username="alice", hashed_password="x"),
            )
        )
    assert error.value.status_code == 409


def test_live_route_never_runs_final_work(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    transcript = session.execute(
        select(Transcript).where(Transcript.recording_id == 1)
    ).scalar_one()

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Live analytics entered final processing")

    for name in (
        "_backfill_and_compute",
        "dispatch_task_best_effort",
        "claim_run",
        "get_budget",
    ):
        monkeypatch.setattr(routes_analytics, name, forbidden)
    result = asyncio.run(
        routes_analytics.get_live_recording_analytics(
            "live-rec",
            ReadSession(session),
            User(id=1, username="alice", hashed_password="x"),
        )
    )
    assert result.provisional is True
    assert result.recording_id == "live-rec"
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            routes_analytics.get_live_recording_analytics(
                "live-rec",
                ReadSession(session),
                User(id=2, username="other", hashed_password="x"),
            )
        )
    assert error.value.status_code == 404
    session.delete(transcript)
    session.commit()
    empty = asyncio.run(
        routes_analytics.get_live_recording_analytics(
            "live-rec",
            ReadSession(session),
            User(id=1, username="alice", hashed_password="x"),
        )
    )
    assert empty.speakers == []
    assert empty.metrics.utterance_count == 0
    assert empty.transcript_wpm == {}


@pytest.mark.parametrize(
    "status",
    [
        RecordingStatus.UPLOADING,
        RecordingStatus.PAUSED,
        RecordingStatus.QUEUED,
        RecordingStatus.PROCESSING,
    ],
)
@pytest.mark.parametrize(
    "handler",
    [
        "get_recording_analytics",
        "generate_recording_analytics",
        "generate_recording_ai_analytics",
    ],
)
def test_final_routes_refuse_in_flight_before_work(
    status: RecordingStatus, handler: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    recording = Recording(id=1, name="Live", audio_path="", user_id=1, status=status)
    transcript = Transcript(recording_id=1)

    async def owned(*args: Any) -> Recording:
        return recording

    async def get_transcript(*args: Any) -> Transcript:
        return transcript

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Final work reached during capture")

    monkeypatch.setattr(routes_analytics, "_get_owned_recording", owned)
    monkeypatch.setattr(routes_analytics, "_get_recording_transcript", get_transcript)
    for name in (
        "read_recording_analytics",
        "dispatch_task_best_effort",
        "claim_run",
        "get_budget",
    ):
        monkeypatch.setattr(routes_analytics, name, forbidden)
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            getattr(routes_analytics, handler)(
                "live-rec", None, User(id=1, username="alice", hashed_password="x")
            )
        )
    assert error.value.status_code == 409
