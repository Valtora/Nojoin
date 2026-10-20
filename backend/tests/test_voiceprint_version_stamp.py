"""A voiceprint written into a person carries the version it was extracted with.

Identification only compares voiceprints of one extraction method version, so a
person whose voiceprint is stamped with the wrong version (or none) is ignored
until the voiceprint rebuild task runs. Each test founds or rewrites a person's
voiceprint through one path and then asks identification for the same voice.
"""

from __future__ import annotations

import json
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
from sqlmodel.ext.asyncio.session import AsyncSession as SQLModelAsyncSession

from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.core.backup.restore.conflicts import _resolve_global_speakers
from backend.models.recording import Recording
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.processing.embedding import (
    find_matching_global_speaker,
    merge_embeddings,
)
from backend.tests.sqlite_schemas import (
    DIARIZATION_WINDOW_RESULTS_SCHEMA,
    DIARIZATION_WINDOW_TURNS_SCHEMA,
    GLOBAL_SPEAKERS_SCHEMA,
    P_TAGS_SCHEMA,
    PEOPLE_TAGS_SCHEMA,
    PROCESSING_RUNS_SCHEMA,
    RECORDING_AUDIO_WINDOW_MANIFESTS_SCHEMA,
    RECORDING_SPEAKER_ALIASES_SCHEMA,
    RECORDING_SPEAKERS_SCHEMA,
    RECORDINGS_SCHEMA,
    SPEAKER_CORRECTION_EVENTS_SCHEMA,
    TRANSCRIPT_UTTERANCE_EVENTS_SCHEMA,
    TRANSCRIPT_UTTERANCES_SCHEMA,
    TRANSCRIPTS_SCHEMA,
)
from backend.worker.tasks.speaker_assignment import _matchable_global_speakers

VOICE = [1.0, 0.0]
OTHER = [0.0, 1.0]
NOW = "2026-10-20 00:00:00"
REC = "vs-rec"
CURRENT = 2
ALICE = 1


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
        id=ALICE, username="alice", settings={}, force_password_change=False
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
    """Queue the voiceprints the worker hands back, one per extraction call."""
    from backend.api.v1.endpoints import speakers as speakers_module
    from backend.api.v1.endpoints.speakers import routes_global

    monkeypatch.setattr(
        routes_global,
        "select_recording_audio_for_embedding",
        lambda recording: recording.audio_path,
    )
    results: deque = deque()

    def _send_task(name, args=None, kwargs=None, **options):
        result = results.popleft()

        class _Result:
            def get(self, timeout=None):
                return result

        return _Result()

    monkeypatch.setattr(speakers_module.celery_app, "send_task", _send_task)
    return results


def _json_or_none(value: Any) -> str | None:
    return json.dumps(value) if value is not None else None


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
                    ) VALUES (:id, :now, :now, 1, :name, :embedding, :version,
                              :locked)
                    """
                ),
                {
                    "now": NOW,
                    "id": person["id"],
                    "name": person["name"],
                    "embedding": _json_or_none(person.get("embedding")),
                    "version": person.get("version"),
                    "locked": person.get("locked", False),
                },
            )
        for index, speaker in enumerate(speakers, start=1):
            await session.execute(
                text(
                    """
                    INSERT INTO recording_speakers (
                        id, created_at, updated_at, public_id, recording_id,
                        global_speaker_id, diarization_label, local_name, name,
                        embedding, embedding_version, speaker_status,
                        speaker_kind, identity_locked
                    ) VALUES (
                        :id, :now, :now, :public_id, 1, :global_id, :label,
                        :local_name, :name, :embedding, :version,
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
                    "embedding": _json_or_none(speaker.get("embedding")),
                    "version": speaker.get("version", CURRENT),
                },
            )
        await session.commit()


