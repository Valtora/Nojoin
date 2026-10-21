"""A person's library voiceprint is founded only on enough speech, and folded
only with a voiceprint that sounds like it.

Every vector here is built so its cosine to the person's voiceprint is known:
``_at_cosine(c)`` is a unit vector at cosine ``c`` to ``PERSON``. The
floor (20 s) and the guard (0.70) are written as literals so each case reads on
its own.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from collections import deque
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.processing.embedding import merge_embeddings
from backend.tests.sqlite_schemas import (
    DIARIZATION_WINDOW_RESULTS_SCHEMA,
    DIARIZATION_WINDOW_TURNS_SCHEMA,
    GLOBAL_SPEAKERS_SCHEMA,
    P_TAGS_SCHEMA,
    PEOPLE_TAGS_SCHEMA,
    PROCESSING_RUNS_SCHEMA,
    RECORDING_AUDIO_WINDOW_MANIFESTS_SCHEMA,
    RECORDING_SPEAKER_ALIASES_SCHEMA,
    RECORDING_SPEAKER_DRIFT_REFUSALS_SCHEMA,
    RECORDING_SPEAKERS_SCHEMA,
    RECORDINGS_SCHEMA,
    SPEAKER_CORRECTION_EVENTS_SCHEMA,
    TRANSCRIPT_UTTERANCE_EVENTS_SCHEMA,
    TRANSCRIPT_UTTERANCES_SCHEMA,
    TRANSCRIPTS_SCHEMA,
)

PERSON = [1.0, 0.0]
ELI = [0.0, 1.0]
NOW = "2026-10-20 00:00:00"
REC = "vp-rec"


def _at_cosine(cosine: float) -> list[float]:
    return [cosine, math.sqrt(1.0 - cosine * cosine)]


def _blend(current: list[float], new: list[float], alpha: float) -> list[float]:
    return [(1 - alpha) * c + alpha * n for c, n in zip(current, new)]


def _outcome(status: str, seconds: float | None) -> dict[str, Any]:
    return {
        "status": status,
        "speech_seconds": seconds,
        "required_speech_seconds": 20.0,
    }


@pytest.fixture
async def session_maker():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        for schema in (
            RECORDINGS_SCHEMA,
            TRANSCRIPTS_SCHEMA,
            GLOBAL_SPEAKERS_SCHEMA,
            RECORDING_SPEAKERS_SCHEMA,
            RECORDING_SPEAKER_DRIFT_REFUSALS_SCHEMA,
            PROCESSING_RUNS_SCHEMA,
            RECORDING_AUDIO_WINDOW_MANIFESTS_SCHEMA,
            DIARIZATION_WINDOW_RESULTS_SCHEMA,
            DIARIZATION_WINDOW_TURNS_SCHEMA,
            TRANSCRIPT_UTTERANCES_SCHEMA,
            TRANSCRIPT_UTTERANCE_EVENTS_SCHEMA,
            RECORDING_SPEAKER_ALIASES_SCHEMA,
            SPEAKER_CORRECTION_EVENTS_SCHEMA,
            PEOPLE_TAGS_SCHEMA,
            P_TAGS_SCHEMA,
        ):
            await connection.execute(text(schema))
    try:
        yield sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def client(session_maker, stub_meeting_edge_dispatch):
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    async def override_get_db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=1, username="alice", settings={}, force_password_change=False
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as async_client:
        yield async_client


@pytest.fixture(params=["canonical", "legacy"])
def write_path(request, monkeypatch) -> str:
    """Run a case through the canonical write path and the legacy JSON one."""
    from backend.api.v1.endpoints import speakers as speakers_module

    if request.param == "legacy":
        original_get = speakers_module.config_manager.get

        def _get(key, default=None):
            if key == "enable_canonical_transcript_writes":
                return False
            return original_get(key, default)

        monkeypatch.setattr(speakers_module.config_manager, "get", _get)
    return request.param


@pytest.fixture
def extraction(monkeypatch) -> deque:
    """Queue the voiceprints the worker hands back, one per extraction call.

    An exception in the queue is raised by that call, as ``task.get`` re-raises
    whatever the worker raised.
    """
    from backend.api.v1.endpoints import speakers as speakers_module

    results: deque = deque()

    def _send_task(name, args=None, kwargs=None, **options):
        result = results.popleft()

        class _Result:
            def get(self, timeout=None):
                if isinstance(result, Exception):
                    raise result
                return result

        return _Result()

    monkeypatch.setattr(speakers_module.celery_app, "send_task", _send_task)
    return results


async def _seed(
    session_maker,
    *,
    speakers: list[dict[str, Any]],
    people: list[dict[str, Any]] = (),
) -> None:
    async with session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, status, upload_progress, processing_progress,
                    pipeline_generation, is_archived, is_deleted, user_id
                ) VALUES (
                    1, :now, :now, 'Meeting', :public_id, 'uid-1',
                    '/tmp/meeting.wav', 'PROCESSED', 100, 100, 'unified', 0, 0, 1
                )
                """
            ),
            {"now": NOW, "public_id": REC},
        )
        segments = [
            {
                "start": float(index * 5),
                "end": float(index * 5 + 5),
                "speaker": speaker["label"],
                "text": f"line {index}",
                "segment_source": "legacy",
            }
            for index, speaker in enumerate(speakers)
        ]
        await session.execute(
            text(
                """
                INSERT INTO transcripts (
                    id, created_at, updated_at, recording_id, text, segments,
                    meeting_edge_status, notes_status, transcript_status
                ) VALUES (1, :now, :now, 1, 'text', :segments, 'idle',
                          'pending', 'completed')
                """
            ),
            {"now": NOW, "segments": json.dumps(segments)},
        )
        for person in people:
            await session.execute(
                text(
                    """
                    INSERT INTO global_speakers (
                        id, created_at, updated_at, user_id, name, embedding,
                        embedding_version, is_voiceprint_locked
                    ) VALUES (:id, :now, :now, 1, :name, :embedding, 2, :locked)
                    """
                ),
                {
                    "now": NOW,
                    "id": person["id"],
                    "name": person["name"],
                    "embedding": (
                        json.dumps(person["embedding"])
                        if person.get("embedding") is not None
                        else None
                    ),
                    "locked": int(person.get("locked", False)),
                },
            )
        for index, speaker in enumerate(speakers, start=1):
            await session.execute(
                text(
                    """
                    INSERT INTO recording_speakers (
                        id, created_at, updated_at, public_id, recording_id,
                        global_speaker_id, diarization_label, local_name, name,
                        embedding, embedding_version, embedding_speech_seconds,
                        speaker_status, speaker_kind, identity_locked
                    ) VALUES (
                        :id, :now, :now, :public_id, 1, :global_id, :label,
                        :local_name, :name, :embedding, 2, :seconds,
                        'active', 'automated', 0
                    )
                    """
                ),
                {
                    "now": NOW,
                    "id": index,
                    "public_id": f"rs-{index}",
                    "global_id": speaker.get("global_speaker_id"),
                    "label": speaker["label"],
                    "local_name": speaker.get("local_name"),
                    "name": speaker.get("name", f"Speaker {index}"),
                    "embedding": (
                        json.dumps(speaker["embedding"])
                        if speaker.get("embedding") is not None
                        else None
                    ),
                    "seconds": speaker.get("seconds"),
                },
            )
            await _insert_refusals(session, index, speaker.get("refused_for"))
        await session.commit()


async def _insert_refusals(session, speaker_id: int, refused_for: Any) -> None:
    """Record the people a speaker was refused for: one id, several, or None."""
    people = () if refused_for is None else refused_for
    for person_id in (people,) if isinstance(people, int) else people:
        await session.execute(
            text(
                "INSERT INTO recording_speaker_drift_refusals "
                "(recording_speaker_id, global_speaker_id) VALUES (:id, :person)"
            ),
            {"id": speaker_id, "person": person_id},
        )


async def _refusals_by_id(session_maker) -> dict[int, tuple[int, ...]]:
    """The people each recording speaker was refused for, by speaker id."""
    async with session_maker() as session:
        speakers = (
            await session.execute(text("SELECT id FROM recording_speakers"))
        ).scalars()
        refused: dict[int, tuple[int, ...]] = {speaker: () for speaker in speakers}
        rows = await session.execute(
            text(
                "SELECT recording_speaker_id, global_speaker_id "
                "FROM recording_speaker_drift_refusals "
                "ORDER BY recording_speaker_id, global_speaker_id"
            )
        )
        for speaker, person in rows.all():
            refused[speaker] = (*refused.get(speaker, ()), person)
    return refused


async def _person_voiceprint(session_maker, name: str) -> list[float] | None:
    async with session_maker() as session:
        value = (
            await session.execute(
                text("SELECT embedding FROM global_speakers WHERE name = :name"),
                {"name": name},
            )
        ).scalar_one()
    return json.loads(value) if value is not None else None


async def _refused_for(session_maker) -> dict[str, tuple[int, ...]]:
    """The people each recording speaker was refused for, by label."""
    refused = await _refusals_by_id(session_maker)
    async with session_maker() as session:
        rows = await session.execute(
            text("SELECT id, diarization_label FROM recording_speakers")
        )
        return {label: refused[speaker] for speaker, label in rows.all()}


async def _speaker_row(session_maker, label: str) -> tuple[Any, Any]:
    async with session_maker() as session:
        embedding, seconds = (
            await session.execute(
                text(
                    "SELECT embedding, embedding_speech_seconds "
                    "FROM recording_speakers WHERE diarization_label = :label"
                ),
                {"label": label},
            )
        ).one()
    return (json.loads(embedding) if embedding is not None else None), seconds


def test_the_guards_warning_states_its_verdict_and_no_callers_action(caplog) -> None:
    """judge_drift records a refusal and folds nothing, so the guard's warning
    gives the score and the threshold, not a merge or a returned embedding."""
    from backend.processing.embedding import judge_drift

    speaker = SimpleNamespace(
        embedding=_at_cosine(0.1), embedding_version=2, drift_refusals=[]
    )
    person = SimpleNamespace(id=7, embedding=list(PERSON), embedding_version=2)

    with caplog.at_level(logging.WARNING, logger="backend.processing.embedding"):
        judge_drift(speaker, person)

    assert [refusal.global_speaker_id for refusal in speaker.drift_refusals] == [7]
    assert caplog.messages == [
        "Drift guard refused a voiceprint: similarity 0.100 is below 0.70"
    ]


