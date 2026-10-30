"""A speaker chosen for a line goes to the final words it covers, end to end.

The live cases make the choice through the API, then run the real speaker
assignment (live-label carry-over included) and canonical finalisation, and
read the result back through the API. One runs the complete final task with
only Whisper and pyannote faked. Settled and processed cases run on SQLite and
PostgreSQL. Names and text are made up.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import Mock

import huggingface_hub
import numpy as np
import pytest
import soundfile as sf
import whisper
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pyannote.core import Annotation, Segment
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import Session, select
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.core import db as core_db
from backend.core.db import driver_urls
from backend.models.pipeline import TranscriptUtterance
from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.processing import transcribe as transcription_dispatch
from backend.processing.engines import whisper_engine
from backend.tests.canonical_engine import _recording, _transcript
from backend.tests.canonical_engine import (
    publication_engine as publication_engine,
)
from backend.tests.live_naming_fixtures import ONE, TWO, seed_live_lines
from backend.tests.postgres_setup import fresh_schema
from backend.tests.test_live_settling_publication import _request
from backend.utils.canonical_pipeline import (
    finalize_utterances_from_segments,
    refresh_transcript_projection_from_canonical,
    serialize_canonical_utterances,
    update_utterance_speaker,
    update_utterance_text,
)
from backend.utils.canonical_pipeline.live_settling import (
    close_live_settling,
    publish_live_settling,
)
from backend.utils.config_manager import config_manager
from backend.utils.word_text import reconstruct_text_from_words
from backend.worker.tasks import speaker_assignment
from backend.worker.tasks.pipeline import process_recording_task

SCHEMA = "manual_speaker_carry_test"
Layout = list[tuple[float, float, str, bool]]

# A 2 s line inside one voice's 20.4 s turn, picked for the other voice.
SHORT_PICK: Layout = [
    (0.0, 8.0, ONE, True),
    (8.0, 10.0, ONE, True),
    (10.0, 20.4, ONE, True),
    (21.0, 25.0, TWO, True),
    (26.0, 30.0, TWO, True),
]
SHORT_PICK_FINAL = [
    (0.0, 20.4, "SPEAKER_00"),
    (21.0, 25.0, "SPEAKER_01"),
    (26.0, 30.0, "SPEAKER_01"),
]
# A pick covering 9 s of a 10 s final segment.
LONG_PICK: Layout = [
    (0.0, 1.0, ONE, True),
    (1.0, 10.0, ONE, True),
    (12.0, 16.0, TWO, True),
    (17.0, 20.0, TWO, True),
]
LONG_PICK_FINAL = [
    (0.0, 10.0, "SPEAKER_00"),
    (12.0, 16.0, "SPEAKER_01"),
    (17.0, 20.0, "SPEAKER_01"),
]
# A live mislabel the final pass already corrects: the pick agrees with it.
AGREEING_PICK: Layout = [
    (0.0, 4.0, TWO, True),
    (4.0, 6.0, ONE, True),
    (6.0, 12.0, TWO, True),
    (14.0, 20.0, ONE, True),
]
AGREEING_PICK_FINAL = [(0.0, 12.0, "SPEAKER_01"), (14.0, 20.0, "SPEAKER_00")]


def _words(start: float, end: float, prefix: str) -> list[dict[str, Any]]:
    """0.4 s words every 0.5 s, the first 50 ms after ``start``."""
    words, index = [], 0
    while start + 0.5 * index + 0.45 <= end + 1e-9:
        at = round(start + 0.05 + 0.5 * index, 3)
        words.append(
            {
                "word": f" {prefix}{index}",
                "start": at,
                "end": round(at + 0.4, 3),
                "probability": 0.95,
            }
        )
        index += 1
    return words


def _final(
    layout: list[tuple[float, float, str]], *, timed: bool
) -> list[dict[str, Any]]:
    segments = []
    for number, (start, end, speaker) in enumerate(layout):
        words = _words(start, end, "abcdefgh"[number])
        segment: dict[str, Any] = {
            "start": start,
            "end": end,
            "speaker": speaker,
            "text": reconstruct_text_from_words(words),
        }
        if timed:
            segment["words"] = words
        segments.append(segment)
    return segments


def _summary(lines: list[dict[str, Any]]) -> list[tuple[float, float, str, bool]]:
    return [
        (line["start"], line["end"], line["speaker"], line["speaker_manually_edited"])
        for line in lines
    ]


@pytest.fixture
async def live_api(
    request: pytest.FixtureRequest,
    postgres_test_url: str,
    stub_meeting_edge_dispatch: Any,
) -> AsyncIterator[tuple[Any, ...]]:
    """A live capture with settled, locked lines laid out as ``request.param``."""
    importlib.import_module("backend.models.registry")
    engine = create_async_engine(
        driver_urls(postgres_test_url)[0],
        execution_options={"schema_translate_map": {None: SCHEMA}},
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
    )
    async with engine.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.run_sync(lambda sync: fresh_schema(sync, SCHEMA))
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        user = User(username="erin", hashed_password="x", force_password_change=False)
        db.add(user)
        await db.flush()
        recording = Recording(
            name="Example live meeting",
            audio_path="/data/live.wav",
            user_id=user.id,
            status=RecordingStatus.UPLOADING,
            pipeline_generation="unified",
        )
        db.add(recording)
        await db.flush()
        db.add(
            Transcript(
                recording_id=recording.id, text="", settling_state={"settled": True}
            )
        )
        await db.commit()
        await db.run_sync(
            lambda sync: seed_live_lines(sync, recording.id, request.param)
        )
        for row in (await db.exec(select(TranscriptUtterance))).all():
            row.confidence_payload = {
                **(row.confidence_payload or {}),
                "live_text_settling": {
                    "version": 1,
                    "line_locked": True,
                    "text_locked": True,
                },
            }
        await db.run_sync(refresh_transcript_projection_from_canonical, recording.id)
        await db.commit()
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    async def database() -> AsyncIterator[AsyncSession]:
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client, sessions, recording
    finally:
        await engine.dispose()


async def _read(
    client: AsyncClient, recording: Recording
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    response = await client.get(f"/api/v1/transcripts/{recording.public_id}/utterances")
    assert response.status_code == 200, response.text
    return response.json()["utterances"], response.json()["speakers"]


async def _pick(client: AsyncClient, recording: Recording, index: int) -> None:
    """Change speaker on live line ``index`` to the live speaker ``TWO``."""
    line = (await _read(client, recording))[0][index]
    assert line["locked"] is True
    response = await client.patch(
        f"/api/v1/transcripts/{recording.public_id}/utterances/{line['id']}/speaker",
        json={
            "new_speaker_name": "Speaker 2",
            "diarization_label": TWO,
            "scope": "utterance_only",
            "expected_revision": line["revision"],
        },
    )
    assert response.status_code == 200, response.text


async def _publish(
    sessions: Any, recording: Recording, final: list[dict[str, Any]]
) -> None:
    """Speaker assignment and canonical finalisation, as the final task runs them."""

    def publish(sync: Any) -> None:
        current = sync.get(Recording, recording.id)
        current.status = RecordingStatus.PROCESSING
        speaker_assignment.assign_and_identify_speakers(
            SimpleNamespace(session=sync, merged_config={}, recording_id=recording.id),
            current,
            final,
            None,
        )
        finalize_utterances_from_segments(
            sync, recording_id=recording.id, segments=final
        )
        refresh_transcript_projection_from_canonical(sync, recording.id)
        current.status = RecordingStatus.PROCESSED

    async with sessions() as db:
        await db.run_sync(publish)
        await db.commit()


def _labels(speakers: list[dict[str, Any]]) -> set[str]:
    return {speaker["diarization_label"] for speaker in speakers}


@pytest.mark.anyio
@pytest.mark.parametrize("live_api", [SHORT_PICK], indirect=True)
@pytest.mark.parametrize("timed", [True, False], ids=["timed", "untimed"])
async def test_a_short_pick_inside_a_long_final_line_leaves_the_other_voice(
    live_api: tuple[Any, ...], timed: bool
) -> None:
    client, sessions, recording = live_api
    await _pick(client, recording, 1)
    await _publish(sessions, recording, _final(SHORT_PICK_FINAL, timed=timed))
    lines, speakers = await _read(client, recording)
    if timed:
        assert _summary(lines) == [
            (0.0, 8.05, "SPEAKER_00", False),
            (8.05, 10.05, "SPEAKER_01", True),
            (10.05, 20.4, "SPEAKER_00", False),
            (21.0, 25.0, "SPEAKER_01", False),
            (26.0, 30.0, "SPEAKER_01", False),
        ]
        picked = lines[1]
        assert picked["text"] == "a16 a17 a18 a19"
        assert picked["speaker_edit_source"] == "api"
        assert picked["recording_speaker_id"] == lines[3]["recording_speaker_id"]
        assert lines[0]["recording_speaker_id"] == lines[2]["recording_speaker_id"]
    else:
        # 2 s of a 20.4 s segment with no word timings is no majority.
        assert _summary(lines) == [
            (0.0, 20.4, "SPEAKER_00", False),
            (21.0, 25.0, "SPEAKER_01", False),
            (26.0, 30.0, "SPEAKER_01", False),
        ]
    assert _labels(speakers) == {"SPEAKER_00", "SPEAKER_01"}


@pytest.mark.anyio
@pytest.mark.parametrize("live_api", [LONG_PICK], indirect=True)
@pytest.mark.parametrize("timed", [True, False], ids=["timed", "untimed"])
async def test_a_pick_covering_most_of_a_final_line_takes_only_its_words(
    live_api: tuple[Any, ...], timed: bool
) -> None:
    client, sessions, recording = live_api
    await _pick(client, recording, 1)
    await _publish(sessions, recording, _final(LONG_PICK_FINAL, timed=timed))
    lines, _ = await _read(client, recording)
    rest = [(12.0, 16.0, "SPEAKER_01", False), (17.0, 20.0, "SPEAKER_01", False)]
    if timed:
        assert _summary(lines) == [
            (0.0, 1.05, "SPEAKER_00", False),
            (1.05, 10.0, "SPEAKER_01", True),
            *rest,
        ]
        assert lines[0]["text"] == "a0 a1"
    else:
        # 9 s of a 10 s segment with no word timings is a majority.
        assert _summary(lines) == [(0.0, 10.0, "SPEAKER_01", True), *rest]
    assert lines[1]["recording_speaker_id"] == lines[-1]["recording_speaker_id"]


@pytest.mark.anyio
@pytest.mark.parametrize("live_api", [AGREEING_PICK], indirect=True)
@pytest.mark.parametrize("timed", [True, False], ids=["timed", "untimed"])
async def test_a_pick_the_final_pass_agrees_with_keeps_its_final_line_whole(
    live_api: tuple[Any, ...], timed: bool
) -> None:
    client, sessions, recording = live_api
    await _pick(client, recording, 1)
    await _publish(sessions, recording, _final(AGREEING_PICK_FINAL, timed=timed))
    lines, _ = await _read(client, recording)
    assert _summary(lines) == [
        (0.0, 12.0, "SPEAKER_01", True),
        (14.0, 20.0, "SPEAKER_00", False),
    ]
    assert lines[0]["speaker_edit_source"] == "api"


@pytest.mark.anyio
@pytest.mark.parametrize("live_api", [SHORT_PICK], indirect=True)
@pytest.mark.parametrize("final", ["timed", "untimed", "exact", "edited"])
async def test_a_pick_leaves_no_live_label_when_processing_finds_no_speakers(
    live_api: tuple[Any, ...], final: str
) -> None:
    # Diarisation off or failed: every final segment is UNKNOWN, so the live
    # speaker the line was moved to has no processed speaker to go to.
    client, sessions, recording = live_api
    if final == "edited":
        line = (await _read(client, recording))[0][1]
        edited = await client.patch(
            f"/api/v1/transcripts/{recording.public_id}/utterances/{line['id']}/text",
            json={"text": "Edited words", "expected_revision": line["revision"]},
        )
        assert edited.status_code == 200, edited.text
    await _pick(client, recording, 1)
    if final == "exact":
        layout = [(0.0, 8.0), (8.0, 10.0), (10.0, 20.4), (21.0, 25.0), (26.0, 30.0)]
    else:
        layout = [(0.0, 20.4), (21.0, 25.0), (26.0, 30.0)]
    segments = _final(
        [(start, end, "UNKNOWN") for start, end in layout],
        timed=final in ("timed", "edited"),
    )
    await _publish(sessions, recording, segments)
    lines, speakers = await _read(client, recording)
    if final == "edited":
        # The edited line keeps its words; the final words beside it stay.
        layout = [(0.05, 7.95), (8.0, 10.0), (10.05, 19.95), *layout[1:]]
        assert lines[1]["text"] == "Edited words"
        assert lines[1]["text_manually_edited"] is True
    # Like every other line of a meeting processed without speakers.
    assert _summary(lines) == [(start, end, "UNKNOWN", False) for start, end in layout]
    assert all(line["recording_speaker_id"] is None for line in lines)
    assert speakers == []


@contextmanager
def _fresh_diarisation_module() -> Iterator[None]:
    """Load the real wrapper against this test's temporary third-party export."""
    name = "backend.processing.diarize"
    package = importlib.import_module("backend.processing")
    missing = object()
    previous_module = sys.modules.get(name, missing)
    previous_export = getattr(package, "diarize", missing)
    sys.modules.pop(name, None)
    try:
        importlib.import_module(name)
        yield
    finally:
        if previous_module is missing:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous_module
        if previous_export is missing:
            package.__dict__.pop("diarize", None)
        else:
            package.diarize = previous_export