async def _person(session_maker, name: str) -> SimpleNamespace:
    async with session_maker() as session:
        embedding, version = (
            await session.execute(
                text(
                    "SELECT embedding, embedding_version FROM global_speakers "
                    "WHERE name = :name"
                ),
                {"name": name},
            )
        ).one()
    return SimpleNamespace(
        name=name,
        embedding=json.loads(embedding) if embedding is not None else None,
        embedding_version=version,
    )


async def _identified(session_maker, name: str, voice: list[float]) -> bool:
    """Whether identification picks ``name`` for ``voice``.

    The candidates come from the loader the pipeline itself uses, so a person
    it would never load can't pass.
    """
    async with SQLModelAsyncSession(session_maker.kw["bind"]) as session:
        people = await session.run_sync(
            lambda sync_session: _matchable_global_speakers(sync_session, ALICE)
        )
    match, _score = find_matching_global_speaker(voice, people)
    return match is not None and match.name == name


@pytest.mark.anyio
async def test_a_person_founded_by_a_rename_is_identified(
    client, session_maker, write_path
) -> None:
    await _seed(
        session_maker,
        speakers=[{"label": "SPEAKER_00", "embedding": VOICE}],
        people=[{"id": 7, "name": "Dana"}],
    )

    response = await client.put(
        f"/api/v1/speakers/recordings/{REC}",
        json={"diarization_label": "SPEAKER_00", "global_speaker_name": "Dana"},
    )

    assert response.status_code == 200
    assert await _identified(session_maker, "Dana", VOICE)


@pytest.mark.anyio
async def test_a_person_founded_by_promoting_a_speaker_is_identified(
    client, session_maker, write_path
) -> None:
    await _seed(
        session_maker,
        speakers=[
            {
                "label": "SPEAKER_00",
                "embedding": VOICE,
                "local_name": "Alex",
                "name": None,
            }
        ],
    )

    response = await client.post(
        f"/api/v1/speakers/recordings/{REC}/speakers/SPEAKER_00/promote"
    )

    assert response.status_code == 200
    assert await _identified(session_maker, "Alex", VOICE)


@pytest.mark.anyio
async def test_promoting_into_a_person_without_a_voiceprint_founds_an_identified_one(
    client, session_maker
) -> None:
    await _seed(
        session_maker,
        speakers=[
            {
                "label": "SPEAKER_00",
                "embedding": VOICE,
                "local_name": "Dana",
                "name": None,
            }
        ],
        people=[{"id": 7, "name": "Dana"}],
    )

    response = await client.post(
        f"/api/v1/speakers/recordings/{REC}/speakers/SPEAKER_00/promote"
    )

    assert response.status_code == 200
    assert await _identified(session_maker, "Dana", VOICE)


@pytest.mark.anyio
@pytest.mark.parametrize("action", ["link_existing", "force_link"])
async def test_a_person_linked_from_the_voiceprint_dialog_is_identified(
    client, session_maker, action
) -> None:
    await _seed(
        session_maker,
        speakers=[{"label": "SPEAKER_00", "embedding": VOICE}],
        people=[{"id": 7, "name": "Dana"}],
    )

    response = await client.post(
        f"/api/v1/speakers/recordings/{REC}/speakers/SPEAKER_00/voiceprint/apply",
        json={"action": action, "global_speaker_id": 7},
    )

    assert response.status_code == 200
    assert await _identified(session_maker, "Dana", VOICE)