def test_merge_embeddings_refuses_a_fold_below_seventy() -> None:
    assert merge_embeddings(PERSON, _at_cosine(0.69), alpha=0.5) == PERSON
    assert merge_embeddings(PERSON, _at_cosine(0.71), alpha=0.5) == pytest.approx(
        _blend(PERSON, _at_cosine(0.71), 0.5)
    )


def test_a_fold_at_exactly_the_threshold_is_accepted(monkeypatch) -> None:
    """The guard refuses below its threshold, not at it.

    The cosine of ``_at_cosine(0.70)`` lands within an ulp of 0.7, and which
    side depends on the CPU and BLAS. So the threshold is set to the cosine this
    platform actually computes: a fold scoring exactly the threshold is
    accepted, and the next float up refuses it.
    """
    from backend.processing import embedding

    at_threshold = _at_cosine(0.70)
    similarity = embedding.cosine_similarity(PERSON, at_threshold)
    assert similarity == pytest.approx(0.7)

    monkeypatch.setattr(embedding, "DRIFT_GUARD_THRESHOLD", similarity)
    assert merge_embeddings(PERSON, at_threshold, alpha=0.5) == pytest.approx(
        _blend(PERSON, at_threshold, 0.5)
    )

    monkeypatch.setattr(
        embedding, "DRIFT_GUARD_THRESHOLD", math.nextafter(similarity, 1.0)
    )
    assert merge_embeddings(PERSON, at_threshold, alpha=0.5) == PERSON


@pytest.mark.parametrize(
    ("seconds", "status", "replaced"),
    [(12.0, "skipped_insufficient_speech", False), (25.0, "saved", True)],
)
def test_replacing_a_stale_voiceprint_needs_the_floor_like_founding_one(
    seconds, status, replaced
) -> None:
    from backend.processing.embedding import (
        RecordingVoiceprint,
        update_library_voiceprint,
    )

    stale = [0.6, 0.8]
    person = SimpleNamespace(embedding=list(stale), embedding_version=None)

    update = update_library_voiceprint(
        person, RecordingVoiceprint(list(PERSON), seconds), alpha=0.3
    )

    assert update.status.value == status
    assert (person.embedding, person.embedding_version) == (
        (PERSON, 2) if replaced else (stale, None)
    )


def test_a_stale_recording_voiceprint_leaves_a_current_person_as_they_were() -> None:
    from backend.processing.embedding import (
        RecordingVoiceprint,
        update_library_voiceprint,
    )

    person = SimpleNamespace(embedding=list(PERSON), embedding_version=2)

    update = update_library_voiceprint(
        person, RecordingVoiceprint([0.6, 0.8], 25.0, version=None), alpha=0.3
    )

    assert update.status.value == "skipped_outdated"
    assert not update.changed
    assert (person.embedding, person.embedding_version) == (PERSON, 2)


def test_a_recording_voiceprint_that_replaces_a_stale_one_takes_its_speech() -> None:
    from backend.processing.embedding import (
        RecordingVoiceprint,
        fold_recording_voiceprint,
    )

    target = SimpleNamespace(
        embedding=[0.6, 0.8],
        embedding_version=None,
        embedding_speech_seconds=5.0,
        drift_refusals=[],
    )

    fold_recording_voiceprint(
        target, RecordingVoiceprint(list(PERSON), 25.0), alpha=0.5, person=None
    )

    assert (
        target.embedding,
        target.embedding_version,
        target.embedding_speech_seconds,
    ) == (PERSON, 2, 25.0)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "case",
    [
        (12.0, "skipped_insufficient_speech", False),
        (19.99, "skipped_insufficient_speech", False),
        # A voiceprint from before speech was measured is grandfathered.
        (None, "saved", True),
        (20.0, "saved", True),
    ],
)
async def test_rename_founds_a_first_voiceprint_only_from_enough_speech(
    client, session_maker, write_path, case
) -> None:
    seconds, status, saved = case
    voice = _at_cosine(0.9)
    await _seed(
        session_maker,
        speakers=[{"label": "SPEAKER_00", "embedding": voice, "seconds": seconds}],
        people=[{"id": 7, "name": "Dana"}],
    )

    response = await client.put(
        f"/api/v1/speakers/recordings/{REC}",
        json={"diarization_label": "SPEAKER_00", "global_speaker_name": "Dana"},
    )

    assert response.status_code == 200
    assert response.json()[0]["library_voiceprint"] == _outcome(status, seconds)
    expected = pytest.approx(voice) if saved else None
    assert await _person_voiceprint(session_maker, "Dana") == expected
    assert response.json()[0]["global_speaker_id"] == 7


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("cosine", "status"), [(0.65, "skipped_drift"), (0.75, "folded")]
)
async def test_rename_folds_into_a_voiceprint_only_past_the_drift_guard(
    client, session_maker, write_path, cosine, status
) -> None:
    voice = _at_cosine(cosine)
    await _seed(
        session_maker,
        speakers=[
            {
                "label": "SPEAKER_00",
                "embedding": voice,
                "seconds": 3.0,
                "refused_for": None if status == "skipped_drift" else 7,
            }
        ],
        people=[{"id": 7, "name": "Dana", "embedding": PERSON}],
    )

    response = await client.put(
        f"/api/v1/speakers/recordings/{REC}",
        json={"diarization_label": "SPEAKER_00", "global_speaker_name": "Dana"},
    )

    assert response.json()[0]["library_voiceprint"] == _outcome(status, 3.0)
    expected = _blend(PERSON, voice, 0.3) if status == "folded" else PERSON
    assert await _person_voiceprint(session_maker, "Dana") == pytest.approx(expected)
    # The refusal stays on the link, so the rebuild and a split leave it out;
    # a fold lifts an earlier one.
    assert await _refused_for(session_maker) == {
        "SPEAKER_00": (7,) if status == "skipped_drift" else ()
    }


@pytest.mark.anyio
async def test_a_lock_blocks_a_fold_on_the_legacy_path_but_not_a_first_voiceprint(
    client, session_maker, monkeypatch
) -> None:
    from backend.api.v1.endpoints import speakers as speakers_module

    original_get = speakers_module.config_manager.get
    monkeypatch.setattr(
        speakers_module.config_manager,
        "get",
        lambda key, default=None: (
            False
            if key == "enable_canonical_transcript_writes"
            else original_get(key, default)
        ),
    )
    voice = _at_cosine(0.95)
    await _seed(
        session_maker,
        speakers=[
            {"label": "SPEAKER_00", "embedding": voice, "seconds": 30.0},
            {"label": "SPEAKER_01", "embedding": voice, "seconds": 30.0},
        ],
        people=[
            {"id": 7, "name": "Dana", "embedding": PERSON, "locked": True},
            {"id": 8, "name": "Eli", "locked": True},
        ],
    )

    dana = await client.put(
        f"/api/v1/speakers/recordings/{REC}",
        json={"diarization_label": "SPEAKER_00", "global_speaker_name": "Dana"},
    )
    eli = await client.put(
        f"/api/v1/speakers/recordings/{REC}",
        json={"diarization_label": "SPEAKER_01", "global_speaker_name": "Eli"},
    )

    assert dana.json()[0]["library_voiceprint"] == _outcome("skipped_locked", 30.0)
    assert await _person_voiceprint(session_maker, "Dana") == PERSON
    assert eli.json()[0]["library_voiceprint"] == _outcome("saved", 30.0)
    assert await _person_voiceprint(session_maker, "Eli") == pytest.approx(voice)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("seconds", "status"),
    [(12.0, "skipped_insufficient_speech"), (25.0, "saved")],
)
async def test_promote_founds_a_new_persons_voiceprint_only_from_enough_speech(
    client, session_maker, write_path, seconds, status
) -> None:
    voice = _at_cosine(0.9)
    await _seed(
        session_maker,
        speakers=[
            {
                "label": "SPEAKER_00",
                "embedding": voice,
                "seconds": seconds,
                "local_name": "Alex",
                "name": None,
            }
        ],
    )

    response = await client.post(
        f"/api/v1/speakers/recordings/{REC}/speakers/SPEAKER_00/promote"
    )

    assert response.status_code == 200
    assert response.json()["library_voiceprint"] == _outcome(status, seconds)
    expected = pytest.approx(voice) if status == "saved" else None
    assert await _person_voiceprint(session_maker, "Alex") == expected


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("cosine", "status"), [(0.65, "skipped_drift"), (0.75, "folded")]
)
async def test_promote_to_an_existing_person_folds_only_past_the_drift_guard(
    client, session_maker, cosine, status
) -> None:
    voice = _at_cosine(cosine)
    await _seed(
        session_maker,
        speakers=[
            {
                "label": "SPEAKER_00",
                "embedding": voice,
                "seconds": 40.0,
                "local_name": "Dana",
                "name": None,
            }
        ],
        people=[{"id": 7, "name": "Dana", "embedding": PERSON}],
    )

    response = await client.post(
        f"/api/v1/speakers/recordings/{REC}/speakers/SPEAKER_00/promote"
    )

    assert response.json()["library_voiceprint"] == _outcome(status, 40.0)
    expected = _blend(PERSON, voice, 0.5) if status == "folded" else PERSON
    assert await _person_voiceprint(session_maker, "Dana") == pytest.approx(expected)
    assert await _refused_for(session_maker) == {
        "SPEAKER_00": (7,) if status == "skipped_drift" else ()
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("seconds", "status"),
    [(12.0, "skipped_insufficient_speech"), (25.0, "saved")],
)
async def test_voiceprint_create_new_founds_one_only_from_enough_speech(
    client, session_maker, seconds, status
) -> None:
    voice = _at_cosine(0.9)
    await _seed(
        session_maker,
        speakers=[{"label": "SPEAKER_00", "embedding": voice, "seconds": seconds}],
    )

    response = await client.post(
        f"/api/v1/speakers/recordings/{REC}/speakers/SPEAKER_00/voiceprint/apply",
        json={"action": "create_new", "new_speaker_name": "Robin"},
    )

    assert response.status_code == 200
    assert response.json()["library_voiceprint"] == _outcome(status, seconds)
    expected = pytest.approx(voice) if status == "saved" else None
    assert await _person_voiceprint(session_maker, "Robin") == expected


