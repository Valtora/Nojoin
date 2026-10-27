"""Authoritative final words replace closed live words before publication."""

from __future__ import annotations

from typing import Any

import pytest
from pyannote.core import Annotation, Segment
from sqlalchemy import Engine
from sqlmodel import Session

from backend.models.recording_public import serialize_transcript
from backend.tests.test_live_settling_publication import _request
from backend.tests.test_transcript_publication import _recording, _transcript
from backend.tests.test_transcript_publication import (
    publication_engine as publication_engine,
)
from backend.utils.canonical_pipeline import (
    finalize_utterances_from_segments,
    refresh_transcript_projection_from_canonical,
    serialize_canonical_delta,
    serialize_canonical_utterances,
)
from backend.utils.canonical_pipeline.live_settling import (
    close_live_settling,
    publish_live_settling,
)
from backend.utils.config_manager import config_manager
from backend.utils.word_text import reconstruct_text_from_words
from backend.worker.tasks.final_segments import combine_and_consolidate_segments


def _closed_line(session: Session) -> tuple[int, dict[str, Any]]:
    recording_id = _recording(session)
    transcript = _transcript(session, recording_id)
    transcript.settling_state = {
        "version": 1,
        "eligible": True,
        "generation": "finalisation-test",
        "capture_epoch": 0,
        "sealed": False,
        "phase": "live",
        "policy": {"k": 3, "window_s": 10, "enabled": True},
    }
    session.add(transcript)
    session.commit()
    assert publish_live_settling(
        session,
        request=_request(session, recording_id, 0),
        words=[
            {"word": "Hello", "start_ms": 100, "end_ms": 400},
            {"word": " world", "start_ms": 550, "end_ms": 850},
        ],
    ).accepted
    session.commit()
    assert close_live_settling(session, recording_id=recording_id)
    session.commit()
    return recording_id, serialize_canonical_utterances(session, recording_id)[0]


def _final_segment(engine: str, first: str = "Goodbye") -> dict[str, Any]:
    words = [
        {"word": first, "start": 0.1, "end": 0.4},
        {"word": " world", "start": 0.55, "end": 0.85},
    ]
    if engine == "whisper":
        words = [{**word, "probability": 0.95} for word in words]
    elif engine == "parakeet":
        words[0]["word"] = " " + first
    elif engine == "canary":
        words = [
            {
                **word,
                "source_id": "forced-final",
                "source_start": start,
                "source_end": end,
            }
            for word, start, end in zip(
                words, [0, len(first)], [len(first), len(first) + 6], strict=True
            )
        ]
    return {
        "start": 0.1,
        "end": 0.85,
        "text": first + " world",
        "speaker": "UNKNOWN",
        "words": words,
    }


def _assert_final_reads(
    session: Session,
    recording_id: int,
    text: str,
    *,
    timings: list[tuple[int | None, int | None]] | None = None,
) -> dict[str, Any]:
    # This is the worker's pre-commit projection refresh, where forced words
    # previously raised validation errors against the stale live marker.
    refreshed = refresh_transcript_projection_from_canonical(session, recording_id)
    session.commit()
    session.expunge_all()
    canonical = serialize_canonical_utterances(
        session, recording_id, include_confidence_payload=True
    )[0]
    payload = canonical["confidence_payload"]
    words = [
        word for source in payload["asr_segments"] for word in source.get("words", [])
    ]
    assert canonical["text"] == text == reconstruct_text_from_words(words)
    assert "live_text_settling" not in payload
    assert "live_speaker_label" not in payload
    assert all("locked" not in word and "unchanged_count" not in word for word in words)
    assert [(word["start_ms"], word["end_ms"]) for word in words] == (
        timings if timings is not None else [(100, 400), (550, 850)]
    )
    assert refreshed[0]["confidence_payload"] == payload
    _, delta, tombstones = serialize_canonical_delta(session, recording_id)
    assert not tombstones
    public = serialize_transcript(
        _transcript(session, recording_id), recording_public_id="recording"
    ).segments
    for line in (canonical, delta[0], public[0]):
        assert line["text"] == text
        assert not line.get("text_locked") and not line.get("locked")
    return canonical


@pytest.fixture(autouse=True)
def labels_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        config_manager,
        "get",
        lambda key, default=None: {"live_speaker_labels": "off"}.get(key, default),
    )


