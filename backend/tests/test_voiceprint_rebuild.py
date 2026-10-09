"""Tests for the stale-voiceprint rebuild task.

The rebuild exists to repair voiceprints left unmatchable by an extraction
method bump. Its failure mode is silence: every path that cannot do the work
used to ``continue`` without a counter or a log line, so a run that rebuilt
nothing was indistinguishable from a run that had nothing to do, and the
"needs rebuilding" prompt could never clear. These tests pin down that every
stale voiceprint leaves the run in a resolved state -- rebuilt, deliberately
cleared, or explicitly held back for retry -- and that the counts say which.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session

import backend.processing.embedding_core as embedding_core
import backend.utils.embedding_audio as embedding_audio
import backend.worker.tasks as tasks_module
from backend.processing.embedding_core import (
    EMBEDDING_METHOD_VERSION,
    LEGACY_EMBEDDING_METHOD_VERSION,
)
from backend.tests.sqlite_schemas import (
    GLOBAL_SPEAKERS_SCHEMA,
    P_TAGS_SCHEMA,
    PEOPLE_TAGS_SCHEMA,
    RECORDING_SPEAKERS_SCHEMA,
    RECORDINGS_SCHEMA,
    TRANSCRIPT_UTTERANCES_SCHEMA,
    TRANSCRIPTS_SCHEMA,
    USERS_SCHEMA,
)

STALE_VECTOR = [1.0, 0.0, 0.0]
REBUILT_VECTOR = [0.6, 0.8, 0.0]


def _utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _make_engine(tmp_path: Path, name: str) -> Any:
    engine = create_engine(f"sqlite:///{tmp_path / name}.sqlite", future=True)
    with engine.begin() as connection:
        for schema in (
            USERS_SCHEMA,
            RECORDINGS_SCHEMA,
            TRANSCRIPTS_SCHEMA,
            GLOBAL_SPEAKERS_SCHEMA,
            RECORDING_SPEAKERS_SCHEMA,
            TRANSCRIPT_UTTERANCES_SCHEMA,
            PEOPLE_TAGS_SCHEMA,
            P_TAGS_SCHEMA,
        ):
            connection.execute(text(schema))
    return engine


def _add_user(connection, user_id: int) -> None:
    connection.execute(
        text(
            """
            INSERT INTO users (
                id, created_at, updated_at, username, hashed_password,
                is_active, is_superuser, force_password_change, role,
                token_version, settings, has_seen_demo_recording, invitation_id
            ) VALUES (
                :id, :now, :now, :username, 'hash', 1, 0, 0, 'user',
                0, '{}', 0, NULL
            )
            """
        ),
        {"id": user_id, "now": _utc_now_naive(), "username": f"user{user_id}"},
    )


def _add_recording(connection, recording_id: int, user_id: int) -> None:
    connection.execute(
        text(
            """
            INSERT INTO recordings (
                id, created_at, updated_at, name, public_id, meeting_uid,
                audio_path, proxy_path, status, upload_progress,
                processing_progress, is_archived, is_deleted, user_id
            ) VALUES (
                :id, :now, :now, :name, :public_id, :uid,
                :audio_path, NULL, 'PROCESSED', 100, 100, 0, 0, :user_id
            )
            """
        ),
        {
            "id": recording_id,
            "now": _utc_now_naive(),
            "name": f"Meeting {recording_id}",
            "public_id": f"public-recording-{recording_id}",
            "uid": f"meeting-uid-{recording_id}",
            "audio_path": f"/audio/{recording_id}.wav",
            "user_id": user_id,
        },
    )


@dataclass
class SpeakerRow:
    """A recording speaker to seed, stale by default."""

    id: int
    recording_id: int
    label: str = "SPEAKER_00"
    embedding: Any = field(default_factory=lambda: list(STALE_VECTOR))
    embedding_version: int | None = LEGACY_EMBEDDING_METHOD_VERSION
    global_speaker_id: int | None = None


def _add_speaker(connection, speaker: SpeakerRow) -> None:
    connection.execute(
        text(
            """
            INSERT INTO recording_speakers (
                id, created_at, updated_at, public_id, recording_id,
                global_speaker_id, diarization_label, embedding,
                embedding_version, merged_into_id, speaker_status,
                speaker_kind, identity_locked
            ) VALUES (
                :id, :now, :now, :public_id, :recording_id,
                :global_speaker_id, :label, :embedding,
                :embedding_version, NULL, 'active', 'automated', 0
            )
            """
        ),
        {
            "id": speaker.id,
            "now": _utc_now_naive(),
            "public_id": f"recording-speaker-{speaker.id}",
            "recording_id": speaker.recording_id,
            "global_speaker_id": speaker.global_speaker_id,
            "label": speaker.label,
            "embedding": json.dumps(speaker.embedding),
            "embedding_version": speaker.embedding_version,
        },
    )


def _add_person(
    connection,
    person_id: int,
    user_id: int,
    *,
    embedding: Any = None,
    embedding_version: int | None = None,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO global_speakers (
                id, created_at, updated_at, user_id, name, embedding,
                embedding_version, is_voiceprint_locked
            ) VALUES (
                :id, :now, :now, :user_id, :name, :embedding,
                :embedding_version, 0
            )
            """
        ),
        {
            "id": person_id,
            "now": _utc_now_naive(),
            "user_id": user_id,
            "name": f"Person {person_id}",
            "embedding": json.dumps(embedding) if embedding is not None else None,
            "embedding_version": embedding_version,
        },
    )


