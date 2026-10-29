"""Final alignment failures survive canonical storage and projection refreshes."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlmodel import Session, select

from backend.core.db import driver_urls
from backend.models.pipeline import TranscriptUtterance
from backend.models.recording import Recording
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.tests.postgres_setup import fresh_schema
from backend.utils.canonical_pipeline.core import (
    finalize_utterances_from_segments,
    refresh_transcript_projection_from_canonical,
    serialize_canonical_utterances,
)
from backend.worker.tasks.final_segments import combine_and_consolidate_segments

pytestmark = pytest.mark.usefixtures("stub_celery_dispatch")
SCHEMA = "forced_alignment_persistence_test"
ASR_TEXT = "Hello, Mike — it's café time!"


@pytest.fixture
def alignment_session(postgres_test_url: str) -> Iterator[tuple[Session, int]]:
    """A worker-driver session over an isolated production-schema database."""
    importlib.import_module("backend.models.registry")
    engine = create_engine(
        driver_urls(postgres_test_url)[1],
        connect_args={"options": f"-csearch_path={SCHEMA},public"},
    )
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        fresh_schema(engine, SCHEMA)
        with Session(engine) as session:
            owner = User(username="aligner-owner", hashed_password="x")
            session.add(owner)
            session.commit()
            recording = Recording(
                name="Alignment", audio_path="/data/alignment.wav", user_id=owner.id
            )
            session.add(recording)
            session.commit()
            recording_id = int(recording.id)
            session.add(Transcript(recording_id=recording_id, text="", segments=[]))
            session.commit()
            yield session, recording_id
    finally:
        engine.dispose()


def _segment(error: str | None = None) -> dict[str, Any]:
    segment: dict[str, Any] = {
        "start": 1.25,
        "end": 4.75,
        "text": ASR_TEXT,
        "speaker": "UNKNOWN",
    }
    if error:
        segment["alignment_error"] = error
    return segment


def _partial_segment() -> dict[str, Any]:
    """A valid prediction followed by an unplaced original word."""
    return {
        **_segment(),
        "words": [
            {
                "word": "Hello,",
                "start": 1.25,
                "end": 1.75,
                "source_id": "partial",
                "source_start": 0,
                "source_end": 6,
            },
            {
                "word": ASR_TEXT[6:],
                "start": None,
                "end": None,
                "source_id": "partial",
                "source_start": 6,
                "source_end": len(ASR_TEXT),
            },
        ],
    }


def test_partial_alignment_survives_canonical_refresh_and_clears_old_failure(
    alignment_session: tuple[Session, int],
) -> None:
    session, recording_id = alignment_session
    previous = finalize_utterances_from_segments(
        session,
        recording_id=recording_id,
        segments=[_segment("Previous failure")],
        idempotency_key="failed",
    )
    public_id = previous[0].public_id
    session.commit()
    current = finalize_utterances_from_segments(
        session,
        recording_id=recording_id,
        segments=[_partial_segment()],
        idempotency_key="partial",
    )
    assert current[0].public_id == public_id
    session.commit()
    session.expunge_all()
    refreshed = refresh_transcript_projection_from_canonical(session, recording_id)
    payload = serialize_canonical_utterances(
        session, recording_id, include_confidence_payload=True
    )[0]
    assert not refreshed[0].get("alignment_error")
    assert not payload.get("alignment_error")
    assert payload["text"] == ASR_TEXT
    evidence = payload["confidence_payload"]
    assert "alignment_error" not in evidence
    assert (evidence["utterance_start_ms"], evidence["utterance_end_ms"]) == (
        1250,
        4750,
    )
    words = evidence["asr_segments"][0]["words"]
    assert [(word["start_ms"], word["end_ms"]) for word in words] == [
        (1250, 1750),
        (None, None),
    ]
    assert "".join(word["word"] for word in words) == ASR_TEXT
    assert evidence["asr_word_timestamps_available"] is True


def test_combined_partial_runs_keep_source_order_and_nulls_after_canonical_refresh(
    alignment_session: tuple[Session, int],
) -> None:
    session, recording_id = alignment_session
    segments = combine_and_consolidate_segments(
        {"segments": [_partial_segment()]},
        None,
        enable_diarization=False,
        recording_id=recording_id,
    )
    assert len(segments) == 1
    finalize_utterances_from_segments(
        session, recording_id=recording_id, segments=segments
    )
    session.commit()
    session.expunge_all()
    refreshed = refresh_transcript_projection_from_canonical(session, recording_id)
    reads = serialize_canonical_utterances(
        session, recording_id, include_confidence_payload=True
    )
    assert [segment["text"] for segment in refreshed] == [ASR_TEXT]
    assert (refreshed[0]["start"], refreshed[0]["end"]) == (1.25, 1.75)
    words = [
        word
        for row in reads
        for source in row["confidence_payload"]["asr_segments"]
        for word in source["words"]
    ]
    assert "".join(word["word"] for word in words) == ASR_TEXT
    assert [(word["start_ms"], word["end_ms"]) for word in words] == [
        (1250, 1750),
        (None, None),
    ]
    assert all(not row.get("alignment_error") for row in reads)


def test_final_failure_survives_storage_and_projection_refresh(
    alignment_session: tuple[Session, int],
) -> None:
    session, recording_id = alignment_session
    segments = combine_and_consolidate_segments(
        {"text": ASR_TEXT, "segments": [_segment("Unsupported alignment language")]},
        None,
        enable_diarization=False,
        recording_id=recording_id,
    )
    utterances = finalize_utterances_from_segments(
        session, recording_id=recording_id, segments=segments
    )
    utterance_id = int(utterances[0].id)
    session.commit()
    session.expunge_all()

    stored = session.get(TranscriptUtterance, utterance_id)
    assert stored is not None
    assert (
        stored.confidence_payload["alignment_error"] == "Unsupported alignment language"
    )
    assert stored.confidence_payload["asr_word_timestamps_available"] is False
    assert stored.confidence_payload["asr_segments"] == [
        {
            "start_ms": 1250,
            "end_ms": 4750,
            "text": ASR_TEXT,
            "alignment_error": "Unsupported alignment language",
        }
    ]
    refreshed = refresh_transcript_projection_from_canonical(session, recording_id)
    session.commit()
    session.expunge_all()

    default_read = serialize_canonical_utterances(session, recording_id)
    evidence_read = serialize_canonical_utterances(
        session, recording_id, include_confidence_payload=True
    )
    for payload in (refreshed[0], default_read[0], evidence_read[0]):
        assert payload["alignment_error"] == "Unsupported alignment language"
        assert payload["text"] == ASR_TEXT
        assert (payload["start_ms"], payload["end_ms"]) == (1250, 4750)
        assert "words" not in payload
    assert (
        evidence_read[0]["confidence_payload"]["alignment_error"]
        == "Unsupported alignment language"
    )


@pytest.mark.parametrize(
    "previous_error,current_error",
    [
        (None, "Alignment failed"),
        ("Alignment failed", None),
        ("Old failure", "New failure"),
    ],
)
def test_matching_final_rerun_replaces_failure_and_keeps_other_evidence(
    alignment_session: tuple[Session, int],
    previous_error: str | None,
    current_error: str | None,
) -> None:
    session, recording_id = alignment_session
    initial_segment = {
        **_segment(previous_error),
        "confidence_payload": {"existing_evidence": "kept"},
    }
    first = finalize_utterances_from_segments(
        session,
        recording_id=recording_id,
        segments=[initial_segment],
        idempotency_key="initial",
    )
    public_id = first[0].public_id
    session.commit()
    session.expunge_all()

    rerun = finalize_utterances_from_segments(
        session,
        recording_id=recording_id,
        segments=[_segment(current_error)],
        idempotency_key="rerun",
    )
    assert rerun[0].public_id == public_id
    session.commit()
    session.expunge_all()

    refreshed = refresh_transcript_projection_from_canonical(session, recording_id)
    payload = serialize_canonical_utterances(
        session, recording_id, include_confidence_payload=True
    )[0]
    assert payload["confidence_payload"]["existing_evidence"] == "kept"
    if current_error:
        assert refreshed[0]["alignment_error"] == current_error
        assert payload["confidence_payload"]["alignment_error"] == current_error
    else:
        assert "alignment_error" not in refreshed[0]
        assert "alignment_error" not in payload["confidence_payload"]
    assert payload["text"] == ASR_TEXT


@pytest.mark.parametrize(
    "previous_payload", [None, {}, {"native_evidence": "original"}]
)
def test_native_final_rerun_keeps_existing_confidence_evidence(
    alignment_session: tuple[Session, int],
    previous_payload: dict[str, Any] | None,
) -> None:
    session, recording_id = alignment_session
    initial = finalize_utterances_from_segments(
        session,
        recording_id=recording_id,
        segments=[{**_segment(), "confidence_payload": previous_payload}],
        idempotency_key="initial",
    )
    public_id = initial[0].public_id
    session.commit()
    rerun = finalize_utterances_from_segments(
        session,
        recording_id=recording_id,
        segments=[
            {
                **_segment(),
                "confidence_payload": {"native_evidence": "replacement", "extra": True},
            }
        ],
        idempotency_key="rerun",
    )
    assert rerun[0].public_id == public_id
    assert rerun[0].confidence_payload == previous_payload


@pytest.mark.parametrize("canonical", [True, False], ids=["canonical", "projection"])
@pytest.mark.parametrize("partial", [False, True], ids=["failed", "partial"])
def test_alignment_error_survives_utterance_http_response(
    alignment_session: tuple[Session, int],
    monkeypatch: pytest.MonkeyPatch,
    canonical: bool,
    partial: bool,
) -> None:
    session, recording_id = alignment_session
    finalize_utterances_from_segments(
        session,
        recording_id=recording_id,
        segments=[_partial_segment() if partial else _segment("Unplaced word")],
    )
    refresh_transcript_projection_from_canonical(session, recording_id)
    session.commit()
    utterances = _http_utterances(
        session, recording_id, monkeypatch, canonical=canonical
    )
    assert utterances[0]["text"] == ASR_TEXT
    assert utterances[0]["alignment_error"] == (None if partial else "Unplaced word")


def _http_utterances(
    session: Session,
    recording_id: int,
    monkeypatch: pytest.MonkeyPatch,
    *,
    canonical: bool,
) -> list[dict[str, Any]]:
    from backend.api.deps import get_current_user, get_db
    from backend.api.v1.endpoints.transcripts import routes_utterances as routes

    recording = session.get(Recording, recording_id)
    transcript = session.exec(
        select(Transcript).where(Transcript.recording_id == recording_id)
    ).one()
    monkeypatch.setattr(
        routes, "_get_owned_recording", AsyncMock(return_value=recording)
    )
    monkeypatch.setattr(
        routes, "_get_recording_transcript", AsyncMock(return_value=transcript)
    )
    monkeypatch.setattr(
        routes, "_canonical_transcript_writes_enabled", lambda: canonical
    )
    db = SimpleNamespace(
        run_sync=AsyncMock(side_effect=lambda function: function(session)),
        execute=AsyncMock(
            return_value=SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [])
            )
        ),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    app = FastAPI()
    app.include_router(routes.router, prefix="/transcripts")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        response = client.get(f"/transcripts/{recording.public_id}/utterances")
    assert response.status_code == 200, response.text
    return response.json()["utterances"]


@pytest.mark.parametrize("canonical", [True, False], ids=["canonical", "projection"])
@pytest.mark.parametrize("diarise", [False, True])
def test_edge_and_interior_untimed_words_survive_storage_and_http_in_source_order(
    alignment_session: tuple[Session, int],
    monkeypatch: pytest.MonkeyPatch,
    canonical: bool,
    diarise: bool,
) -> None:
    from pyannote.core import Annotation, Segment

    from backend.processing.alignment_spans import map_alignment_spans

    session, recording_id = alignment_session
    original = "Edge, valid! interior? kept. last!"
    prediction = map_alignment_spans(
        original,
        [
            {"text": word, "start_time": start, "end_time": end}
            for word, start, end in [
                ("Edge", 0, 0),
                ("valid", 2, 3),
                ("interior", 4, 3),
                ("kept", 12, 13),
                ("last", 29, 31),
            ]
        ],
        30,
    )
    source = {
        "start": 0,
        "end": 30,
        "text": original,
        "words": [
            {
                "word": word.text,
                "start": word.start_ms / 1000 if word.start_ms is not None else None,
                "end": word.end_ms / 1000 if word.end_ms is not None else None,
                "source_start": word.source_start,
                "source_end": word.source_end,
                "source_id": "mixed",
            }
            for word in prediction.words
        ],
    }
    turns = Annotation()
    turns[Segment(0, 10)] = "A"
    turns[Segment(10, 30)] = "B"
    segments = combine_and_consolidate_segments(
        {"segments": [source]},
        turns if diarise else None,
        enable_diarization=diarise,
        recording_id=recording_id,
    )
    finalize_utterances_from_segments(
        session, recording_id=recording_id, segments=segments
    )
    session.commit()
    session.expunge_all()
    refreshed = refresh_transcript_projection_from_canonical(session, recording_id)
    expected = (
        [(2, 3, "Edge, valid! interior?"), (12, 13, "kept. last!")]
        if diarise
        else [(2, 13, original)]
    )
    assert [(row["start"], row["end"], row["text"]) for row in refreshed] == expected
    reads = serialize_canonical_utterances(
        session, recording_id, include_confidence_payload=True
    )
    words = [
        word
        for row in reads
        for source in row["confidence_payload"]["asr_segments"]
        for word in source["words"]
    ]
    assert "".join(word["word"] for word in words) == original
    assert [(w["start_ms"], w["end_ms"]) for w in words] == [
        (None, None),
        (2000, 3000),
        (None, None),
        (12000, 13000),
        (None, None),
    ]
    session.commit()
    utterances = _http_utterances(
        session, recording_id, monkeypatch, canonical=canonical
    )
    assert [(row["start"], row["end"], row["text"]) for row in utterances] == expected


@pytest.mark.parametrize("canonical", [True, False], ids=["canonical", "projection"])
def test_live_failure_reaches_public_utterance_error(
    alignment_session: tuple[Session, int],
    monkeypatch: pytest.MonkeyPatch,
    canonical: bool,
) -> None:
    from backend.models.recording import RecordingStatus
    from backend.processing.live_asr_payload import (
        _build_live_confidence_payload,
        _prepare_live_region,
    )
    from backend.processing.live_persistence import _persist_live_run

    session, recording_id = alignment_session
    recording = session.get(Recording, recording_id)
    recording.status = RecordingStatus.UPLOADING
    session.add(recording)
    session.commit()
    text, sources = _prepare_live_region(
        {"segments": [_segment("No usable timings")]}, 0
    )
    payload = _build_live_confidence_payload(
        region_segment_payloads=sources,
        region_start_ms=10_000,
        region_end_ms=15_000,
    )
    monkeypatch.setattr(
        "backend.core.db.get_sync_session", lambda: Session(session.bind)
    )
    monkeypatch.setattr(
        "backend.processing.live_persistence.config_manager.get",
        lambda key, default=None: True,
    )
    _persist_live_run(
        recording_id=recording_id,
        sequence=1,
        run=[1],
        new_segments=[
            {
                "start": 10,
                "end": 15,
                "text": text,
                "speaker": "UNKNOWN",
                "confidence_payload": payload,
            }
        ],
        state={},
        live_config={"transcription_backend": "canary"},
        live_config_hash="test",
        live_model_name="canary",
    )
    session.expire_all()
    refreshed = refresh_transcript_projection_from_canonical(session, recording_id)
    assert refreshed[0]["alignment_error"] == "No usable timings"
    session.commit()
    utterances = _http_utterances(
        session, recording_id, monkeypatch, canonical=canonical
    )
    assert utterances[0]["alignment_error"] == "No usable timings"
    assert utterances[0]["text"] == ASR_TEXT
    assert utterances[0]["state"] == "provisional"


def test_timed_to_matched_failure_replaces_alignment_evidence(
    alignment_session: tuple[Session, int],
) -> None:
    session, recording_id = alignment_session
    timed = _partial_segment()
    timed["words"][1].update(start=1.75, end=4.75)
    timed["confidence_payload"] = {
        "source_channel_activity": {"mic": 0.8},
        "other_confidence": 0.9,
    }
    first = finalize_utterances_from_segments(
        session,
        recording_id=recording_id,
        segments=[timed],
        idempotency_key="timed",
    )
    public_id = first[0].public_id
    assert first[0].confidence_payload["asr_word_timestamps_available"] is True
    session.commit()
    failure = {**_segment("No usable timings"), "text": "New failed wording!"}
    current = finalize_utterances_from_segments(
        session,
        recording_id=recording_id,
        segments=[failure],
        idempotency_key="failure",
    )
    assert current[0].public_id == public_id
    session.commit()
    session.expunge_all()
    evidence = serialize_canonical_utterances(
        session, recording_id, include_confidence_payload=True
    )[0]["confidence_payload"]
    assert evidence == {
        "source_channel_activity": {"mic": 0.8},
        "other_confidence": 0.9,
        "utterance_start_ms": 1250,
        "utterance_end_ms": 4750,
        "alignment_error": "No usable timings",
        "asr_word_timestamps_available": False,
        "asr_segments": [
            {
                "start_ms": 1250,
                "end_ms": 4750,
                "text": "New failed wording!",
                "alignment_error": "No usable timings",
            }
        ],
    }