@pytest.mark.parametrize("engine", ["whisper", "parakeet", "canary"])
def test_final_words_replace_closed_live_words_before_publication(
    publication_engine: Engine, engine: str
) -> None:
    """Both audit probes: native disagreement and forced-word validation."""
    with Session(publication_engine) as session:
        recording_id, closed = _closed_line(session)
        rows = finalize_utterances_from_segments(
            session, recording_id=recording_id, segments=[_final_segment(engine)]
        )
        assert rows[0].public_id == closed["id"]
        _assert_final_reads(session, recording_id, "Goodbye world")


@pytest.mark.parametrize("engine", ["whisper", "parakeet", "canary"])
@pytest.mark.parametrize("diarised", [False, True])
def test_worker_final_segments_and_reprocess_keep_latest_words(
    publication_engine: Engine, engine: str, diarised: bool
) -> None:
    with Session(publication_engine) as session:
        recording_id, closed = _closed_line(session)
        turns = Annotation()
        turns[Segment(0.0, 1.0)] = "SPEAKER_00"
        for run, first in enumerate(("Goodbye", "Farewell")):
            final_segments = combine_and_consolidate_segments(
                {"segments": [_final_segment(engine, first)]},
                turns if diarised else None,
                enable_diarization=diarised,
                recording_id=recording_id,
            )
            assert len(final_segments) == 1
            assert final_segments[0]["speaker"] == (
                "SPEAKER_00" if diarised else "UNKNOWN"
            )
            rows = finalize_utterances_from_segments(
                session,
                recording_id=recording_id,
                segments=final_segments,
                idempotency_key=f"final-run-{run}",
            )
            assert rows[0].public_id == closed["id"]
            _assert_final_reads(session, recording_id, first + " world")


def test_partial_final_words_replace_live_without_inventing_timing(
    publication_engine: Engine,
) -> None:
    with Session(publication_engine) as session:
        recording_id, _ = _closed_line(session)
        final = _final_segment("canary")
        final["words"][1].update(start=None, end=None)
        final["alignment_error"] = "Second word could not be placed"
        finalize_utterances_from_segments(
            session, recording_id=recording_id, segments=[final]
        )
        result = _assert_final_reads(
            session, recording_id, "Goodbye world", timings=[(100, 400), (None, None)]
        )
        assert result["alignment_error"] == "Second word could not be placed"


@pytest.mark.parametrize("error", [None, "Alignment unavailable"])
@pytest.mark.parametrize("incoming_live_payload", [False, True])
def test_wordless_final_discards_live_evidence_and_lock_metadata(
    publication_engine: Engine, error: str | None, incoming_live_payload: bool
) -> None:
    with Session(publication_engine) as session:
        recording_id, _ = _closed_line(session)
        final = _final_segment("canary")
        final.pop("words")
        if incoming_live_payload:
            final["confidence_payload"] = serialize_canonical_utterances(
                session, recording_id, include_confidence_payload=True
            )[0]["confidence_payload"]
        if error:
            final["alignment_error"] = error
        finalize_utterances_from_segments(
            session, recording_id=recording_id, segments=[final]
        )
        refresh_transcript_projection_from_canonical(session, recording_id)
        session.commit()
        line = serialize_canonical_utterances(
            session, recording_id, include_confidence_payload=True
        )[0]
        payload = line["confidence_payload"]
        assert line["text"] == "Goodbye world"
        assert "live_text_settling" not in payload
        assert "live_speaker_label" not in payload
        assert not any(
            source.get("words") for source in payload.get("asr_segments", [])
        )
        assert line.get("alignment_error") == error


@pytest.mark.parametrize("engine", ["whisper", "parakeet", "canary"])
def test_wordless_reprocess_discards_previous_final_words(
    publication_engine: Engine, engine: str
) -> None:
    with Session(publication_engine) as session:
        recording_id, closed = _closed_line(session)
        finalize_utterances_from_segments(
            session,
            recording_id=recording_id,
            segments=[_final_segment(engine)],
            idempotency_key="timed-final",
        )
        _assert_final_reads(session, recording_id, "Goodbye world")
        final = _final_segment(engine, "Farewell")
        final.pop("words")
        rows = finalize_utterances_from_segments(
            session,
            recording_id=recording_id,
            segments=[final],
            idempotency_key="wordless-reprocess",
        )
        assert rows[0].public_id == closed["id"]
        refresh_transcript_projection_from_canonical(session, recording_id)
        session.commit()
        result = serialize_canonical_utterances(
            session, recording_id, include_confidence_payload=True
        )[0]
        assert result["text"] == "Farewell world"
        assert not any(
            source.get("words")
            for source in result["confidence_payload"].get("asr_segments", [])
        )