@pytest.mark.anyio
@pytest.mark.parametrize(
    "case",
    [
        ("link_existing", 0.3, 12.0, None, 0.9, "skipped_insufficient_speech"),
        ("link_existing", 0.3, 25.0, None, 0.9, "saved"),
        ("link_existing", 0.3, 5.0, PERSON, 0.65, "skipped_drift"),
        ("link_existing", 0.3, 5.0, PERSON, 0.75, "folded"),
        ("force_link", 0.4, 5.0, PERSON, 0.65, "skipped_drift"),
        ("force_link", 0.4, 5.0, PERSON, 0.75, "folded"),
    ],
)
async def test_voiceprint_link_applies_the_floor_and_the_drift_guard(
    client, session_maker, case
) -> None:
    action, alpha, seconds, person, cosine, status = case
    voice = _at_cosine(cosine)
    await _seed(
        session_maker,
        speakers=[
            {
                "label": "SPEAKER_00",
                "embedding": voice,
                "seconds": seconds,
                "refused_for": 7,
            }
        ],
        people=[{"id": 7, "name": "Dana", "embedding": person}],
    )

    response = await client.post(
        f"/api/v1/speakers/recordings/{REC}/speakers/SPEAKER_00/voiceprint/apply",
        json={"action": action, "global_speaker_id": 7},
    )

    assert response.json()["library_voiceprint"] == _outcome(status, seconds)
    expected = {
        "skipped_insufficient_speech": None,
        "saved": voice,
        "skipped_drift": PERSON,
        "folded": _blend(PERSON, voice, alpha),
    }[status]
    stored = await _person_voiceprint(session_maker, "Dana")
    assert stored == (pytest.approx(expected) if expected is not None else None)
    # A save or a fold lifts an earlier refusal, a refusal keeps it, and too
    # little speech says nothing about it either way.
    lifted = status in ("saved", "folded")
    assert await _refused_for(session_maker) == {"SPEAKER_00": () if lifted else (7,)}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "case",
    [
        ("speakers/SPEAKER_00/voiceprint/extract", 12.0, "skipped_insufficient_speech"),
        ("speakers/SPEAKER_00/voiceprint/extract", 25.0, "saved"),
        ("voiceprints/extract-all", 12.0, "skipped_insufficient_speech"),
        ("voiceprints/extract-all", 25.0, "saved"),
    ],
)
async def test_an_extracted_voiceprint_carries_its_measured_speech_to_the_library(
    client, session_maker, extraction, case
) -> None:
    endpoint, seconds, status = case
    voice = _at_cosine(0.9)
    await _seed(session_maker, speakers=[{"label": "SPEAKER_00", "embedding": None}])
    extraction.append({"embedding": voice, "speech_seconds": seconds})

    extracted = await client.post(f"/api/v1/speakers/recordings/{REC}/{endpoint}")
    applied = await client.post(
        f"/api/v1/speakers/recordings/{REC}/speakers/SPEAKER_00/voiceprint/apply",
        json={"action": "create_new", "new_speaker_name": "Robin"},
    )

    assert extracted.status_code == 200
    assert await _speaker_row(session_maker, "SPEAKER_00") == (
        pytest.approx(voice),
        seconds,
    )
    assert applied.json()["library_voiceprint"] == _outcome(status, seconds)


@pytest.mark.anyio
async def test_the_api_asks_for_a_measured_voiceprint_and_never_stores_an_unmeasured_one(
    client, session_maker, monkeypatch
) -> None:
    from backend.api.v1.endpoints import speakers as speakers_module

    asked: list[str] = []

    def _send_task(name, args=None, kwargs=None, **options):
        asked.append(name)

        class _Result:
            def get(self, timeout=None):
                return list(PERSON)

        return _Result()

    monkeypatch.setattr(speakers_module.celery_app, "send_task", _send_task)
    await _seed(session_maker, speakers=[{"label": "SPEAKER_00", "embedding": None}])

    response = await client.post(
        f"/api/v1/speakers/recordings/{REC}/speakers/SPEAKER_00/voiceprint/extract"
    )

    # A result with no speech figure would be grandfathered past the floor,
    # which only voiceprints from before the figure existed may be.
    assert response.status_code == 504
    assert asked == ["backend.worker.tasks.extract_measured_embedding_task"]
    assert await _speaker_row(session_maker, "SPEAKER_00") == (None, None)


@pytest.mark.anyio
async def test_deleting_a_recording_voiceprint_forgets_its_measured_speech(
    client, session_maker
) -> None:
    await _seed(
        session_maker,
        speakers=[{"label": "SPEAKER_00", "embedding": PERSON, "seconds": 25.0}],
    )

    response = await client.delete(
        f"/api/v1/speakers/recordings/{REC}/speakers/SPEAKER_00/voiceprint"
    )

    assert response.status_code == 200
    assert await _speaker_row(session_maker, "SPEAKER_00") == (None, None)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "case", [(0.75, 22.0, "saved"), (0.65, 12.0, "skipped_insufficient_speech")]
)
async def test_merging_two_clusters_adds_their_speech_when_the_guard_lets_it_fold(
    client, session_maker, write_path, case
) -> None:
    cosine, merged_seconds, status = case
    source_voice = _at_cosine(cosine)
    await _seed(
        session_maker,
        speakers=[
            {
                "label": "SPEAKER_00",
                "embedding": PERSON,
                "seconds": 12.0,
                "local_name": "Alex",
                "name": None,
            },
            {"label": "SPEAKER_01", "embedding": source_voice, "seconds": 10.0},
        ],
    )

    merged = await client.post(
        f"/api/v1/speakers/recordings/{REC}/merge",
        json={
            "target_speaker_label": "SPEAKER_00",
            "source_speaker_label": "SPEAKER_01",
        },
    )
    promoted = await client.post(
        f"/api/v1/speakers/recordings/{REC}/speakers/SPEAKER_00/promote"
    )

    assert merged.status_code == 200
    target = _blend(PERSON, source_voice, 0.5) if status == "saved" else PERSON
    embedding, seconds = await _speaker_row(session_maker, "SPEAKER_00")
    assert (embedding, seconds) == (pytest.approx(target), merged_seconds)
    assert promoted.json()["library_voiceprint"] == _outcome(status, merged_seconds)
    # The merged row keeps what it added to the target's figure, for an
    # unmerge to take back out; a refused fold added nothing.
    assert await _speaker_row(session_maker, "SPEAKER_01") == (
        None,
        10.0 if status == "saved" else None,
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("target_seconds", "source_seconds"), [(None, None), (12.0, None), (None, 10.0)]
)
async def test_merging_an_unmeasured_speaker_leaves_the_result_unmeasured(
    client, session_maker, write_path, target_seconds, source_seconds
) -> None:
    """Speech from before measurement is grandfathered, so the merge is too.

    Recording 0 s, or only the measured side, would take a voiceprint that
    founds a person today and stop it founding one after the upgrade.
    """
    source_voice = _at_cosine(0.9)
    await _seed(
        session_maker,
        speakers=[
            {
                "label": "SPEAKER_00",
                "embedding": PERSON,
                "seconds": target_seconds,
                "local_name": "Alex",
                "name": None,
            },
            {
                "label": "SPEAKER_01",
                "embedding": source_voice,
                "seconds": source_seconds,
            },
        ],
    )

    merged = await client.post(
        f"/api/v1/speakers/recordings/{REC}/merge",
        json={
            "target_speaker_label": "SPEAKER_00",
            "source_speaker_label": "SPEAKER_01",
        },
    )
    promoted = await client.post(
        f"/api/v1/speakers/recordings/{REC}/speakers/SPEAKER_00/promote"
    )

    assert merged.status_code == 200
    assert await _speaker_row(session_maker, "SPEAKER_00") == (
        pytest.approx(_blend(PERSON, source_voice, 0.5)),
        None,
    )
    assert promoted.json()["library_voiceprint"] == _outcome("saved", None)


async def _links(session_maker) -> dict[str, tuple[Any, tuple[int, ...]]]:
    """Each recording speaker's person and the people it was refused for."""
    refused = await _refusals_by_id(session_maker)
    async with session_maker() as session:
        rows = await session.execute(
            text(
                "SELECT id, diarization_label, global_speaker_id "
                "FROM recording_speakers"
            )
        )
        return {label: (person, refused[speaker]) for speaker, label, person in rows}


@pytest.mark.anyio
async def test_merging_people_carries_the_sources_refusals_to_the_target(
    client, session_maker
) -> None:
    """Eli is merged into Dana, and Eli's refusals become Dana's.

    They are not nulled with Eli's row: SPEAKER_00, refused for Eli, is
    Dana's now and still refused. SPEAKER_01, refused for Eli and since moved
    to Fay, is refused for Dana should it come back to her. Fay's own refusal
    of SPEAKER_02 is left alone.
    """
    await _seed(
        session_maker,
        speakers=[
            {"label": "SPEAKER_00", "global_speaker_id": 8, "refused_for": 8},
            {"label": "SPEAKER_01", "global_speaker_id": 9, "refused_for": 8},
            {"label": "SPEAKER_02", "global_speaker_id": 9, "refused_for": 9},
        ],
        people=[
            {"id": 7, "name": "Dana", "embedding": PERSON},
            {"id": 8, "name": "Eli", "embedding": PERSON},
            {"id": 9, "name": "Fay", "embedding": PERSON},
        ],
    )

    response = await client.post(
        "/api/v1/speakers/merge", json={"source_speaker_id": 8, "target_speaker_id": 7}
    )

    assert response.status_code == 200
    assert await _links(session_maker) == {
        "SPEAKER_00": (7, (7,)),
        "SPEAKER_01": (9, (7,)),
        "SPEAKER_02": (9, (9,)),
    }