@pytest.fixture
def final_task(monkeypatch: pytest.MonkeyPatch, stub_celery_dispatch: Any) -> Any:
    """Whisper with word timings and pyannote are the only fakes in the final task."""
    segments = _final(SHORT_PICK_FINAL, timed=True)
    model = Mock(is_multilingual=False)
    model.transcribe.return_value = {
        "text": " ".join(segment["text"] for segment in segments),
        "segments": [
            {key: segment[key] for key in ("start", "end", "text", "words")}
            for segment in segments
        ],
        "language": "en",
    }
    annotation = Annotation()
    for segment in segments:
        annotation[Segment(segment["start"], segment["end"])] = segment["speaker"]
    monkeypatch.setattr(whisper_engine, "_model_cache", {})
    monkeypatch.setattr(transcription_dispatch, "_ENGINE_REGISTRY", {})
    monkeypatch.setattr(whisper, "load_model", lambda *args, **kwargs: model)
    audio_api = ModuleType("pyannote.audio")
    audio_api.Pipeline = SimpleNamespace(
        from_pretrained=lambda *args, **kwargs: Mock(return_value=annotation)
    )
    monkeypatch.setitem(sys.modules, "pyannote.audio", audio_api)
    monkeypatch.setattr(huggingface_hub, "login", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        process_recording_task, "update_state", lambda *args, **kwargs: None
    )
    tasks = importlib.import_module("backend.worker.tasks")
    for name in (
        "index_transcript_task",
        "compute_delivery_analytics_task",
        "compute_overlap_analytics_task",
    ):
        monkeypatch.setattr(
            getattr(tasks, name),
            "apply_async",
            lambda *args, **kwargs: SimpleNamespace(id="test-dispatch"),
        )
    monkeypatch.setattr(
        config_manager,
        "config",
        {
            **config_manager.config,
            "processing_device": "cpu",
            "transcription_backend": "whisper",
            "transcription_language": "en",
            "whisper_model_size": "tiny",
            "enable_vad": False,
            "enable_diarization": True,
            "enable_auto_voiceprints": False,
            "speaker_engine": "classic",
            "phantom_max_duration_s": 1.0,
            "llm_provider": "openai",
            "openai_api_key": "offline-test-key",
            "openai_model": "offline-test-model",
            "hf_token": "offline-test-token",
        },
    )
    with _fresh_diarisation_module():
        yield stub_celery_dispatch


