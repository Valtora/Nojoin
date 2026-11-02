"""Live label choices cross the API and survive actual final publication."""

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
from sqlalchemy import create_engine, func, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.core import db as core_db
from backend.core.db import driver_urls
from backend.models.pipeline import TranscriptUtterance
from backend.models.recording import Recording, RecordingStatus
from backend.models.speaker import RecordingSpeaker
from backend.models.speaker_name_event import SpeakerNameEvent
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.processing import transcribe as transcription_dispatch
from backend.processing.engines import whisper_engine
from backend.tests.live_naming_fixtures import ONE, THREE, TWO, seed_live_lines
from backend.tests.postgres_setup import fresh_schema
from backend.utils.canonical_pipeline import (
    finalize_utterances_from_segments,
    refresh_transcript_projection_from_canonical,
)
from backend.utils.config_manager import config_manager
from backend.worker.tasks import speaker_assignment
from backend.worker.tasks.pipeline import process_recording_task

pytestmark = pytest.mark.anyio
SCHEMA = "live_speaker_assignment_test"
LIVE = [
    (0.0, 10.0, ONE, True),
    (12.0, 14.0, TWO, True),
    (15.0, 17.0, TWO, True),
    (20.0, 24.0, THREE, True),
]


@pytest.fixture
async def live_api(
    postgres_test_url: str, stub_meeting_edge_dispatch: Any
) -> AsyncIterator[tuple[Any, ...]]:
    importlib.import_module("backend.models.registry")
    engine = create_async_engine(
        driver_urls(postgres_test_url)[0],
        execution_options={
            "schema_translate_map": {None: SCHEMA},
        },
        connect_args={
            "server_settings": {"search_path": f"{SCHEMA},public"},
        },
    )
    async with engine.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.run_sync(lambda sync: fresh_schema(sync, SCHEMA))
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as db:
        user = User(username="alice", hashed_password="x", force_password_change=False)
        db.add(user)
        await db.flush()
        recording = Recording(
            name="Live meeting",
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
        await db.run_sync(lambda sync: seed_live_lines(sync, recording.id, LIVE))
        rows = (await db.execute(select(TranscriptUtterance))).scalars().all()
        for row in rows:
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


async def _assign(
    client: AsyncClient, recording: Recording, *, named: bool
) -> dict[str, Any]:
    if named:
        response = await client.put(
            f"/api/v1/speakers/recordings/{recording.public_id}/live/{TWO}",
            json={"name": "Alex"},
        )
        assert response.status_code == 200, response.text
    read = await client.get(f"/api/v1/transcripts/{recording.public_id}/utterances")
    assert read.status_code == 200, read.text
    line = read.json()["utterances"][0]
    assert line["locked"] is True and read.json()["settling_required"] is True
    response = await client.patch(
        f"/api/v1/transcripts/{recording.public_id}/utterances/{line['id']}/speaker",
        json={
            "new_speaker_name": "Alex" if named else "Speaker 2",
            "diarization_label": TWO,
            "scope": "utterance_only",
            "expected_revision": line["revision"],
        },
    )
    assert response.status_code == 200, response.text
    return line


@pytest.mark.parametrize("named", [False, True], ids=["unnamed", "named"])
async def test_live_assignment_api_retains_identity_and_rejects_stale_or_absent_targets(
    live_api: tuple[Any, ...],
    named: bool,
) -> None:
    client, sessions, recording = live_api
    original = await _assign(client, recording, named=named)
    if named:
        same_name = await client.put(
            f"/api/v1/speakers/recordings/{recording.public_id}/live/{THREE}",
            json={"name": "Alex"},
        )
        assert same_name.status_code == 200, same_name.text
    url = f"/api/v1/transcripts/{recording.public_id}/utterances"
    read = (await client.get(url)).json()
    changed, reference = read["utterances"][:2]
    assert (changed["speaker"], changed["recording_speaker_id"]) == (TWO, None)
    assert changed["revision"] == original["revision"] + 1
    assert changed["speaker_manually_edited"] is True
    assert changed["speaker_assignment_source"] == "manual"
    assert changed["speaker_assignment_authority"] == "manual"
    assert changed["speaker_edit_source"] == "api"
    assert reference["speaker"] == TWO and reference["speaker_manually_edited"] is False
    assert read["live_speaker_names"] == ({TWO: "Alex", THREE: "Alex"} if named else {})
    for target, revision, status in [
        (THREE, original["revision"], 409),
        ("LIVE_PROVISIONAL_9", changed["revision"], 404),
    ]:
        refused = await client.patch(
            f"{url}/{changed['id']}/speaker",
            json={
                "new_speaker_name": "Other",
                "diarization_label": target,
                "scope": "utterance_only",
                "expected_revision": revision,
            },
        )
        assert refused.status_code == status, refused.text
    async with sessions() as db:
        row = (
            await db.execute(
                select(TranscriptUtterance).where(
                    TranscriptUtterance.public_id == changed["id"]
                )
            )
        ).scalar_one()
        assert row.speaker_label == TWO and row.manual_speaker_locked
        assert row.revision == changed["revision"]
        assert (
            await db.execute(select(func.count(RecordingSpeaker.id)))
        ).scalar_one() == 0
        assert (
            await db.execute(select(func.count(SpeakerNameEvent.id)))
        ).scalar_one() == 2 * int(named)


@pytest.mark.parametrize("named", [False, True], ids=["unnamed", "named"])
@pytest.mark.parametrize("drift", [False, True], ids=["exact", "drift"])
async def test_a_corrected_line_follows_the_targets_other_speech_through_finalisation(
    live_api: tuple[Any, ...],
    named: bool,
    drift: bool,
) -> None:
    client, sessions, recording = live_api
    await _assign(client, recording, named=named)
    # With no diarisation object, the real extractor has no voiceprints to
    # learn. Name placement, merges, canonical reconciliation and reads execute.
    final = [
        {
            "start": 0.2 if drift else 0.0,
            "end": 10.2 if drift else 10.0,
            "speaker": "SPEAKER_00",
            "text": "Final corrected line.",
        },
        {
            "start": 12.0,
            "end": 14.0,
            "speaker": "SPEAKER_01",
            "text": "Target reference.",
        },
        {"start": 15.0, "end": 17.0, "speaker": "SPEAKER_01", "text": "Target again."},
        {"start": 20.0, "end": 24.0, "speaker": "SPEAKER_02", "text": "Another voice."},
    ]
    async with sessions() as db:

        def publish(sync: Any) -> None:
            current = sync.get(Recording, recording.id)
            current.status = RecordingStatus.PROCESSING
            speaker_assignment.assign_and_identify_speakers(
                SimpleNamespace(
                    session=sync, merged_config={}, recording_id=recording.id
                ),
                current,
                final,
                None,
            )
            finalize_utterances_from_segments(
                sync, recording_id=recording.id, segments=final
            )
            refresh_transcript_projection_from_canonical(sync, recording.id)
            current.status = RecordingStatus.PROCESSED

        await db.run_sync(publish)
        await db.commit()
    response = await client.get(f"/api/v1/transcripts/{recording.public_id}/utterances")
    assert response.status_code == 200, response.text
    corrected, first, second, other = response.json()["utterances"]
    assert corrected["speaker"] == first["speaker"] == second["speaker"] == "SPEAKER_01"
    assert (
        corrected["recording_speaker_id"]
        == first["recording_speaker_id"]
        == second["recording_speaker_id"]
    )
    assert corrected["recording_speaker_id"] != other["recording_speaker_id"]
    assert corrected["text"] == "Final corrected line."
    assert (
        corrected["speaker_manually_edited"] is True
        and corrected["speaker_edit_source"] == "api"
    )
    assert response.json()["live_speaker_names"] == {}
    if named:
        target = next(
            row
            for row in response.json()["speakers"]
            if row["diarization_label"] == "SPEAKER_01"
        )
        assert target["local_name"] == "Alex"
        assert (
            sum(row["local_name"] == "Alex" for row in response.json()["speakers"]) == 1
        )


async def _finalise(
    sessions: Any,
    recording: Recording,
    final: list[dict],
    voiceprints: dict[str, Any] | None = None,
) -> None:
    """The final pass's speaker stage and canonical finalisation, as it runs them.

    ``voiceprints`` stands in for extraction, which needs the audio.
    """
    async with sessions() as db:

        def publish(sync: Any) -> None:
            current = sync.get(Recording, recording.id)
            current.status = RecordingStatus.PROCESSING
            speaker_assignment.assign_and_identify_speakers(
                SimpleNamespace(
                    session=sync,
                    merged_config={},
                    recording_id=recording.id,
                    speaker_voiceprints=voiceprints,
                ),
                current,
                final,
                None,
            )
            finalize_utterances_from_segments(
                sync, recording_id=recording.id, segments=final
            )
            refresh_transcript_projection_from_canonical(sync, recording.id)
            current.status = RecordingStatus.PROCESSED

        await db.run_sync(publish)
        await db.commit()


def _assert_no_live_label_survives(body: dict[str, Any]) -> None:
    assert not [row for row in body["utterances"] if "LIVE_" in row["speaker"]]
    assert not [row for row in body["speakers"] if "LIVE_" in row["diarization_label"]]


@pytest.mark.parametrize("named", [False, True], ids=["unnamed", "named"])
@pytest.mark.parametrize(
    ("reference", "holder"), [("split", "SPEAKER_01"), ("missing", "SPEAKER_00")]
)
async def test_an_unclear_live_identity_sends_the_line_where_its_name_goes(
    live_api: tuple[Any, ...], named: bool, reference: str, holder: str
) -> None:
    client, sessions, recording = live_api
    await _assign(client, recording, named=named)
    final = [
        {"start": 0.2, "end": 10.2, "speaker": "SPEAKER_00", "text": "Final words."}
    ]
    if reference == "split":
        final.extend(
            [
                {
                    "start": 12.0,
                    "end": 14.0,
                    "speaker": "SPEAKER_01",
                    "text": "One holder.",
                },
                {
                    "start": 15.0,
                    "end": 17.0,
                    "speaker": "SPEAKER_02",
                    "text": "Equal holder.",
                },
            ]
        )
    await _finalise(sessions, recording, final)
    response = await client.get(f"/api/v1/transcripts/{recording.public_id}/utterances")
    assert response.status_code == 200, response.text
    body = response.json()
    corrected = body["utterances"][0]
    target = next(row for row in body["speakers"] if row["diarization_label"] == holder)
    # Split evenly, the top holder (first in time) takes the line, as it takes
    # the name; with none of the label's speech heard, the nearest speaker.
    assert corrected["speaker"] == holder
    assert corrected["recording_speaker_id"] == target["id"]
    assert corrected["speaker_manually_edited"] is True
    assert corrected["speaker_edit_source"] == "api"
    _assert_no_live_label_survives(body)
    if named:
        assert target["local_name"] == "Alex" and target["name_pending"] is True
        assert sum(row["local_name"] == "Alex" for row in body["speakers"]) == 1


@pytest.mark.parametrize("named", [False, True], ids=["unnamed", "named"])
async def test_a_live_speaker_with_no_speech_of_its_own_places_its_moved_lines(
    live_api: tuple[Any, ...], named: bool
) -> None:
    client, sessions, recording = live_api
    url = f"/api/v1/transcripts/{recording.public_id}/utterances"
    if named:
        # Anchored on Speaker 3's own line, which is then moved away.
        response = await client.put(
            f"/api/v1/speakers/recordings/{recording.public_id}/live/{THREE}",
            json={"name": "Carol"},
        )
        assert response.status_code == 200, response.text
    lines = (await client.get(url)).json()["utterances"]
    # The first line goes to Speaker 3, then Speaker 3's own line goes to
    # Speaker 2: nothing of Speaker 3's own speech is left.
    for index, target in [(0, THREE), (3, TWO)]:
        moved = await client.patch(
            f"{url}/{lines[index]['id']}/speaker",
            json={
                "new_speaker_name": "Speaker",
                "diarization_label": target,
                "scope": "utterance_only",
                "expected_revision": lines[index]["revision"],
            },
        )
        assert moved.status_code == 200, moved.text
    final = [
        {"start": 0.2, "end": 10.2, "speaker": "SPEAKER_00", "text": "Own speech."},
        {"start": 12.0, "end": 14.0, "speaker": "SPEAKER_01", "text": "Two."},
        {"start": 15.0, "end": 17.0, "speaker": "SPEAKER_01", "text": "Two again."},
        {"start": 20.0, "end": 24.0, "speaker": "SPEAKER_02", "text": "Moved to two."},
    ]
    await _finalise(sessions, recording, final)
    body = (await client.get(url)).json()
    by_text = {row["text"]: row for row in body["utterances"]}
    # Unnamed, the line keeps the final speaker of its own words. Named, it
    # goes where the name goes: nearest the name's saved turn, as pending.
    own = "SPEAKER_02" if named else "SPEAKER_00"
    assert by_text["Own speech."]["speaker"] == own
    if named:
        target = next(
            row for row in body["speakers"] if row["diarization_label"] == own
        )
        assert target["local_name"] == "Carol" and target["name_pending"] is True
        assert sum(row["local_name"] == "Carol" for row in body["speakers"]) == 1
    assert by_text["Moved to two."]["speaker"] == "SPEAKER_01"
    assert (
        by_text["Moved to two."]["recording_speaker_id"]
        == by_text["Two."]["recording_speaker_id"]
    )
    assert all(
        by_text[text]["speaker_manually_edited"]
        for text in ("Own speech.", "Moved to two.")
    )
    _assert_no_live_label_survives(body)


async def test_a_pending_name_and_its_lines_follow_a_merged_away_holder(
    live_api: tuple[Any, ...],
) -> None:
    client, sessions, recording = live_api
    await _assign(client, recording, named=True)
    # Speaker 2's speech is split 1.8 s / 2.2 s, so Alex is pending on the top
    # holder, SPEAKER_02; its voice matches SPEAKER_01, which absorbs it.
    final = [
        {"start": 0.2, "end": 10.2, "speaker": "SPEAKER_00", "text": "Moved line."},
        {"start": 12.0, "end": 13.8, "speaker": "SPEAKER_01", "text": "Survivor."},
        {"start": 13.8, "end": 14.0, "speaker": "SPEAKER_02", "text": "Holder."},
        {"start": 15.0, "end": 17.0, "speaker": "SPEAKER_02", "text": "Holder again."},
        {"start": 20.0, "end": 24.0, "speaker": "SPEAKER_03", "text": "Other voice."},
    ]

    def voice(axis: int) -> SimpleNamespace:
        return SimpleNamespace(
            embedding=[1.0 if index == axis else 0.0 for index in range(8)],
            speech_seconds=5.0,
        )

    await _finalise(
        sessions,
        recording,
        final,
        {
            "SPEAKER_00": voice(0),
            "SPEAKER_01": voice(1),
            "SPEAKER_02": voice(1),
            "SPEAKER_03": voice(2),
        },
    )
    async with sessions() as db:
        merged = (
            await db.execute(
                select(RecordingSpeaker).where(
                    RecordingSpeaker.recording_id == recording.id,
                    RecordingSpeaker.diarization_label == "SPEAKER_02",
                )
            )
        ).scalar_one()
        assert merged.merged_into_id is not None
    body = (
        await client.get(f"/api/v1/transcripts/{recording.public_id}/utterances")
    ).json()
    by_text = {row["text"]: row for row in body["utterances"]}
    survivor = next(
        row for row in body["speakers"] if row["id"] == merged.merged_into_id
    )
    assert by_text["Moved line."]["recording_speaker_id"] == survivor["id"]
    assert survivor["local_name"] == "Alex" and survivor["name_pending"] is True
    _assert_no_live_label_survives(body)


@pytest.mark.parametrize(
    "scope",
    [
        "speaker_everywhere_in_recording",
        "from_this_utterance_forward",
        "merge_into_speaker",
    ],
)
async def test_a_live_speaker_takes_one_line_only(
    live_api: tuple[Any, ...], scope: str
) -> None:
    client, sessions, recording = live_api
    url = f"/api/v1/transcripts/{recording.public_id}/utterances"
    line = (await client.get(url)).json()["utterances"][0]
    refused = await client.patch(
        f"{url}/{line['id']}/speaker",
        json={
            "new_speaker_name": "Speaker 2",
            "diarization_label": TWO,
            "scope": scope,
            "expected_revision": line["revision"],
        },
    )
    assert refused.status_code == 409, refused.text
    assert "this line only" in refused.json()["detail"]
    after = (await client.get(url)).json()["utterances"]
    assert [row["speaker"] for row in after] == [ONE, TWO, TWO, THREE]
    assert after[0]["revision"] == line["revision"]


@pytest.mark.parametrize(
    ("status", "sealed"),
    [(RecordingStatus.PROCESSED, False), (RecordingStatus.UPLOADING, True)],
    ids=["processed", "stopping"],
)
async def test_a_live_speaker_is_offered_only_while_the_capture_runs(
    live_api: tuple[Any, ...], status: RecordingStatus, sealed: bool
) -> None:
    client, sessions, recording = live_api
    url = f"/api/v1/transcripts/{recording.public_id}/utterances"
    line = (await client.get(url)).json()["utterances"][0]
    async with sessions() as db:
        current = await db.get(Recording, recording.id)
        current.status = status
        if sealed:
            transcript = (
                await db.execute(
                    select(Transcript).where(Transcript.recording_id == recording.id)
                )
            ).scalar_one()
            transcript.settling_state = {**transcript.settling_state, "sealed": True}
        await db.commit()
    refused = await client.patch(
        f"{url}/{line['id']}/speaker",
        json={
            "new_speaker_name": "Speaker 2",
            "diarization_label": TWO,
            "scope": "utterance_only",
            "expected_revision": line["revision"],
        },
    )
    assert refused.status_code == 409, refused.text
    assert "recording or paused" in refused.json()["detail"]
    async with sessions() as db:
        row = (
            await db.execute(
                select(TranscriptUtterance).where(
                    TranscriptUtterance.public_id == line["id"]
                )
            )
        ).scalar_one()
        assert row.speaker_label == ONE and not row.manual_speaker_locked


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
def final_task_edges(monkeypatch: pytest.MonkeyPatch, stub_celery_dispatch: Any) -> Any:
    """Only third-party neural inference and Celery progress/dispatch are fake."""
    segments = [
        {"start": 0.2, "end": 10.2, "text": "Final corrected line."},
        {"start": 12.0, "end": 14.0, "text": "Target reference."},
        {"start": 15.0, "end": 17.0, "text": "Target again."},
        {"start": 20.0, "end": 24.0, "text": "Another voice."},
    ]
    model = Mock(is_multilingual=False)
    model.transcribe.return_value = {
        "text": " ".join(row["text"] for row in segments),
        "segments": segments,
        "language": "en",
    }
    annotation = Annotation()
    for row, label in zip(
        segments, ("SPEAKER_00", "SPEAKER_01", "SPEAKER_01", "SPEAKER_02")
    ):
        annotation[Segment(row["start"], row["end"])] = label
    neural_diariser = Mock(return_value=annotation)
    monkeypatch.setattr(whisper_engine, "_model_cache", {})
    monkeypatch.setattr(transcription_dispatch, "_ENGINE_REGISTRY", {})
    monkeypatch.setattr(whisper, "load_model", lambda *args, **kwargs: model)
    audio_api = ModuleType("pyannote.audio")
    audio_api.Pipeline = SimpleNamespace(
        from_pretrained=lambda *args, **kwargs: neural_diariser
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


async def test_a_named_live_choice_survives_the_complete_final_task(
    live_api: tuple[Any, ...],
    postgres_test_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    final_task_edges: Any,
) -> None:
    client, sessions, recording = live_api
    await _assign(client, recording, named=True)
    audio = tmp_path / "meeting.wav"
    sf.write(audio, 0.1 * np.sin(np.arange(25 * 16000) * 0.08), 16000)
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
    response = await client.get(f"/api/v1/transcripts/{recording.public_id}/utterances")
    assert response.status_code == 200, response.text
    lines = response.json()["utterances"]
    corrected = next(row for row in lines if "Final corrected line." in row["text"])
    references = [row for row in lines if "Target" in row["text"]]
    assert references and corrected["start"] == 0.2
    assert (
        corrected["speaker"] == "SPEAKER_01"
        and corrected["recording_speaker_id"] is not None
    )
    assert all(
        row["recording_speaker_id"] == corrected["recording_speaker_id"]
        for row in references
    )
    assert (
        corrected["speaker_manually_edited"] is True
        and corrected["speaker_edit_source"] == "api"
    )
    assert sum(row["local_name"] == "Alex" for row in response.json()["speakers"]) == 1
    assert response.json()["live_speaker_names"] == {}
    assert any(
        name == "backend.worker.tasks.generate_meeting_intelligence_task"
        for name, _, _ in final_task_edges
    )