@pytest.mark.anyio
async def test_merging_people_keeps_one_refusal_where_both_refused_a_speaker(
    client, session_maker
) -> None:
    """Eli is merged into Dana, and some speakers were refused for both.

    SPEAKER_00, Eli's and refused for both, keeps one refusal, for Dana.
    SPEAKER_01, Fay's and refused for Eli and for Fay, is refused for Dana
    and Fay. No refusal for Eli is left behind.
    """
    await _seed(
        session_maker,
        speakers=[
            {"label": "SPEAKER_00", "global_speaker_id": 8, "refused_for": (7, 8)},
            {"label": "SPEAKER_01", "global_speaker_id": 9, "refused_for": (8, 9)},
        ],
        people=[
            {"id": 7, "name": "Dana", "embedding": PERSON},
            {"id": 8, "name": "Eli", "embedding": PERSON},
            {"id": 9, "name": "Fay", "embedding": PERSON},
        ],
    )

    response = await client.post(
        "/api/v1/speakers/merge", json={"source_speaker_id": 8, "target_speaker_id": 7}
    )

    assert response.status_code == 200
    assert await _links(session_maker) == {
        "SPEAKER_00": (7, (7,)),
        "SPEAKER_01": (9, (7, 9)),
    }


@pytest.mark.anyio
async def test_merging_people_brings_back_a_refusal_for_the_target(
    client, session_maker
) -> None:
    """A speaker refused for Dana and moved to Eli is Dana's again after the
    merge, with the voiceprint the guard refused for her, so it stays out."""
    await _seed(
        session_maker,
        speakers=[{"label": "SPEAKER_00", "global_speaker_id": 8, "refused_for": 7}],
        people=[
            {"id": 7, "name": "Dana", "embedding": PERSON},
            {"id": 8, "name": "Eli", "embedding": PERSON},
        ],
    )

    response = await client.post(
        "/api/v1/speakers/merge", json={"source_speaker_id": 8, "target_speaker_id": 7}
    )

    assert response.status_code == 200
    assert await _links(session_maker) == {"SPEAKER_00": (7, (7,))}


@pytest.mark.anyio
async def test_linking_a_speaker_back_without_a_verdict_keeps_its_refusal(
    client, session_maker, monkeypatch
) -> None:
    """A refusal for Dana applies again when its speaker is hers again.

    The speaker was refused for Dana and moved to Eli. Renamed back to Dana on
    the legacy path, whose lock refuses the fold, it gets no new verdict, and
    its voiceprint is still the one the guard refused for her.
    """
    from backend.api.v1.endpoints import speakers as speakers_module

    original_get = speakers_module.config_manager.get
    monkeypatch.setattr(
        speakers_module.config_manager,
        "get",
        lambda key, default=None: (
            False
            if key == "enable_canonical_transcript_writes"
            else original_get(key, default)
        ),
    )
    await _seed(
        session_maker,
        speakers=[
            {
                "label": "SPEAKER_00",
                "global_speaker_id": 8,
                "embedding": _at_cosine(0.65),
                "seconds": 30.0,
                "refused_for": 7,
            }
        ],
        people=[
            {"id": 7, "name": "Dana", "embedding": PERSON, "locked": True},
            {"id": 8, "name": "Eli"},
        ],
    )

    response = await client.put(
        f"/api/v1/speakers/recordings/{REC}",
        json={"diarization_label": "SPEAKER_00", "global_speaker_name": "Dana"},
    )

    assert response.json()[0]["library_voiceprint"]["status"] == "skipped_locked"
    assert await _links(session_maker) == {"SPEAKER_00": (7, (7,))}


@pytest.mark.anyio
async def test_a_save_into_someone_else_keeps_the_refusal_for_a_return(
    client, session_maker, monkeypatch
) -> None:
    """Refused for Dana, saved into Eli, renamed back to Dana: still refused.

    Saving the speaker into Eli is Eli's verdict, not Dana's, so the refusal
    for Dana stays. The legacy rename back to a locked Dana gives no new
    verdict, and without the refusal the speaker would be pooled into her
    voiceprint again.
    """
    from backend.api.v1.endpoints import speakers as speakers_module

    legacy = {"on": False}
    original_get = speakers_module.config_manager.get
    monkeypatch.setattr(
        speakers_module.config_manager,
        "get",
        lambda key, default=None: (
            not legacy["on"]
            if key == "enable_canonical_transcript_writes"
            else original_get(key, default)
        ),
    )
    await _seed(
        session_maker,
        speakers=[
            {"label": "SPEAKER_00", "embedding": _at_cosine(0.65), "seconds": 30.0}
        ],
        people=[
            {"id": 7, "name": "Dana", "embedding": PERSON, "locked": True},
            {"id": 8, "name": "Eli"},
        ],
    )

    async def rename(name: str) -> str:
        response = await client.put(
            f"/api/v1/speakers/recordings/{REC}",
            json={"diarization_label": "SPEAKER_00", "global_speaker_name": name},
        )
        return response.json()[0]["library_voiceprint"]["status"]

    assert await rename("Dana") == "skipped_drift"
    assert await rename("Eli") == "saved"
    assert await _links(session_maker) == {"SPEAKER_00": (8, (7,))}
    legacy["on"] = True
    assert await rename("Dana") == "skipped_locked"
    assert await _links(session_maker) == {"SPEAKER_00": (7, (7,))}


@pytest.mark.anyio
async def test_merging_people_folds_voiceprints_only_past_the_drift_guard(
    client, session_maker
) -> None:
    near, far = _at_cosine(0.75), _at_cosine(0.65)
    await _seed(
        session_maker,
        speakers=[],
        people=[
            {"id": 7, "name": "Dana", "embedding": PERSON},
            {"id": 8, "name": "Dana (2)", "embedding": near},
            {"id": 9, "name": "Eli", "embedding": PERSON},
            {"id": 10, "name": "Eli (2)", "embedding": far},
        ],
    )

    for source, target in ((8, 7), (10, 9)):
        response = await client.post(
            "/api/v1/speakers/merge",
            json={"source_speaker_id": source, "target_speaker_id": target},
        )
        assert response.status_code == 200

    assert await _person_voiceprint(session_maker, "Dana") == pytest.approx(
        _blend(PERSON, near, 0.5)
    )
    assert await _person_voiceprint(session_maker, "Eli") == PERSON