@pytest.mark.anyio
@pytest.mark.parametrize("live_api", [SHORT_PICK], indirect=True)
async def test_a_short_pick_survives_the_complete_final_task_without_the_rest(
    live_api: tuple[Any, ...],
    postgres_test_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    final_task: Any,
) -> None:
    client, sessions, recording = live_api
    await _pick(client, recording, 1)
    audio = tmp_path / "meeting.wav"
    sf.write(audio, 0.1 * np.sin(np.arange(31 * 16000) * 0.08), 16000)
    engine = create_engine(
        driver_urls(postgres_test_url)[1],
        connect_args={"options": f"-csearch_path={SCHEMA},public"},
        execution_options={"schema_translate_map": {None: SCHEMA}},
    )
    monkeypatch.setattr(core_db, "sync_engine", engine)
    try:
        async with sessions() as db:

            def finish(sync: Any) -> Any:
                current = sync.get(Recording, recording.id)
                current.audio_path = str(audio)
                current.status = RecordingStatus.QUEUED
                sync.commit()
                monkeypatch.setattr(process_recording_task, "_session", sync)
                return process_recording_task.run(recording.id)

            result = await db.run_sync(finish)
            assert result == {"status": "success", "recording_id": recording.id}
    finally:
        engine.dispose()
    lines, speakers = await _read(client, recording)
    words = {word.strip(): line for line in lines for word in line["text"].split(" ")}
    picked = {f"a{index}" for index in range(16, 20)}
    first_voice = {f"a{index}" for index in range(40)} - picked
    assert {words[word]["id"] for word in picked} == {words["a16"]["id"]}
    assert words["a16"]["text"] == "a16 a17 a18 a19"
    assert words["a16"]["speaker_manually_edited"] is True
    assert words["a16"]["recording_speaker_id"] == words["b0"]["recording_speaker_id"]
    assert all(
        words[word]["speaker"] == words["a0"]["speaker"]
        and not words[word]["speaker_manually_edited"]
        for word in first_voice
    )
    assert words["a0"]["recording_speaker_id"] != words["b0"]["recording_speaker_id"]
    assert len(_labels(speakers)) == 2