@pytest.mark.anyio
async def test_a_person_created_from_the_voiceprint_dialog_keeps_its_version(
    client, session_maker
) -> None:
    await _seed(
        session_maker,
        speakers=[{"label": "SPEAKER_00", "embedding": VOICE}],
    )

    response = await client.post(
        f"/api/v1/speakers/recordings/{REC}/speakers/SPEAKER_00/voiceprint/apply",
        json={"action": "create_new", "new_speaker_name": "Dana"},
    )

    assert response.status_code == 200
    person = await _person(session_maker, "Dana")
    assert (person.embedding, person.embedding_version) == (VOICE, CURRENT)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("endpoint", "seeded"),
    [
        ("speakers/SPEAKER_00/voiceprint/extract", OTHER),
        ("voiceprints/extract-all", None),
    ],
)
async def test_a_re_extracted_voiceprint_linked_to_a_person_is_identified(
    client, session_maker, extraction, endpoint, seeded
) -> None:
    await _seed(
        session_maker,
        speakers=[{"label": "SPEAKER_00", "embedding": seeded, "version": None}],
        people=[{"id": 7, "name": "Robin"}],
    )
    extraction.append(VOICE)

    extracted = await client.post(f"/api/v1/speakers/recordings/{REC}/{endpoint}")
    applied = await client.post(
        f"/api/v1/speakers/recordings/{REC}/speakers/SPEAKER_00/voiceprint/apply",
        json={"action": "link_existing", "global_speaker_id": 7},
    )

    assert (extracted.status_code, applied.status_code) == (200, 200)
    assert await _identified(session_maker, "Robin", VOICE)


@pytest.mark.anyio
async def test_a_recalibrated_legacy_voiceprint_is_identified(
    client, session_maker, extraction
) -> None:
    await _seed(
        session_maker,
        speakers=[{"label": "SPEAKER_00", "global_speaker_id": 7}],
        people=[{"id": 7, "name": "Dana", "embedding": OTHER, "version": None}],
    )
    extraction.append(VOICE)

    response = await client.post(
        "/api/v1/speakers/7/recalibrate",
        json=[{"recording_id": REC, "start": 0.0, "end": 5.0}],
    )

    assert response.status_code == 200
    assert await _identified(session_maker, "Dana", VOICE)