@pytest.mark.anyio
@pytest.mark.parametrize(
    "case",
    [
        ([12.0], None),
        # Seconds are not added up across recordings: 21 s from two is not a
        # voiceprint built from 20 s.
        ([12.0, 9.0], None),
        # Only the recording that meets the floor on its own is used.
        ([12.0, 25.0], 1),
        ([25.0, 30.0], "both"),
    ],
)
async def test_recalibration_uses_only_recordings_with_enough_speech_each(
    client, session_maker, extraction, monkeypatch, case
) -> None:
    from backend.api.v1.endpoints.speakers import routes_global

    chunks, used = case
    monkeypatch.setattr(
        routes_global,
        "select_recording_audio_for_embedding",
        lambda recording: recording.audio_path,
    )
    voice = _at_cosine(0.8)
    await _seed(
        session_maker,
        speakers=[{"label": "SPEAKER_00", "global_speaker_id": 7}],
        people=[{"id": 7, "name": "Dana", "embedding": PERSON}],
    )
    async with session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, status, upload_progress, processing_progress,
                    pipeline_generation, is_archived, is_deleted, user_id
                ) VALUES (2, :now, :now, 'Second', 'vp-rec-2', 'uid-2',
                          '/tmp/second.wav', 'PROCESSED', 100, 100, 'unified',
                          0, 0, 1)
                """
            ),
            {"now": NOW},
        )
        await session.commit()
    voices = [voice, _at_cosine(0.6)]
    for recording_voice, seconds in zip(voices, chunks):
        extraction.append({"embedding": recording_voice, "speech_seconds": seconds})
    selections = [
        {"recording_id": recording_id, "start": 0.0, "end": 5.0}
        for recording_id in (REC, "vp-rec-2")[: len(chunks)]
    ]

    response = await client.post("/api/v1/speakers/7/recalibrate", json=selections)

    expected = {
        None: PERSON,
        1: voices[1],
        "both": _blend(voices[0], voices[1], 0.5),
    }[used]
    assert response.status_code == (400 if used is None else 200)
    assert await _person_voiceprint(session_maker, "Dana") == pytest.approx(expected)
    if used is None:
        assert "20 s of clear speech on its own" in response.json()["detail"]
    else:
        assert (
            response.json()["message"]
            == {
                1: (
                    "Voiceprint recalibrated from 1 of 2 recordings and locked. "
                    "The others held less than 20 s of clear speech."
                ),
                "both": "Voiceprint recalibrated and locked.",
            }[used]
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("selected_seconds", "remaining_seconds"), [(12.0, 25.0), (25.0, 12.0)]
)
async def test_a_split_founds_and_rebuilds_voiceprints_only_from_enough_speech(
    client, session_maker, extraction, selected_seconds, remaining_seconds
) -> None:
    selected_voice, remaining_voice = _at_cosine(0.2), _at_cosine(0.95)
    await _seed(
        session_maker,
        speakers=[{"label": "SPEAKER_00", "global_speaker_id": 7, "name": "Dana"}],
        people=[{"id": 7, "name": "Dana", "embedding": PERSON, "locked": True}],
    )
    async with session_maker() as session:
        segments = [
            {"start": 0.0, "end": 5.0, "speaker": "SPEAKER_00", "text": "mine"},
            {"start": 5.0, "end": 10.0, "speaker": "SPEAKER_00", "text": "theirs"},
        ]
        await session.execute(
            text("UPDATE transcripts SET segments = :segments WHERE id = 1"),
            {"segments": json.dumps(segments)},
        )
        await session.commit()
    extraction.append({"embedding": selected_voice, "speech_seconds": selected_seconds})
    extraction.append(
        {"embedding": remaining_voice, "speech_seconds": remaining_seconds}
    )

    response = await client.post(
        "/api/v1/speakers/7/split",
        json={
            "new_speaker_name": "Robin",
            "segments": [{"recording_id": REC, "start": 0.0, "end": 5.0}],
        },
    )

    assert response.status_code == 200
    robin = await _person_voiceprint(session_maker, "Robin")
    dana = await _person_voiceprint(session_maker, "Dana")
    body = response.json()
    if selected_seconds >= 20.0:
        # Dana's old voiceprint held Robin's clips, and what remains is too
        # little to rebuild it, so it goes.
        assert (robin, dana) == (pytest.approx(selected_voice), None)
        assert body["library_voiceprint"] == _outcome("saved", 25.0)
        assert body["original_library_voiceprint"] == _outcome(
            "skipped_insufficient_speech", 12.0
        )
        assert body["has_voiceprint"] is True
    else:
        assert (robin, dana) == (None, pytest.approx(remaining_voice))
        assert body["library_voiceprint"] == _outcome(
            "skipped_insufficient_speech", 12.0
        )
        assert body["original_library_voiceprint"] == _outcome("saved", 25.0)
        assert body["has_voiceprint"] is False
    # Robin is locked exactly when the split gave them a voiceprint. Dana's
    # lock stays when hers is rebuilt, and goes with it when it is cleared, so
    # that it can learn again.
    assert await _locked(session_maker) == {
        "Robin": selected_seconds >= 20.0,
        "Dana": remaining_seconds >= 20.0,
    }
    rows = await _speaker_row(session_maker, "SPEAKER_00")
    assert rows == (pytest.approx(remaining_voice), remaining_seconds)


async def _locked(session_maker) -> dict[str, bool]:
    async with session_maker() as session:
        rows = await session.execute(
            text("SELECT name, is_voiceprint_locked FROM global_speakers")
        )
        return {name: bool(locked) for name, locked in rows.all()}


@pytest.mark.anyio
async def test_a_split_that_moves_all_of_a_recordings_clips_clears_its_speaker(
    client, session_maker, extraction
) -> None:
    """Nothing of Dana remains in the recording, so her speaker keeps no voiceprint.

    Its old one was built from the clips that moved, and the voiceprint
    rebuild would otherwise average them back into Dana.
    """
    await _seed(
        session_maker,
        speakers=[
            {
                "label": "SPEAKER_00",
                "global_speaker_id": 7,
                "name": "Dana",
                "embedding": PERSON,
                "seconds": 25.0,
            }
        ],
        people=[{"id": 7, "name": "Dana", "embedding": PERSON}],
    )
    extraction.append({"embedding": _at_cosine(0.2), "speech_seconds": 25.0})

    response = await client.post(
        "/api/v1/speakers/7/split",
        json={
            "new_speaker_name": "Robin",
            "segments": [{"recording_id": REC, "start": 0.0, "end": 5.0}],
        },
    )

    assert response.status_code == 200
    assert response.json()["original_library_voiceprint"] == _outcome(
        "skipped_insufficient_speech", None
    )
    assert await _speaker_row(session_maker, "SPEAKER_00") == (None, None)
    assert await _person_voiceprint(session_maker, "Dana") is None


@pytest.mark.anyio
@pytest.mark.parametrize("failing_call", [0, 1])
async def test_a_split_whose_extraction_fails_changes_nothing(
    client, session_maker, extraction, failing_call
) -> None:
    await _seed(
        session_maker,
        speakers=[{"label": "SPEAKER_00", "global_speaker_id": 7, "name": "Dana"}],
        people=[{"id": 7, "name": "Dana", "embedding": PERSON}],
    )
    async with session_maker() as session:
        segments = [
            {"start": 0.0, "end": 5.0, "speaker": "SPEAKER_00", "text": "mine"},
            {"start": 5.0, "end": 10.0, "speaker": "SPEAKER_00", "text": "theirs"},
        ]
        await session.execute(
            text("UPDATE transcripts SET segments = :segments WHERE id = 1"),
            {"segments": json.dumps(segments)},
        )
        await session.commit()
    calls: list[Any] = [
        {"embedding": _at_cosine(0.2), "speech_seconds": 25.0},
        {"embedding": _at_cosine(0.95), "speech_seconds": 25.0},
    ]
    calls[failing_call] = TimeoutError("the worker never answered")
    extraction.extend(calls)

    response = await client.post(
        "/api/v1/speakers/7/split",
        json={
            "new_speaker_name": "Robin",
            "segments": [{"recording_id": REC, "start": 0.0, "end": 5.0}],
        },
    )

    assert response.status_code == 504
    async with session_maker() as session:
        names = (
            (await session.execute(text("SELECT name FROM global_speakers")))
            .scalars()
            .all()
        )
        labels = (
            (
                await session.execute(
                    text("SELECT diarization_label FROM recording_speakers")
                )
            )
            .scalars()
            .all()
        )
    assert (names, labels) == (["Dana"], ["SPEAKER_00"])
    assert await _person_voiceprint(session_maker, "Dana") == PERSON


async def _seed_split_across_two_recordings(session_maker, robin=None) -> None:
    """Dana speaks in two recordings, each with a line to split off and one to keep."""
    people = [{"id": 7, "name": "Dana", "embedding": PERSON}]
    if robin is not None:
        people.append({"id": 8, "name": "Robin", "embedding": robin})
    await _seed(
        session_maker,
        speakers=[{"label": "SPEAKER_00", "global_speaker_id": 7, "name": "Dana"}],
        people=people,
    )
    segments = [
        {"start": 0.0, "end": 5.0, "speaker": "SPEAKER_00", "text": "theirs"},
        {"start": 5.0, "end": 10.0, "speaker": "SPEAKER_00", "text": "mine"},
    ]
    async with session_maker() as session:
        await session.execute(
            text("UPDATE transcripts SET segments = :segments WHERE id = 1"),
            {"segments": json.dumps(segments)},
        )
        await session.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, status, upload_progress, processing_progress,
                    pipeline_generation, is_archived, is_deleted, user_id
                ) VALUES (2, :now, :now, 'Second', 'vp-rec-2', 'uid-2',
                          '/tmp/second.wav', 'PROCESSED', 100, 100, 'unified',
                          0, 0, 1)
                """
            ),
            {"now": NOW},
        )
        await session.execute(
            text(
                """
                INSERT INTO transcripts (
                    id, created_at, updated_at, recording_id, text, segments,
                    meeting_edge_status, notes_status, transcript_status
                ) VALUES (2, :now, :now, 2, 'text', :segments, 'idle',
                          'pending', 'completed')
                """
            ),
            {"now": NOW, "segments": json.dumps(segments)},
        )
        await session.execute(
            text(
                """
                INSERT INTO recording_speakers (
                    id, created_at, updated_at, public_id, recording_id,
                    global_speaker_id, diarization_label, name, embedding,
                    embedding_version, speaker_status, speaker_kind,
                    identity_locked
                ) VALUES (2, :now, :now, 'rs-2', 2, 7, 'SPEAKER_00', 'Dana',
                          NULL, NULL, 'active', 'automated', 0)
                """
            ),
            {"now": NOW},
        )
        await session.commit()


async def _split_across_two_recordings(client) -> Any:
    return await client.post(
        "/api/v1/speakers/7/split",
        json={
            "new_speaker_name": "Robin",
            "segments": [
                {"recording_id": recording_id, "start": 0.0, "end": 5.0}
                for recording_id in (REC, "vp-rec-2")
            ],
        },
    )


@pytest.mark.anyio
async def test_a_split_does_not_add_up_short_selections_across_recordings(
    client, session_maker, extraction
) -> None:
    """12 s in each of two recordings found nothing, for either person."""
    await _seed_split_across_two_recordings(session_maker)
    robin_voice, dana_voice = _at_cosine(0.2), _at_cosine(0.95)
    for voice in (robin_voice, robin_voice, dana_voice, dana_voice):
        extraction.append({"embedding": voice, "speech_seconds": 12.0})

    response = await _split_across_two_recordings(client)

    assert response.status_code == 200
    assert await _person_voiceprint(session_maker, "Robin") is None


@pytest.mark.anyio
async def test_a_split_into_an_existing_person_folds_only_selections_that_meet_the_floor(
    client, session_maker, extraction
) -> None:
    robin = _at_cosine(0.3)
    await _seed_split_across_two_recordings(session_maker, robin=robin)
    near_robin = _at_cosine(0.25)
    for voice, seconds in (
        (near_robin, 12.0),
        (near_robin, 12.0),
        (_at_cosine(0.95), 25.0),
        (_at_cosine(0.95), 25.0),
    ):
        extraction.append({"embedding": voice, "speech_seconds": seconds})

    response = await _split_across_two_recordings(client)

    assert response.status_code == 200
    assert await _person_voiceprint(session_maker, "Robin") == pytest.approx(robin)


@pytest.mark.anyio
async def test_a_split_into_an_existing_person_folds_and_locks_their_voiceprint(
    client, session_maker, extraction
) -> None:
    robin = _at_cosine(0.3)
    await _seed_split_across_two_recordings(session_maker, robin=robin)
    near_robin = _at_cosine(0.25)
    for voice in (near_robin, near_robin, _at_cosine(0.95), _at_cosine(0.95)):
        extraction.append({"embedding": voice, "speech_seconds": 25.0})

    response = await _split_across_two_recordings(client)

    assert response.status_code == 200
    assert response.json()["library_voiceprint"] == _outcome("folded", 25.0)
    assert await _person_voiceprint(session_maker, "Robin") == pytest.approx(
        _blend(robin, near_robin, 0.1)
    )
    async with session_maker() as session:
        locked = (
            await session.execute(
                text("SELECT is_voiceprint_locked FROM global_speakers WHERE id = 8")
            )
        ).scalar_one()
    assert locked


def test_a_refusal_is_for_one_person_and_lifts_when_the_speaker_moves_on() -> None:
    from backend.processing.embedding import is_drift_refused

    def link(person: int | None, *refused_for: int) -> SimpleNamespace:
        return SimpleNamespace(
            global_speaker_id=person,
            drift_refusals=[
                SimpleNamespace(global_speaker_id=person_id)
                for person_id in refused_for
            ],
        )

    assert is_drift_refused(link(7, 7))
    assert is_drift_refused(link(7, 8, 7))
    assert not is_drift_refused(link(8, 7))
    assert not is_drift_refused(link(None, 7))
    assert not is_drift_refused(link(7))
    assert not is_drift_refused(link(None))