def _settled_capture(session: Session, words: list[dict[str, Any]]) -> int:
    recording_id = _recording(session)
    transcript = _transcript(session, recording_id)
    transcript.settling_state = {
        "version": 1,
        "eligible": True,
        "generation": "manual-carry-test",
        "capture_epoch": 0,
        "sealed": False,
        "phase": "live",
        "policy": {"enabled": True},
    }
    session.add(transcript)
    session.commit()
    assert publish_live_settling(
        session, request=_request(session, recording_id, 0, end_ms=8000), words=words
    ).accepted
    session.commit()
    assert close_live_settling(session, recording_id=recording_id)
    session.commit()
    return recording_id


def _settling_words(*runs: tuple[int, str]) -> list[dict[str, Any]]:
    """Three 300 ms words from each run's start, 100 ms apart."""
    return [
        {
            "word": word,
            "start_ms": start + 400 * index,
            "end_ms": start + 400 * index + 300,
        }
        for start, run in runs
        for index, word in enumerate(run.split("|"))
    ]


def test_choices_on_settled_lines_keep_their_words_and_leave_the_next_line(
    publication_engine: Engine,
) -> None:
    runs = (
        (100, "Opening| words| here"),
        (3000, " Carol| speaks| briefly"),
        (5900, " Then| someone| else"),
    )
    with Session(publication_engine) as session:
        recording_id = _settled_capture(session, _settling_words(*runs))
        opening, carol, _then = serialize_canonical_utterances(session, recording_id)
        update_utterance_text(
            session,
            recording_id=recording_id,
            utterance_public_id=opening["id"],
            text="Opening words, corrected",
        )
        _, dana = update_utterance_speaker(
            session,
            recording_id=recording_id,
            utterance_public_id=opening["id"],
            new_speaker_name="Dana Example",
        )
        _, carol_speaker = update_utterance_speaker(
            session,
            recording_id=recording_id,
            utterance_public_id=carol["id"],
            new_speaker_name="Carol Example",
        )
        assert dana is not None and carol_speaker is not None
        dana_id, carol_id = dana.id, carol_speaker.id
        session.commit()
        words = [
            {
                "word": word["word"],
                "start": word["start_ms"] / 1000,
                "end": word["end_ms"] / 1000,
            }
            for word in _settling_words(*runs)
        ]
        final = {
            "start": 0.1,
            "end": 7.0,
            "speaker": "SPEAKER_00",
            "text": reconstruct_text_from_words(words),
            "words": words,
        }
        finalize_utterances_from_segments(
            session, recording_id=recording_id, segments=[final]
        )
        refresh_transcript_projection_from_canonical(session, recording_id)
        session.commit()
        session.expunge_all()
        lines = serialize_canonical_utterances(session, recording_id)
    assert [
        (line["text"], line["recording_speaker_id"], line["speaker_manually_edited"])
        for line in lines
    ] == [
        ("Opening words, corrected", dana_id, True),
        ("Carol speaks briefly", carol_id, True),
        ("Then someone else", lines[2]["recording_speaker_id"], False),
    ]
    assert lines[0]["text_manually_edited"] is True
    assert lines[0]["final_review"]["text"] == "Opening words here"
    assert lines[2]["speaker"] == "SPEAKER_00"
    assert lines[2]["recording_speaker_id"] not in (dana_id, carol_id)
    assert [word["word"].strip() for word in lines[1]["words"]] == [
        "Carol",
        "speaks",
        "briefly",
    ]