def _add_utterance(
    connection,
    utterance_id: int,
    recording_id: int,
    speaker_id: int | None,
    span_ms: tuple[int, int] = (0, 2000),
) -> None:
    start_ms, end_ms = span_ms
    connection.execute(
        text(
            """
            INSERT INTO transcript_utterances (
                id, created_at, updated_at, public_id, recording_id, sort_key,
                start_ms, end_ms, text, speaker_label, recording_speaker_id,
                state, source_kind, revision, overlap_rank,
                manual_text_locked, manual_speaker_locked,
                speaker_assignment_source, speaker_assignment_authority
            ) VALUES (
                :id, :now, :now, :public_id, :recording_id, :sort_key,
                :start_ms, :end_ms, 'text', NULL, :speaker_id,
                'final', 'asr', 1, 0, 0, 0, 'diarization', 'automated'
            )
            """
        ),
        {
            "id": utterance_id,
            "now": _utc_now_naive(),
            "public_id": f"utterance-{utterance_id}",
            "recording_id": recording_id,
            "sort_key": f"{start_ms:012d}",
            "start_ms": start_ms,
            "end_ms": end_ms,
            "speaker_id": speaker_id,
        },
    )


def _add_transcript(connection, recording_id: int, segments: list[dict]) -> None:
    connection.execute(
        text(
            """
            INSERT INTO transcripts (
                id, created_at, updated_at, recording_id, text, segments,
                notes_status, transcript_status
            ) VALUES (
                :id, :now, :now, :recording_id, 'text', :segments,
                'completed', 'completed'
            )
            """
        ),
        {
            "id": recording_id,
            "now": _utc_now_naive(),
            "recording_id": recording_id,
            "segments": json.dumps(segments),
        },
    )


def _run_task(engine: Any, **kwargs: Any) -> dict:
    """Invoke the task against a prepared engine, bypassing Celery dispatch."""
    task = tasks_module.rebuild_voiceprints_task
    task._session = Session(engine)
    try:
        return task.run(**kwargs)
    finally:
        task._session.close()
        task._session = None
        engine.dispose()


def _speaker_rows(engine: Any) -> dict[int, tuple[Any, Any]]:
    with engine.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT id, embedding, embedding_version FROM recording_speakers"
                " ORDER BY id"
            )
        ).all()
    return {row[0]: (row[1], row[2]) for row in rows}