async def _add_second_recording(session_maker) -> None:
    async with session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, status, upload_progress, processing_progress,
                    pipeline_generation, is_archived, is_deleted, user_id
                ) VALUES (2, :now, :now, 'Second', 'vp-rec-2', 'uid-2',
                          '/tmp/second.wav', 'PROCESSED', 100, 100, 'unified',
                          0, 0, 1)
                """
            ),
            {"now": NOW},
        )
        await session.execute(
            text(
                """
                INSERT INTO recording_speakers (
                    id, created_at, updated_at, public_id, recording_id,
                    global_speaker_id, diarization_label, name, embedding,
                    embedding_version, speaker_status, speaker_kind,
                    identity_locked
                ) VALUES (2, :now, :now, 'rs-2', 2, 7, 'SPEAKER_09', 'Dana',
                          NULL, NULL, 'active', 'automated', 0)
                """
            ),
            {"now": NOW},
        )
        await _insert_refusals(session, 2, 7)
        await session.commit()


@pytest.mark.anyio
async def test_recalibration_uses_the_samples_chosen_and_lets_their_links_back_in(
    client, session_maker, extraction, monkeypatch
) -> None:
    """Choosing and hearing a recording's samples overrides the guard's refusal.

    Dana's links in both recordings were refused against a poor voiceprint.
    The recording whose samples hold enough speech is used, and its link
    counts towards her voiceprint again. The other recording's samples cover
    her line there too, but held too little speech to be used, so its
    refusal stands. The used link's refusal for Eli stands too: hearing it
    as Dana says nothing about Eli.
    """
    from backend.api.v1.endpoints.speakers import routes_global

    monkeypatch.setattr(
        routes_global,
        "select_recording_audio_for_embedding",
        lambda recording: recording.audio_path,
    )
    await _seed(
        session_maker,
        speakers=[
            {"label": "SPEAKER_00", "global_speaker_id": 7, "refused_for": (7, 8)}
        ],
        people=[
            {"id": 7, "name": "Dana", "embedding": PERSON},
            {"id": 8, "name": "Eli"},
        ],
    )
    await _add_second_recording(session_maker)
    second_lines = [
        {
            "start": 0.0,
            "end": 5.0,
            "speaker": "SPEAKER_09",
            "text": "line 0",
            "segment_source": "legacy",
        }
    ]
    async with session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO transcripts (
                    id, created_at, updated_at, recording_id, text, segments,
                    meeting_edge_status, notes_status, transcript_status
                ) VALUES (2, :now, :now, 2, 'text', :segments, 'idle',
                          'pending', 'completed')
                """
            ),
            {"now": NOW, "segments": json.dumps(second_lines)},
        )
        await session.commit()
    genuine = _at_cosine(0.2)
    extraction.append({"embedding": genuine, "speech_seconds": 25.0})
    extraction.append({"embedding": genuine, "speech_seconds": 12.0})

    response = await client.post(
        "/api/v1/speakers/7/recalibrate",
        json=[
            {"recording_id": recording_id, "start": 0.0, "end": 5.0}
            for recording_id in (REC, "vp-rec-2")
        ],
    )

    assert response.status_code == 200
    assert await _person_voiceprint(session_maker, "Dana") == pytest.approx(genuine)
    assert await _refused_for(session_maker) == {
        "SPEAKER_00": (8,),
        "SPEAKER_09": (7,),
    }