PROCESSED = [
    (0.0, 5.0, "SPEAKER_00"),
    (5.5, 10.0, "SPEAKER_01"),
    (10.5, 15.0, "SPEAKER_00"),
]


def _processed_meeting(session: Session, settled: bool) -> tuple[int, int]:
    """A processed meeting whose middle line was moved, whole, to Carol Example."""
    recording_id = _recording(session)
    if settled:
        transcript = _transcript(session, recording_id)
        transcript.settling_state = {"settled": True}
        session.add(transcript)
        session.commit()
    finalize_utterances_from_segments(
        session, recording_id=recording_id, segments=_final(PROCESSED, timed=True)
    )
    refresh_transcript_projection_from_canonical(session, recording_id)
    session.commit()
    middle = serialize_canonical_utterances(session, recording_id)[1]
    _, carol = update_utterance_speaker(
        session,
        recording_id=recording_id,
        utterance_public_id=middle["id"],
        new_speaker_name="Carol Example",
        expected_revision=middle["revision"],
    )
    assert carol is not None and carol.id is not None
    session.commit()
    return recording_id, carol.id


def _reprocess(
    session: Session, recording_id: int, segments: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    finalize_utterances_from_segments(
        session,
        recording_id=recording_id,
        segments=segments,
        idempotency_key="reprocess",
    )
    refresh_transcript_projection_from_canonical(session, recording_id)
    session.commit()
    session.expunge_all()
    return serialize_canonical_utterances(session, recording_id)


@pytest.mark.parametrize("settled", [True, False], ids=["settled", "unsettled"])
@pytest.mark.parametrize("drift", [False, True], ids=["same", "drifted"])
def test_a_whole_line_correction_on_a_processed_meeting_keeps_its_line(
    publication_engine: Engine, settled: bool, drift: bool
) -> None:
    with Session(publication_engine) as session:
        recording_id, carol_id = _processed_meeting(session, settled)
        segments = _final(PROCESSED, timed=True)
        if drift:
            segments[1] = {**segments[1], "start": 5.45, "end": 10.1}
        lines = _reprocess(session, recording_id, segments)
    assert [
        (line["start"], line["end"], line["speaker_manually_edited"]) for line in lines
    ] == [
        (0.0, 5.0, False),
        (5.45 if drift else 5.5, 10.1 if drift else 10.0, True),
        (10.5, 15.0, False),
    ]
    assert [line["recording_speaker_id"] == carol_id for line in lines] == [
        False,
        True,
        False,
    ]
    assert lines[1]["text"] == segments[1]["text"]
    assert lines[0]["speaker"] == lines[2]["speaker"] == "SPEAKER_00"


@pytest.mark.parametrize("settled", [True, False], ids=["settled", "unsettled"])
def test_a_processed_correction_stops_at_its_words_when_a_reprocess_joins_lines(
    publication_engine: Engine, settled: bool
) -> None:
    with Session(publication_engine) as session:
        recording_id, carol_id = _processed_meeting(session, settled)
        first, middle, last = _final(PROCESSED, timed=True)
        joined = {
            **middle,
            "end": last["end"],
            "words": middle["words"] + last["words"],
            "text": f"{middle['text']} {last['text']}",
        }
        lines = _reprocess(session, recording_id, [first, joined])
    assert [
        (line["start"], line["end"], line["text"], line["speaker_manually_edited"])
        for line in lines
    ] == [
        (0.0, 5.0, first["text"], False),
        (5.5, 10.55, middle["text"], True),
        (10.55, 15.0, last["text"], False),
    ]
    assert lines[1]["recording_speaker_id"] == carol_id
    assert lines[2]["speaker"] == "SPEAKER_01"