@pytest.fixture(autouse=True)
def _audio_always_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default to audio being available so tests opt in to the missing case."""
    monkeypatch.setattr(
        embedding_audio,
        "select_recording_audio_for_embedding",
        lambda recording: getattr(recording, "audio_path", None),
    )


@pytest.fixture(autouse=True)
def _extraction_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        embedding_core,
        "extract_embedding_for_segments",
        lambda audio_path, segments, device_str="auto", hf_token=None: list(
            REBUILT_VECTOR
        ),
    )


def test_speaker_with_no_attributable_speech_is_cleared_and_counted(
    tmp_path: Path,
) -> None:
    """A stale speaker owning no speech is unrebuildable, so it must not be skipped.

    Re-diarisation can fold a speaker's segments into another speaker while
    leaving the row and its embedding behind. There is no audio to re-extract
    from, on this run or any later one, so silently continuing made the task a
    permanent no-op that still reported success.
    """
    engine = _make_engine(tmp_path, "no-speech")
    with engine.begin() as connection:
        _add_user(connection, 1)
        _add_recording(connection, 10, 1)
        _add_speaker(connection, SpeakerRow(100, 10, "SPEAKER_00"))
        # The transcript names a different speaker entirely.
        _add_transcript(
            connection, 10, [{"start": 0.0, "end": 2.0, "speaker": "SPEAKER_09"}]
        )

    summary = _run_task(engine, user_id=1)

    assert summary["stale_speakers_found"] == 1
    assert summary["speakers_rebuilt"] == 0
    assert summary["speakers_cleared_unrebuildable"] == 1
    assert summary["speakers_failed_retryable"] == 0

    embedding, version = _speaker_rows(engine)[100]
    assert embedding is None
    assert version is None


def test_transcript_segments_cover_a_speaker_the_utterance_table_missed(
    tmp_path: Path,
) -> None:
    """The segment fallback must apply per speaker, not per recording.

    A recording processed across a pipeline change can have utterance rows for
    some speakers and only transcript segments for the rest. An all-or-nothing
    fallback -- used only when no speaker had utterances -- left exactly those
    partially covered speakers with nothing to rebuild from.
    """
    engine = _make_engine(tmp_path, "partial-coverage")
    with engine.begin() as connection:
        _add_user(connection, 1)
        _add_recording(connection, 10, 1)
        _add_speaker(connection, SpeakerRow(100, 10, "SPEAKER_00"))
        _add_speaker(connection, SpeakerRow(101, 10, "SPEAKER_01"))
        # Only SPEAKER_00 has a canonical utterance, so the recording is not
        # empty and the old code never consulted the transcript at all.
        _add_utterance(connection, 1, 10, 100, (0, 2000))
        _add_transcript(
            connection,
            10,
            [
                {"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00"},
                {"start": 2.0, "end": 5.0, "speaker": "SPEAKER_01"},
            ],
        )

    summary = _run_task(engine, user_id=1)

    assert summary["speakers_rebuilt"] == 2
    assert summary["speakers_cleared_unrebuildable"] == 0

    rows = _speaker_rows(engine)
    for speaker_id in (100, 101):
        embedding, version = rows[speaker_id]
        assert json.loads(embedding) == REBUILT_VECTOR
        assert version == EMBEDDING_METHOD_VERSION


def test_utterance_ranges_win_over_transcript_segments(tmp_path: Path) -> None:
    """The canonical table stays authoritative where it has coverage."""
    engine = _make_engine(tmp_path, "canonical-wins")
    captured: dict[str, Any] = {}

    with engine.begin() as connection:
        _add_user(connection, 1)
        _add_recording(connection, 10, 1)
        _add_speaker(connection, SpeakerRow(100, 10, "SPEAKER_00"))
        _add_utterance(connection, 1, 10, 100, (1000, 3000))
        _add_transcript(
            connection, 10, [{"start": 90.0, "end": 95.0, "speaker": "SPEAKER_00"}]
        )

    def _capture(audio_path, segments, device_str="auto", hf_token=None):
        captured["segments"] = segments
        return list(REBUILT_VECTOR)

    embedding_core.extract_embedding_for_segments = _capture
    summary = _run_task(engine, user_id=1)

    assert summary["speakers_rebuilt"] == 1
    assert captured["segments"] == [(1.0, 3.0)]


def test_missing_audio_clears_stale_voiceprints(tmp_path: Path) -> None:
    """Audio removal is permanent, so the stale voiceprints it strands are dropped."""
    engine = _make_engine(tmp_path, "no-audio")
    with engine.begin() as connection:
        _add_user(connection, 1)
        _add_recording(connection, 10, 1)
        _add_speaker(connection, SpeakerRow(100, 10, "SPEAKER_00"))
        _add_utterance(connection, 1, 10, 100, (0, 2000))

    embedding_audio.select_recording_audio_for_embedding = lambda recording: None
    summary = _run_task(engine, user_id=1)

    assert summary["recordings_skipped_no_audio"] == 1
    assert summary["speakers_cleared_unrebuildable"] == 1

    embedding, version = _speaker_rows(engine)[100]
    assert embedding is None
    assert version is None


def test_transient_extraction_failure_is_held_back_for_retry(tmp_path: Path) -> None:
    """An exception may be transient, so the voiceprint must survive to be retried.

    This is the one case that must not be cleared: discarding on a decode
    hiccup would destroy a voiceprint that a later run could have rebuilt.
    """
    engine = _make_engine(tmp_path, "transient-failure")
    with engine.begin() as connection:
        _add_user(connection, 1)
        _add_recording(connection, 10, 1)
        _add_speaker(connection, SpeakerRow(100, 10, "SPEAKER_00"))
        _add_utterance(connection, 1, 10, 100, (0, 2000))

    def _boom(audio_path, segments, device_str="auto", hf_token=None):
        raise RuntimeError("device busy")

    embedding_core.extract_embedding_for_segments = _boom
    summary = _run_task(engine, user_id=1)

    assert summary["speakers_failed_retryable"] == 1
    assert summary["speakers_cleared_unrebuildable"] == 0
    assert summary["speakers_rebuilt"] == 0

    embedding, version = _speaker_rows(engine)[100]
    assert json.loads(embedding) == STALE_VECTOR
    assert version == LEGACY_EMBEDDING_METHOD_VERSION


def test_extraction_returning_nothing_clears_the_voiceprint(tmp_path: Path) -> None:
    """No exception but no vector is deterministic for this input, so retrying cannot help."""
    engine = _make_engine(tmp_path, "empty-extraction")
    with engine.begin() as connection:
        _add_user(connection, 1)
        _add_recording(connection, 10, 1)
        _add_speaker(connection, SpeakerRow(100, 10, "SPEAKER_00"))
        _add_utterance(connection, 1, 10, 100, (0, 2000))

    embedding_core.extract_embedding_for_segments = (
        lambda audio_path, segments, device_str="auto", hf_token=None: None
    )
    summary = _run_task(engine, user_id=1)

    assert summary["speakers_cleared_unrebuildable"] == 1
    assert summary["speakers_failed_retryable"] == 0
    assert _speaker_rows(engine)[100] == (None, None)


def test_stale_count_and_work_are_scoped_to_the_requesting_user(
    tmp_path: Path,
) -> None:
    """Another user's stale rows must not be counted, touched, or spend the limit.

    Scoping used to happen while iterating, after the run limit had already
    been sliced off the global set, so a busy neighbour could consume the whole
    budget and leave the requesting user's library untouched while the summary
    still reported nothing remaining.
    """
    engine = _make_engine(tmp_path, "user-scope")
    with engine.begin() as connection:
        _add_user(connection, 1)
        _add_user(connection, 2)
        _add_recording(connection, 10, 1)
        _add_recording(connection, 20, 2)
        _add_speaker(connection, SpeakerRow(100, 10, "SPEAKER_00"))
        _add_speaker(connection, SpeakerRow(200, 20, "SPEAKER_00"))
        _add_utterance(connection, 1, 10, 100, (0, 2000))
        _add_utterance(connection, 2, 20, 200, (0, 2000))

    summary = _run_task(engine, user_id=1)

    assert summary["stale_speakers_found"] == 1
    assert summary["recordings_processed"] == 1
    assert summary["speakers_rebuilt"] == 1

    rows = _speaker_rows(engine)
    assert json.loads(rows[100][0]) == REBUILT_VECTOR
    # The neighbour's stale voiceprint is left exactly as it was.
    assert json.loads(rows[200][0]) == STALE_VECTOR
    assert rows[200][1] == LEGACY_EMBEDDING_METHOD_VERSION


def test_run_limit_is_spent_on_the_requesting_users_recordings(
    tmp_path: Path,
) -> None:
    engine = _make_engine(tmp_path, "user-scoped-limit")
    with engine.begin() as connection:
        _add_user(connection, 1)
        _add_user(connection, 2)
        # The neighbour owns the lower recording ids, so an unscoped query
        # would hand them the whole single-recording budget.
        for recording_id, speaker_id in ((1, 200), (2, 201)):
            _add_recording(connection, recording_id, 2)
            _add_speaker(connection, SpeakerRow(speaker_id, recording_id, "SPEAKER_00"))
            _add_utterance(connection, speaker_id, recording_id, speaker_id, (0, 2000))
        _add_recording(connection, 30, 1)
        _add_speaker(connection, SpeakerRow(100, 30, "SPEAKER_00"))
        _add_utterance(connection, 100, 30, 100, (0, 2000))

    summary = _run_task(engine, user_id=1, limit=1)

    assert summary["recordings_processed"] == 1
    assert summary["speakers_rebuilt"] == 1
    assert summary["recordings_remaining"] == 0
    assert json.loads(_speaker_rows(engine)[100][0]) == REBUILT_VECTOR


def test_json_null_embedding_is_not_counted_as_stale(tmp_path: Path) -> None:
    """A JSON ``null`` passes SQL ``IS NOT NULL`` while carrying no vector.

    The column is JSON, so these rows survive the SQL predicate and would
    inflate the reported stale count above what the user is shown.
    """
    engine = _make_engine(tmp_path, "json-null")
    with engine.begin() as connection:
        _add_user(connection, 1)
        _add_recording(connection, 10, 1)
        _add_speaker(connection, SpeakerRow(100, 10, "UNKNOWN", embedding=None))

    summary = _run_task(engine, user_id=1)

    assert summary["stale_speakers_found"] == 0
    assert summary["speakers_cleared_unrebuildable"] == 0


def test_person_is_recomputed_from_rebuilt_speakers(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path, "person-rebuild")
    with engine.begin() as connection:
        _add_user(connection, 1)
        _add_person(connection, 5, 1, embedding=STALE_VECTOR, embedding_version=1)
        _add_recording(connection, 10, 1)
        _add_speaker(connection, SpeakerRow(100, 10, "SPEAKER_00", global_speaker_id=5))
        _add_utterance(connection, 1, 10, 100, (0, 2000))

    summary = _run_task(engine, user_id=1)

    assert summary["people_rebuilt"] == 1
    assert summary["people_cleared_unrebuildable"] == 0

    with engine.begin() as connection:
        embedding, version = connection.execute(
            text(
                "SELECT embedding, embedding_version FROM global_speakers WHERE id = 5"
            )
        ).one()
    assert version == EMBEDDING_METHOD_VERSION
    assert json.loads(embedding) == pytest.approx([0.6, 0.8, 0.0])


def test_person_with_no_current_version_speakers_is_cleared(tmp_path: Path) -> None:
    """A person whose every speaker was cleared can never be recomputed.

    The person record is kept; only the unmatchable vector goes, so the
    library converges instead of holding a voiceprint that no longer scores
    against anything.
    """
    engine = _make_engine(tmp_path, "person-cleared")
    with engine.begin() as connection:
        _add_user(connection, 1)
        _add_person(connection, 5, 1, embedding=STALE_VECTOR, embedding_version=1)
        _add_recording(connection, 10, 1)
        _add_speaker(connection, SpeakerRow(100, 10, "SPEAKER_00", global_speaker_id=5))
        # No utterance and no transcript, so the speaker is cleared and the
        # person is left with nothing current-version to average.
        _add_transcript(connection, 10, [])

    summary = _run_task(engine, user_id=1)

    assert summary["speakers_cleared_unrebuildable"] == 1
    assert summary["people_rebuilt"] == 0
    assert summary["people_cleared_unrebuildable"] == 1

    with engine.begin() as connection:
        embedding, version, name = connection.execute(
            text(
                "SELECT embedding, embedding_version, name FROM global_speakers"
                " WHERE id = 5"
            )
        ).one()
    assert embedding is None
    assert version is None
    # The person survives; only their unusable voiceprint was dropped.
    assert name == "Person 5"


def test_a_second_run_has_nothing_left_to_do(tmp_path: Path) -> None:
    """The whole point of clearing: the run converges instead of repeating.

    Two identical runs reporting identical unfinished work was the original
    symptom, and it is what the prompt in the UI depends on to clear.
    """
    engine = _make_engine(tmp_path, "convergence")
    with engine.begin() as connection:
        _add_user(connection, 1)
        _add_recording(connection, 10, 1)
        _add_speaker(connection, SpeakerRow(100, 10, "SPEAKER_00"))
        _add_speaker(connection, SpeakerRow(101, 10, "SPEAKER_01"))
        _add_utterance(connection, 1, 10, 100, (0, 2000))
        _add_transcript(connection, 10, [])

    task = tasks_module.rebuild_voiceprints_task
    task._session = Session(engine)
    try:
        first = task.run(user_id=1)
        second = task.run(user_id=1)
    finally:
        task._session.close()
        task._session = None

    assert first["stale_speakers_found"] == 2
    assert first["speakers_rebuilt"] == 1
    assert first["speakers_cleared_unrebuildable"] == 1

    assert second["stale_speakers_found"] == 0
    assert second["speakers_rebuilt"] == 0
    assert second["speakers_cleared_unrebuildable"] == 0

    engine.dispose()