@pytest.mark.anyio
async def test_recalibration_lifts_only_the_refusals_of_the_speakers_heard(
    client, session_maker, extraction, monkeypatch
) -> None:
    """Dana has two refused speakers in one recording, and one was sampled.

    Choosing and hearing SPEAKER_00's lines says nothing about SPEAKER_01's,
    so SPEAKER_01 stays out of her voiceprint, and nothing about her speaker
    of the same label in another recording either. A line of Eli's among the
    samples lifts nothing: its speaker isn't Dana's, and its refusal for her
    stands should it come back to her.
    """
    from backend.api.v1.endpoints.speakers import routes_global

    monkeypatch.setattr(
        routes_global,
        "select_recording_audio_for_embedding",
        lambda recording: recording.audio_path,
    )
    await _seed(
        session_maker,
        speakers=[
            {"label": "SPEAKER_00", "global_speaker_id": 7, "refused_for": 7},
            {"label": "SPEAKER_01", "global_speaker_id": 7, "refused_for": 7},
            {"label": "SPEAKER_02", "global_speaker_id": 8, "refused_for": 7},
        ],
        people=[
            {"id": 7, "name": "Dana", "embedding": PERSON},
            {"id": 8, "name": "Eli"},
        ],
    )
    async with session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, status, upload_progress, processing_progress,
                    pipeline_generation, is_archived, is_deleted, user_id
                ) VALUES (2, :now, :now, 'Second', 'vp-rec-2', 'uid-2',
                          '/tmp/second.wav', 'PROCESSED', 100, 100, 'unified',
                          0, 0, 1)
                """
            ),
            {"now": NOW},
        )
        await session.execute(
            text(
                """
                INSERT INTO recording_speakers (
                    id, created_at, updated_at, public_id, recording_id,
                    global_speaker_id, diarization_label, name,
                    speaker_status, speaker_kind, identity_locked
                ) VALUES (10, :now, :now, 'rs-10', 2, 7, 'SPEAKER_00', 'Dana',
                          'active', 'automated', 0)
                """
            ),
            {"now": NOW},
        )
        await _insert_refusals(session, 10, 7)
        await session.commit()
    extraction.append({"embedding": _at_cosine(0.2), "speech_seconds": 25.0})

    response = await client.post(
        "/api/v1/speakers/7/recalibrate",
        json=[
            {"recording_id": REC, "start": 0.0, "end": 5.0},
            {"recording_id": REC, "start": 10.0, "end": 15.0},
        ],
    )

    assert response.status_code == 200
    assert await _refusals_by_id(session_maker) == {
        1: (),
        2: (7,),
        3: (7,),
        10: (7,),
    }


@pytest.mark.anyio
async def test_recalibration_lifts_a_refusal_by_the_speaker_a_line_names(
    client, session_maker, extraction, monkeypatch
) -> None:
    """Both of Dana's speakers carry her display name on their lines.

    A line that names its recording speaker lifts only that speaker's refusal,
    whatever its label says: matched by name, the sample of SPEAKER_00's line
    would also let SPEAKER_01 back into her voiceprint unheard.
    """
    from backend.api.v1.endpoints.speakers import routes_global

    monkeypatch.setattr(
        routes_global,
        "select_recording_audio_for_embedding",
        lambda recording: recording.audio_path,
    )
    await _seed(
        session_maker,
        speakers=[
            {
                "label": label,
                "global_speaker_id": 7,
                "name": "Dana",
                "refused_for": 7,
            }
            for label in ("SPEAKER_00", "SPEAKER_01")
        ],
        people=[{"id": 7, "name": "Dana", "embedding": PERSON}],
    )
    segments = [
        {
            "start": float(index * 5),
            "end": float(index * 5 + 5),
            "speaker": "Dana",
            "recording_speaker_id": index + 1,
            "text": f"line {index}",
            "segment_source": "legacy",
        }
        for index in range(2)
    ]
    async with session_maker() as session:
        await session.execute(
            text("UPDATE transcripts SET segments = :segments WHERE id = 1"),
            {"segments": json.dumps(segments)},
        )
        await session.commit()
    extraction.append({"embedding": _at_cosine(0.2), "speech_seconds": 25.0})

    response = await client.post(
        "/api/v1/speakers/7/recalibrate",
        json=[{"recording_id": REC, "start": 0.0, "end": 5.0}],
    )

    assert response.status_code == 200
    assert await _refused_for(session_maker) == {"SPEAKER_00": (), "SPEAKER_01": (7,)}


@pytest.mark.anyio
async def test_a_split_drops_the_refusal_of_the_voiceprint_it_replaces(
    client, session_maker, extraction
) -> None:
    """The guard refused Dana's speaker; the split gives it a new voiceprint.

    The refusal described the old voiceprint, moved clips included, so it
    goes with it, and what remains rebuilds Dana. Kept, it left the remainder
    out and cleared her.
    """
    await _seed(
        session_maker,
        speakers=[
            {
                "label": "SPEAKER_00",
                "global_speaker_id": 7,
                "name": "Dana",
                "refused_for": 7,
            }
        ],
        people=[{"id": 7, "name": "Dana", "embedding": PERSON}],
    )
    async with session_maker() as session:
        segments = [
            {"start": 0.0, "end": 5.0, "speaker": "SPEAKER_00", "text": "theirs"},
            {"start": 5.0, "end": 10.0, "speaker": "SPEAKER_00", "text": "mine"},
        ]
        await session.execute(
            text("UPDATE transcripts SET segments = :segments WHERE id = 1"),
            {"segments": json.dumps(segments)},
        )
        await session.commit()
    remaining = _at_cosine(0.95)
    extraction.append({"embedding": _at_cosine(0.2), "speech_seconds": 25.0})
    extraction.append({"embedding": remaining, "speech_seconds": 25.0})

    response = await client.post(
        "/api/v1/speakers/7/split",
        json={
            "new_speaker_name": "Robin",
            "segments": [{"recording_id": REC, "start": 0.0, "end": 5.0}],
        },
    )

    assert response.status_code == 200
    assert response.json()["original_library_voiceprint"] == _outcome("saved", 25.0)
    assert await _person_voiceprint(session_maker, "Dana") == pytest.approx(remaining)
    assert await _speaker_row(session_maker, "SPEAKER_00") == (
        pytest.approx(remaining),
        25.0,
    )
    assert (await _refused_for(session_maker))["SPEAKER_00"] == ()


@pytest.mark.anyio
async def test_a_split_leaves_out_a_refused_speaker_whose_voiceprint_it_keeps(
    client, session_maker, extraction
) -> None:
    """Dana has two speakers in the split recording, and one was refused.

    The split rebuilds neither speaker's voiceprint (only a recording's
    single speaker of hers), so the refusal stands, and the remainder, which
    holds the refused speaker's speech, stays out of her voiceprint.
    """
    await _seed(
        session_maker,
        speakers=[
            {"label": "SPEAKER_00", "global_speaker_id": 7, "name": "Dana"},
            {
                "label": "SPEAKER_01",
                "global_speaker_id": 7,
                "name": "Dana",
                "refused_for": 7,
            },
        ],
        people=[{"id": 7, "name": "Dana", "embedding": PERSON}],
    )

    response = await _split_one_line_off(client, extraction, 25.0)

    assert response.status_code == 200
    # The left-out remainder is not offered at all, so there is no figure.
    assert response.json()["original_library_voiceprint"] == _outcome(
        "skipped_insufficient_speech", None
    )
    assert await _person_voiceprint(session_maker, "Dana") is None
    refused = await _refused_for(session_maker)
    assert (refused["SPEAKER_00"], refused["SPEAKER_01"]) == ((), (7,))


@pytest.mark.anyio
async def test_a_split_that_removes_what_the_guard_refused_rebuilds_the_person(
    client, session_maker, extraction
) -> None:
    """The review's Dana: one speaker of hers holds Robin's speech too.

    Naming the speaker Dana is refused by the guard, since the mixed
    voiceprint sounds too unlike hers. Splitting Robin's clips off leaves 25 s
    of Dana, which rebuilds her voiceprint; the refusal, made against the
    mixed voiceprint, is gone with it.
    """
    mixed = _at_cosine(0.65)
    await _seed(
        session_maker,
        speakers=[{"label": "SPEAKER_00", "embedding": mixed, "seconds": 50.0}],
        people=[{"id": 7, "name": "Dana", "embedding": PERSON}],
    )
    async with session_maker() as session:
        segments = [
            {"start": 0.0, "end": 5.0, "speaker": "SPEAKER_00", "text": "Robin"},
            {"start": 5.0, "end": 10.0, "speaker": "SPEAKER_00", "text": "Dana"},
        ]
        await session.execute(
            text("UPDATE transcripts SET segments = :segments WHERE id = 1"),
            {"segments": json.dumps(segments)},
        )
        await session.commit()

    renamed = await client.put(
        f"/api/v1/speakers/recordings/{REC}",
        json={"diarization_label": "SPEAKER_00", "global_speaker_name": "Dana"},
    )
    assert renamed.json()[0]["library_voiceprint"]["status"] == "skipped_drift"
    assert (await _refused_for(session_maker))["SPEAKER_00"] == (7,)

    split = await _split_one_line_off(client, extraction, 25.0)

    assert split.status_code == 200
    assert split.json()["original_library_voiceprint"] == _outcome("saved", 25.0)
    assert await _person_voiceprint(session_maker, "Dana") == pytest.approx(
        _at_cosine(0.95)
    )
    assert (await _refused_for(session_maker))["SPEAKER_00"] == ()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "endpoint",
    ["speakers/SPEAKER_00/voiceprint/extract", "voiceprints/extract-all"],
)
@pytest.mark.parametrize(
    # The voiceprint extracted, and the people SPEAKER_00 is refused for after.
    "case",
    [
        pytest.param((_at_cosine(0.1), (7,)), id="stranger"),
        pytest.param((_at_cosine(0.9), ()), id="like-dana"),
    ],
)
async def test_extracting_a_refused_speakers_voiceprint_again_judges_it(
    client, session_maker, extraction, endpoint, case
) -> None:
    """SPEAKER_00 was refused for Dana, and its voiceprint has been deleted
    since (extract-all extracts only speakers who have none).

    Extracting one again, with no link applied after it, gives the speaker a
    voiceprint no one offered to Dana, so the guard judges it against her. A
    stranger's stays refused, and a rebuild of Dana from her links leaves it
    out; her own voice is let back in, and the rebuild uses it.
    """
    extracted_voiceprint, refused_after = case
    await _seed(
        session_maker,
        speakers=[
            {
                "label": "SPEAKER_00",
                "global_speaker_id": 7,
                "embedding": None,
                "refused_for": 7,
            }
        ],
        people=[{"id": 7, "name": "Dana", "embedding": PERSON}],
    )
    extraction.append({"embedding": extracted_voiceprint, "speech_seconds": 30.0})

    extracted = await client.post(f"/api/v1/speakers/recordings/{REC}/{endpoint}")
    await _rebuild_person(session_maker, 7)

    assert extracted.status_code == 200
    assert await _refused_for(session_maker) == {"SPEAKER_00": refused_after}
    dana = PERSON if refused_after else extracted_voiceprint
    assert await _person_voiceprint(session_maker, "Dana") == pytest.approx(dana)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "endpoint",
    ["speakers/SPEAKER_00/voiceprint/extract", "voiceprints/extract-all"],
)
async def test_an_extracted_voiceprint_is_judged_for_the_person_linked_when_it_lands(
    client, session_maker, monkeypatch, endpoint
) -> None:
    """SPEAKER_00 is linked to Dana and has no voiceprint. While its extraction
    runs, a rename links it to Eli; with no voiceprint it offers him nothing.

    The extraction returns a voice unlike both of them. It is judged against
    Eli, the person the speaker is linked to when it lands, so Eli's rebuild
    leaves it out; judged against Dana instead, it would become his voiceprint.
    """
    from backend.api.v1.endpoints import speakers as speakers_module

    await _seed(
        session_maker,
        speakers=[{"label": "SPEAKER_00", "global_speaker_id": 7, "embedding": None}],
        people=[
            {"id": 7, "name": "Dana", "embedding": PERSON},
            {"id": 8, "name": "Eli", "embedding": ELI},
        ],
    )
    loop = asyncio.get_running_loop()

    async def rename_to_eli() -> None:
        async with session_maker() as other:
            await other.execute(
                text(
                    "UPDATE recording_speakers SET global_speaker_id = 8, "
                    "name = 'Eli' WHERE id = 1"
                )
            )
            await other.commit()

    def _send_task(name, args=None, kwargs=None, **options):
        class _Result:
            def get(self, timeout=None):
                # The worker's wait runs in a thread; the rename lands during it.
                asyncio.run_coroutine_threadsafe(rename_to_eli(), loop).result()
                return {"embedding": [-0.6, -0.8], "speech_seconds": 30.0}

        return _Result()

    monkeypatch.setattr(speakers_module.celery_app, "send_task", _send_task)

    extracted = await client.post(f"/api/v1/speakers/recordings/{REC}/{endpoint}")
    await _rebuild_person(session_maker, 8)

    assert extracted.status_code == 200
    assert await _refused_for(session_maker) == {"SPEAKER_00": (8,)}
    assert await _person_voiceprint(session_maker, "Eli") == pytest.approx(ELI)


@pytest.mark.anyio
async def test_a_split_into_an_existing_person_records_a_refused_fold_on_the_links(
    client, session_maker, extraction
) -> None:
    robin = _at_cosine(0.3)
    await _seed_split_across_two_recordings(session_maker, robin=robin)
    unlike_robin = _at_cosine(0.95)
    for voice in (unlike_robin, unlike_robin, _at_cosine(0.95), _at_cosine(0.95)):
        extraction.append({"embedding": voice, "speech_seconds": 25.0})

    response = await _split_across_two_recordings(client)

    assert response.status_code == 200
    assert response.json()["library_voiceprint"] == _outcome("skipped_drift", 25.0)
    refused = await _refusals_by_id(session_maker)
    async with session_maker() as session:
        split_links = (
            await session.execute(
                text(
                    "SELECT id FROM recording_speakers "
                    "WHERE diarization_label LIKE 'SPLIT_%'"
                )
            )
        ).scalars()
        assert [refused[link] for link in split_links.all()] == [(8,), (8,)]


async def _seed_split_with_other_recordings(
    session_maker, others: list[dict[str, Any]], *, locked: bool
) -> None:
    """Dana in the recording being split, and in one more recording per ``others``."""
    await _seed(
        session_maker,
        speakers=[{"label": "SPEAKER_00", "global_speaker_id": 7, "name": "Dana"}],
        people=[{"id": 7, "name": "Dana", "embedding": PERSON, "locked": locked}],
    )
    segments = [
        {"start": 0.0, "end": 5.0, "speaker": "SPEAKER_00", "text": "theirs"},
        {"start": 5.0, "end": 10.0, "speaker": "SPEAKER_00", "text": "mine"},
    ]
    async with session_maker() as session:
        await session.execute(
            text("UPDATE transcripts SET segments = :segments WHERE id = 1"),
            {"segments": json.dumps(segments)},
        )
        for offset, other in enumerate(others, start=2):
            await session.execute(
                text(
                    """
                    INSERT INTO recordings (
                        id, created_at, updated_at, name, public_id, meeting_uid,
                        audio_path, status, upload_progress, processing_progress,
                        pipeline_generation, is_archived, is_deleted, user_id
                    ) VALUES (:id, :now, :now, 'Other', :public_id, :uid,
                              '/tmp/other.wav', 'PROCESSED', 100, 100, 'unified',
                              0, 0, 1)
                    """
                ),
                {
                    "now": NOW,
                    "id": offset,
                    "public_id": f"vp-other-{offset}",
                    "uid": f"uid-{offset}",
                },
            )
            await session.execute(
                text(
                    """
                    INSERT INTO recording_speakers (
                        id, created_at, updated_at, public_id, recording_id,
                        global_speaker_id, diarization_label, name, embedding,
                        embedding_version, embedding_speech_seconds,
                        speaker_status, speaker_kind, identity_locked
                    ) VALUES (:id, :now, :now, :public_id, :id, 7, 'SPEAKER_00',
                              'Dana', :embedding, :version, :seconds,
                              'active', 'automated', 0)
                    """
                ),
                {
                    "now": NOW,
                    "id": offset,
                    "public_id": f"rs-{offset}",
                    "embedding": json.dumps(other["embedding"]),
                    "version": other.get("version", 2),
                    "seconds": other.get("seconds"),
                },
            )
            await _insert_refusals(session, offset, other.get("refused_for"))
        await session.commit()


async def _person_row(session_maker, person_id: int) -> tuple[Any, Any, bool]:
    async with session_maker() as session:
        embedding, version, locked = (
            await session.execute(
                text(
                    "SELECT embedding, embedding_version, is_voiceprint_locked "
                    "FROM global_speakers WHERE id = :id"
                ),
                {"id": person_id},
            )
        ).one()
    return (json.loads(embedding) if embedding else None), version, bool(locked)


async def _split_one_line_off(client, extraction, remaining_seconds: float) -> Any:
    extraction.append({"embedding": _at_cosine(0.2), "speech_seconds": 25.0})
    extraction.append(
        {"embedding": _at_cosine(0.95), "speech_seconds": remaining_seconds}
    )
    return await client.post(
        "/api/v1/speakers/7/split",
        json={
            "new_speaker_name": "Robin",
            "segments": [{"recording_id": REC, "start": 0.0, "end": 5.0}],
        },
    )


@pytest.mark.anyio
@pytest.mark.parametrize("locked", [False, True])
async def test_a_split_rebuilds_the_original_from_their_other_recordings(
    client, session_maker, extraction, locked
) -> None:
    """Too little of Dana remains in the split recording, but not elsewhere.

    Her voiceprint is rebuilt from the other recordings that qualify, pooled
    as a recalibration pools them, rather than cleared. A refused link, a
    short one and a stale one don't qualify. The lock stays as it was.
    """
    elsewhere = _at_cosine(0.9)
    await _seed_split_with_other_recordings(
        session_maker,
        [
            {"embedding": elsewhere, "seconds": 30.0},
            {"embedding": _at_cosine(0.1), "seconds": 30.0, "refused_for": 7},
            {"embedding": _at_cosine(0.1), "seconds": 12.0},
            {"embedding": _at_cosine(0.1), "seconds": 30.0, "version": 1},
        ],
        locked=locked,
    )

    response = await _split_one_line_off(client, extraction, 12.0)

    assert response.status_code == 200
    assert response.json()["original_library_voiceprint"] == _outcome(
        "saved_from_other_recordings", 30.0
    )
    assert await _person_row(session_maker, 7) == (
        pytest.approx(elsewhere),
        2,
        locked,
    )


@pytest.mark.anyio
async def test_a_split_rebuilds_the_original_from_no_merged_row(
    client, session_maker, extraction
) -> None:
    """A row merged into another keeps its voiceprint and link, not its place.

    A merge made by processing folds nothing and leaves the merged row's own
    voiceprint on it, so it is the cluster it was before the merge. The
    rebuild leaves merged rows out, and so does the split's fallback.
    """
    elsewhere = _at_cosine(0.9)
    await _seed_split_with_other_recordings(
        session_maker, [{"embedding": elsewhere, "seconds": 30.0}], locked=False
    )
    async with session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO recording_speakers (
                    id, created_at, updated_at, public_id, recording_id,
                    global_speaker_id, diarization_label, name, embedding,
                    embedding_version, embedding_speech_seconds, merged_into_id,
                    speaker_status, speaker_kind, identity_locked
                ) VALUES (10, :now, :now, 'rs-10', 2, 7, 'SPEAKER_01', 'Dana',
                          :embedding, 2, 30.0, 2, 'active', 'automated', 0)
                """
            ),
            {"now": NOW, "embedding": json.dumps(_at_cosine(0.1))},
        )
        await session.commit()

    response = await _split_one_line_off(client, extraction, 12.0)

    assert response.json()["original_library_voiceprint"] == _outcome(
        "saved_from_other_recordings", 30.0
    )
    assert await _person_voiceprint(session_maker, "Dana") == pytest.approx(elsewhere)