@pytest.mark.anyio
async def test_both_people_of_a_split_are_identified(
    client, session_maker, extraction
) -> None:
    selected, remaining = VOICE, OTHER
    await _seed(
        session_maker,
        speakers=[{"label": "SPEAKER_00", "global_speaker_id": 7, "name": "Dana"}],
        people=[{"id": 7, "name": "Dana", "embedding": [0.6, 0.8], "version": None}],
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
    extraction.extend([selected, remaining])

    response = await client.post(
        "/api/v1/speakers/7/split",
        json={
            "new_speaker_name": "Robin",
            "segments": [{"recording_id": REC, "start": 0.0, "end": 5.0}],
        },
    )

    assert response.status_code == 200
    assert await _identified(session_maker, "Robin", selected)
    assert await _identified(session_maker, "Dana", remaining)
    async with session_maker() as session:
        split_version = (
            await session.execute(
                text(
                    "SELECT embedding_version FROM recording_speakers "
                    "WHERE diarization_label LIKE 'SPLIT_%'"
                )
            )
        ).scalar_one()
    assert split_version == CURRENT


@pytest.mark.anyio
async def test_merging_a_person_into_one_without_a_voiceprint_keeps_it_identified(
    client, session_maker
) -> None:
    await _seed(
        session_maker,
        speakers=[],
        people=[
            {"id": 7, "name": "Dana"},
            {"id": 8, "name": "Dana (2)", "embedding": VOICE, "version": CURRENT},
        ],
    )

    response = await client.post(
        "/api/v1/speakers/merge",
        json={"source_speaker_id": 8, "target_speaker_id": 7},
    )

    assert response.status_code == 200
    assert await _identified(session_maker, "Dana", VOICE)


@pytest.mark.anyio
async def test_a_person_promoted_from_merged_speakers_is_identified(
    client, session_maker, write_path
) -> None:
    await _seed(
        session_maker,
        speakers=[
            {
                "label": "SPEAKER_00",
                "embedding": None,
                "version": None,
                "local_name": "Alex",
                "name": None,
            },
            {"label": "SPEAKER_01", "embedding": VOICE},
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

    assert (merged.status_code, promoted.status_code) == (200, 200)
    assert await _identified(session_maker, "Alex", VOICE)


@pytest.mark.anyio
async def test_linking_a_current_voiceprint_replaces_a_stale_one_rather_than_blending(
    client, session_maker, write_path
) -> None:
    await _seed(
        session_maker,
        speakers=[{"label": "SPEAKER_00", "embedding": VOICE}],
        people=[{"id": 7, "name": "Dana", "embedding": OTHER, "version": None}],
    )

    response = await client.put(
        f"/api/v1/speakers/recordings/{REC}",
        json={"diarization_label": "SPEAKER_00", "global_speaker_name": "Dana"},
    )

    assert response.status_code == 200
    person = await _person(session_maker, "Dana")
    assert (person.embedding, person.embedding_version) == (VOICE, CURRENT)


@pytest.mark.anyio
async def test_linking_a_stale_voiceprint_leaves_a_current_person_alone(
    client, session_maker, write_path
) -> None:
    # Close enough to the person to pass the drift guard, so only the version
    # keeps it out.
    stale = [0.8, 0.6]
    await _seed(
        session_maker,
        speakers=[{"label": "SPEAKER_00", "embedding": stale, "version": None}],
        people=[{"id": 7, "name": "Dana", "embedding": VOICE, "version": CURRENT}],
    )

    response = await client.put(
        f"/api/v1/speakers/recordings/{REC}",
        json={"diarization_label": "SPEAKER_00", "global_speaker_name": "Dana"},
    )

    assert response.status_code == 200
    person = await _person(session_maker, "Dana")
    assert (person.embedding, person.embedding_version) == (VOICE, CURRENT)


@pytest.mark.anyio
async def test_a_legacy_voiceprint_founding_a_person_stays_legacy(
    client, session_maker, write_path
) -> None:
    await _seed(
        session_maker,
        speakers=[{"label": "SPEAKER_00", "embedding": VOICE, "version": None}],
        people=[{"id": 7, "name": "Dana"}],
    )

    response = await client.put(
        f"/api/v1/speakers/recordings/{REC}",
        json={"diarization_label": "SPEAKER_00", "global_speaker_name": "Dana"},
    )

    # Still stale, so the voiceprint rebuild picks the person up.
    assert response.status_code == 200
    person = await _person(session_maker, "Dana")
    assert (person.embedding, person.embedding_version) == (VOICE, None)
    assert not await _identified(session_maker, "Dana", VOICE)


@pytest.mark.anyio
# The canonical rename overrides the lock on purpose, as a user correction.
@pytest.mark.parametrize("write_path", ["legacy"], indirect=True)
async def test_a_legacy_rename_leaves_a_locked_voiceprint_alone(
    client, session_maker, write_path
) -> None:
    near = [0.8, 0.6]
    await _seed(
        session_maker,
        speakers=[{"label": "SPEAKER_00", "embedding": near}],
        people=[
            {
                "id": 7,
                "name": "Dana",
                "embedding": VOICE,
                "version": CURRENT,
                "locked": True,
            }
        ],
    )

    response = await client.put(
        f"/api/v1/speakers/recordings/{REC}",
        json={"diarization_label": "SPEAKER_00", "global_speaker_name": "Dana"},
    )

    assert response.status_code == 200
    person = await _person(session_maker, "Dana")
    assert (person.embedding, person.embedding_version) == (VOICE, CURRENT)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("target", "source"),
    [
        # A stale source passes the drift guard, so only its version keeps it out.
        ({"embedding": VOICE, "version": CURRENT}, {"embedding": [0.8, 0.6]}),
        # A current source is orthogonal, so the drift guard would refuse a blend.
        ({"embedding": OTHER}, {"embedding": VOICE, "version": CURRENT}),
    ],
    ids=["stale-into-current", "current-into-stale"],
)
async def test_merging_two_people_keeps_the_current_voiceprint(
    client, session_maker, target, source
) -> None:
    await _seed(
        session_maker,
        speakers=[],
        people=[
            {"id": 7, "name": "Dana", **target},
            {"id": 8, "name": "Dana (2)", **source},
        ],
    )

    response = await client.post(
        "/api/v1/speakers/merge",
        json={"source_speaker_id": 8, "target_speaker_id": 7},
    )

    assert response.status_code == 200
    person = await _person(session_maker, "Dana")
    assert (person.embedding, person.embedding_version) == (VOICE, CURRENT)
    assert await _identified(session_maker, "Dana", VOICE)


def test_segment_learning_stamps_the_current_version(
    monkeypatch, stub_celery_dispatch
) -> None:
    from backend.processing import embedding_core
    from backend.worker.tasks import embeddings as embedding_tasks

    speaker = SimpleNamespace(
        embedding=None,
        embedding_version=None,
        global_speaker_id=7,
        diarization_label="SPEAKER_00",
    )
    person = SimpleNamespace(name="Dana", embedding=list(OTHER), embedding_version=None)
    rows = {
        (Recording, 1): SimpleNamespace(audio_path="/tmp/meeting.wav"),
        (RecordingSpeaker, 3): speaker,
        (GlobalSpeaker, 7): person,
    }

    class _Session:
        committed = False

        def get(self, model, identifier):
            return rows.get((model, identifier))

        def add(self, row) -> None:
            assert row in (speaker, person)

        def commit(self) -> None:
            self.committed = True

        def rollback(self) -> None:
            raise AssertionError("the task failed and rolled back")

    session = _Session()
    monkeypatch.setattr(
        embedding_tasks,
        "select_recording_audio_for_embedding",
        lambda recording: recording.audio_path,
    )
    monkeypatch.setattr(
        embedding_core,
        "extract_embedding_for_segments",
        lambda *args, **kwargs: list(VOICE),
    )
    task = embedding_tasks.update_speaker_embedding_task
    monkeypatch.setattr(task, "_session", session)

    task.run(1, 0.0, 5.0, 3)

    assert session.committed
    assert (speaker.embedding, speaker.embedding_version) == (VOICE, CURRENT)
    assert (person.embedding, person.embedding_version) == (VOICE, CURRENT)
    assert find_matching_global_speaker(VOICE, [person])[0] is person
    assert stub_celery_dispatch == []


def test_a_same_version_blend_folds_through_the_drift_guard() -> None:
    # Imported here so the route tests above still run, and fail one by one,
    # against code that predates the helper.
    from backend.processing.embedding import blend_voiceprint

    near = [0.8, 0.6]
    row = SimpleNamespace(embedding=list(VOICE), embedding_version=None)

    blend_voiceprint(row, near, None, alpha=0.3)

    assert row.embedding == pytest.approx(merge_embeddings(VOICE, near, alpha=0.3))
    assert row.embedding_version is None
    blend_voiceprint(row, OTHER, None, alpha=0.3)
    assert row.embedding == pytest.approx(merge_embeddings(VOICE, near, alpha=0.3))


@pytest.mark.parametrize("overwrite", [True, False])
def test_restoring_a_backup_voiceprint_restores_its_version(overwrite) -> None:
    existing = SimpleNamespace(
        name="Dana",
        embedding=None,
        embedding_version=None,
        **{field: None for field in ("title", "company", "email", "phone_number")},
        notes=None,
        color=None,
    )

    class _Session:
        def exec(self, statement):
            return SimpleNamespace(first=lambda: existing)

        def add(self, row) -> None:
            assert row is existing

    ctx = SimpleNamespace(
        session=_Session(),
        item_data={
            "name": "Dana",
            "user_id": 1,
            "embedding": VOICE,
            "embedding_version": CURRENT,
        },
        state=SimpleNamespace(
            overwrite_existing=overwrite, id_map={"global_speakers": {}}
        ),
        old_id=None,
    )

    _resolve_global_speakers(ctx)

    assert find_matching_global_speaker(VOICE, [existing])[0] is existing