@pytest.mark.anyio
async def test_a_split_clears_the_original_only_when_no_link_qualifies(
    client, session_maker, extraction
) -> None:
    """Cleared, and still of the current version, so the rebuild leaves it be.

    A voiceprint of no version reads as legacy, and the six-hourly rebuild
    would found a new one from Dana's links, unlocked, undoing the split.
    """
    await _seed_split_with_other_recordings(
        session_maker,
        [{"embedding": _at_cosine(0.1), "seconds": 12.0}],
        locked=True,
    )

    response = await _split_one_line_off(client, extraction, 12.0)

    assert response.json()["original_library_voiceprint"] == _outcome(
        "skipped_insufficient_speech", 12.0
    )
    assert await _person_row(session_maker, 7) == (None, 2, False)


@pytest.mark.anyio
async def test_a_split_gives_the_remainder_to_the_original_beside_a_merged_row(
    client, session_maker, extraction
) -> None:
    """A speaker merged into Dana's is still linked to her, but it isn't one.

    Counted as a second speaker of hers, it stopped her one speaker taking the
    remainder's voiceprint, which kept the moved clips in it for the rebuild
    to average back into her.
    """
    await _seed(
        session_maker,
        speakers=[
            {
                "label": "SPEAKER_00",
                "global_speaker_id": 7,
                "name": "Dana",
                "embedding": _at_cosine(0.2),
                "seconds": 30.0,
            },
            {"label": "SPEAKER_01", "global_speaker_id": 7, "name": "Dana"},
        ],
        people=[{"id": 7, "name": "Dana", "embedding": PERSON}],
    )
    segments = [
        {"start": 0.0, "end": 5.0, "speaker": "SPEAKER_00", "text": "theirs"},
        {"start": 5.0, "end": 10.0, "speaker": "SPEAKER_00", "text": "mine"},
    ]
    async with session_maker() as session:
        await session.execute(
            text(
                "UPDATE recording_speakers SET merged_into_id = 1 "
                "WHERE diarization_label = 'SPEAKER_01'"
            )
        )
        await session.execute(
            text("UPDATE transcripts SET segments = :segments WHERE id = 1"),
            {"segments": json.dumps(segments)},
        )
        await session.commit()

    response = await _split_one_line_off(client, extraction, 25.0)

    assert response.status_code == 200
    assert await _speaker_row(session_maker, "SPEAKER_00") == (
        pytest.approx(_at_cosine(0.95)),
        25.0,
    )


@pytest.mark.anyio
async def test_a_split_that_rebuilds_the_original_leaves_their_lock_as_it_was(
    client, session_maker, extraction
) -> None:
    await _seed_split_with_other_recordings(session_maker, [], locked=False)

    response = await _split_one_line_off(client, extraction, 25.0)

    assert response.json()["original_library_voiceprint"] == _outcome("saved", 25.0)
    assert await _person_row(session_maker, 7) == (
        pytest.approx(_at_cosine(0.95)),
        2,
        False,
    )


@pytest.mark.anyio
async def test_a_split_never_rebuilds_the_original_from_the_split_recording_itself(
    client, session_maker, extraction
) -> None:
    """Dana has two speakers in the split recording, with voiceprints from before.

    Neither is rebuilt by the split (only a recording's single speaker is),
    so both still hold the moved clips and must not stand in for her other
    recordings.
    """
    await _seed(
        session_maker,
        speakers=[
            {
                "label": label,
                "global_speaker_id": 7,
                "name": "Dana",
                "embedding": _at_cosine(0.2),
                "seconds": 30.0,
            }
            for label in ("SPEAKER_00", "SPEAKER_01")
        ],
        people=[{"id": 7, "name": "Dana", "embedding": PERSON}],
    )

    response = await _split_one_line_off(client, extraction, 12.0)

    assert response.json()["original_library_voiceprint"] == _outcome(
        "skipped_insufficient_speech", 12.0
    )
    assert await _person_voiceprint(session_maker, "Dana") is None


async def _rebuild_person(session_maker, person_id: int) -> None:
    """Rebuild a person's voiceprint from their links, as the rebuild task does."""
    from sqlmodel import Session as SyncSession

    from backend.models.speaker import GlobalSpeaker
    from backend.processing.embedding_version import EMBEDDING_METHOD_VERSION
    from backend.worker.tasks.voiceprint_rebuild import _rebuild_global_speaker

    engine = session_maker.kw["bind"]
    async with AsyncSession(engine, sync_session_class=SyncSession) as session:
        await session.run_sync(
            lambda sync: _rebuild_global_speaker(
                sync, sync.get(GlobalSpeaker, person_id), EMBEDDING_METHOD_VERSION
            )
        )
        await session.commit()


def _unit(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector]


@pytest.mark.anyio
@pytest.mark.parametrize(
    # Dana's voiceprint, the refused target's, the source's, and the people
    # the target is refused for after the merge.
    "case",
    [
        pytest.param(
            (_at_cosine(0.1), PERSON, _at_cosine(0.95), (7,)), id="refused-again"
        ),
        pytest.param(
            (PERSON, _at_cosine(0.65), _at_cosine(0.986), ()), id="let-back-in"
        ),
        # A fold the guard refuses changes nothing, nor does the refusal.
        pytest.param(
            (_at_cosine(0.1), PERSON, _at_cosine(0.65), (7,)), id="fold-refused"
        ),
        # Dana has no voiceprint to judge the result against.
        pytest.param((None, PERSON, _at_cosine(0.95), (7,)), id="no-voiceprint"),
    ],
)
async def test_a_merge_into_a_refused_speaker_gets_a_new_verdict(
    client, session_maker, write_path, case
) -> None:
    """SPEAKER_00 was refused for Dana. Merging SPEAKER_01 into it folds more
    speech into its voiceprint without offering it to anyone, so the guard
    judges the result against Dana. While it is refused, a rebuild of Dana
    from her links leaves it out; once let back in, the rebuild uses it."""
    dana, target, source, refused_after = case
    await _seed(
        session_maker,
        speakers=[
            {
                "label": "SPEAKER_00",
                "global_speaker_id": 7,
                "embedding": target,
                "seconds": 25.0,
                "refused_for": 7,
            },
            {"label": "SPEAKER_01", "embedding": source, "seconds": 25.0},
        ],
        people=[{"id": 7, "name": "Dana", "embedding": dana}],
    )

    merged = await client.post(
        f"/api/v1/speakers/recordings/{REC}/merge",
        json={
            "target_speaker_label": "SPEAKER_00",
            "source_speaker_label": "SPEAKER_01",
        },
    )
    await _rebuild_person(session_maker, 7)

    assert merged.status_code == 200
    assert (await _refused_for(session_maker))["SPEAKER_00"] == refused_after
    if refused_after:
        assert await _person_voiceprint(session_maker, "Dana") == dana
    else:
        merged_voiceprint, _ = await _speaker_row(session_maker, "SPEAKER_00")
        assert await _person_voiceprint(session_maker, "Dana") == pytest.approx(
            _unit(merged_voiceprint)
        )


@pytest.mark.anyio
async def test_merging_into_a_speaker_without_a_voiceprint_keeps_what_it_gave(
    client, session_maker, write_path
) -> None:
    """The target takes the source's voiceprint and figure; the source row
    keeps that figure, so undoing the merge takes all of it back out."""
    await _seed(
        session_maker,
        speakers=[
            {"label": "SPEAKER_00", "embedding": None, "seconds": None},
            {"label": "SPEAKER_01", "embedding": _at_cosine(0.9), "seconds": 25.0},
        ],
    )

    merged = await client.post(
        f"/api/v1/speakers/recordings/{REC}/merge",
        json={
            "target_speaker_label": "SPEAKER_00",
            "source_speaker_label": "SPEAKER_01",
        },
    )

    assert merged.status_code == 200
    assert await _speaker_row(session_maker, "SPEAKER_00") == (
        pytest.approx(_at_cosine(0.9)),
        25.0,
    )
    assert await _speaker_row(session_maker, "SPEAKER_01") == (None, 25.0)
