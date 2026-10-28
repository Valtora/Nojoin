"""Durable settling provenance gates final evidence replacement across runs."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

from backend.models.pipeline import ProcessingRunKind, TranscriptUtteranceState
from backend.tests.canonical_engine import _recording, _transcript
from backend.tests.canonical_engine import (
    publication_engine as publication_engine,
)
from backend.tests.test_live_settling_finalisation import _final_segment
from backend.tests.test_live_settling_publication import _request, _words
from backend.utils.canonical_pipeline import (
    append_utterances_from_segments,
    finalize_utterances_from_segments,
    refresh_transcript_projection_from_canonical,
    serialize_canonical_utterances,
)
from backend.utils.canonical_pipeline.live_labels import LiveLabelDecision
from backend.utils.canonical_pipeline.live_settling import (
    close_live_settling,
    publish_live_settling,
)
from backend.utils.canonical_pipeline.queries import list_active_utterances
from backend.utils.config_manager import config_manager
from backend.utils.transcript_settling import update_capture_settling_state


def _state() -> dict[str, Any]:
    return {
        "version": 1,
        "eligible": True,
        "generation": "provenance-test",
        "capture_epoch": 0,
        "sealed": False,
        "phase": "live",
        "policy": {"enabled": True},
    }


def _evidence(
    engine: str, first: str, *, changed_boundary: bool = False
) -> dict[str, Any]:
    words = [
        {"word": first, "start_ms": 100, "end_ms": 400},
        {"word": "world", "start_ms": 550, "end_ms": 850},
    ]
    if engine == "canary":
        words[1]["word"] = " world"
        for word, start, end in zip(
            words, [0, len(first)], [len(first), len(first) + 6], strict=True
        ):
            word.update(source_id="forced-final", source_start=start, source_end=end)
    return {
        "utterance_start_ms": 0 if changed_boundary else 100,
        "utterance_end_ms": 1000 if changed_boundary else 850,
        "asr_segments": [
            {
                "start_ms": 0 if changed_boundary else 100,
                "end_ms": 1000 if changed_boundary else 850,
                "text": first + " world",
                "words": words,
            }
        ],
        "asr_word_timestamps_available": True,
    }


def _read(session: Session, recording_id: int) -> dict[str, Any]:
    refresh_transcript_projection_from_canonical(session, recording_id)
    session.commit()
    session.expunge_all()
    line = serialize_canonical_utterances(
        session, recording_id, include_confidence_payload=True
    )[0]
    assert _transcript(session, recording_id).segments[0].get(
        "confidence_payload"
    ) == line.get("confidence_payload")
    return line


@pytest.fixture(params=["off", "auto"])
def live_labels(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    labels = request.param
    monkeypatch.setattr(
        config_manager,
        "get",
        lambda key, default=None: labels if key == "live_speaker_labels" else default,
    )
    return labels


@pytest.mark.parametrize("engine", ["whisper", "parakeet", "canary"])
@pytest.mark.parametrize("changed_boundary", [False, True])
@pytest.mark.parametrize("marker", [None, "missing", False, 1])
def test_never_settled_final_evidence_keeps_head_behavior(
    publication_engine: Engine,
    live_labels: str,
    engine: str,
    changed_boundary: bool,
    marker: bool | int | str | None,
) -> None:
    with Session(publication_engine) as session:
        recording_id = _recording(session)
        transcript = _transcript(session, recording_id)
        state = _state()
        if marker != "missing":
            state["settled"] = marker
        transcript.settling_state = None if marker is None else state
        session.add(transcript)
        initial = _final_segment(engine, "Hello")
        initial["confidence_payload"] = _evidence(engine, "Hello")
        append_utterances_from_segments(
            session,
            recording_id=recording_id,
            segments=[initial],
            source="live",
            run_kind=ProcessingRunKind.LIVE,
        )
        session.commit()
        expected: dict[str, Any] | None = _evidence(engine, "Hello")
        for run, first in enumerate(("Goodbye", "Farewell", "Wordless")):
            final = _final_segment(engine, first)
            if changed_boundary:
                final.update(start=0.0, end=1.0)
            if run == 2:
                final.pop("words")
            elif engine == "canary":
                expected = _evidence(engine, first, changed_boundary=changed_boundary)
            elif changed_boundary:
                expected = None
            finalize_utterances_from_segments(
                session,
                recording_id=recording_id,
                segments=[final],
                idempotency_key=f"legacy-final-{run}",
            )
            line = _read(session, recording_id)
            assert line["text"] == first + " world"
            # Canonical serialization exposes null private evidence as {}.
            assert line["confidence_payload"] == (
                expected if expected is not None else {}
            )
            assert (
                list_active_utterances(session, recording_id)[0].confidence_payload
                == expected
            )


def test_only_committed_word_publication_sets_sticky_provenance(
    publication_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    with Session(publication_engine) as session:
        recording_id = _recording(session)
        transcript = _transcript(session, recording_id)
        transcript.settling_state = _state()
        session.add(transcript)
        session.commit()
        empty = publish_live_settling(
            session, request=_request(session, recording_id, 0), words=[]
        )
        assert empty.accepted and empty.changed == 0
        session.commit()
        assert not _transcript(session, recording_id).settling_state.get("settled")
        stale = _request(session, recording_id, 1)
        stale = replace(
            stale, expected_token=replace(stale.expected_token, generation="stale")
        )
        assert not publish_live_settling(
            session, request=stale, words=_words()
        ).accepted
        session.commit()
        assert not _transcript(session, recording_id).settling_state.get("settled")
        result = publish_live_settling(
            session, request=_request(session, recording_id, 1), words=_words()
        )
        assert result.accepted and result.changed > 0
        session.rollback()
        assert not _transcript(session, recording_id).settling_state.get("settled")
        assert serialize_canonical_utterances(session, recording_id) == []
        assert publish_live_settling(
            session, request=_request(session, recording_id, 1), words=_words()
        ).accepted
        session.commit()
        assert _transcript(session, recording_id).settling_state["settled"] is True
        assert publish_live_settling(
            session, request=_request(session, recording_id, 2), words=[]
        ).accepted
        session.commit()
        transcript = _transcript(session, recording_id)
        assert transcript.settling_state["settled"] is True
        monkeypatch.setattr(
            config_manager,
            "get",
            lambda key, default=None: "off" if key == "live_text_settling" else default,
        )
        update_capture_settling_state(
            transcript, capture_epoch=0, sealed=False, phase="paused"
        )
        update_capture_settling_state(
            transcript, capture_epoch=1, sealed=False, phase="live"
        )
        session.add(transcript)
        session.commit()
        session.refresh(transcript)
        assert transcript.settling_state["settled"] is True
        assert transcript.settling_state["policy"]["enabled"] is False
        assert "last_decode_id" not in transcript.settling_state


def test_protected_words_without_a_new_partition_do_not_set_provenance(
    publication_engine: Engine,
) -> None:
    with Session(publication_engine) as session:
        recording_id = _recording(session)
        transcript = _transcript(session, recording_id)
        transcript.settling_state = _state()
        session.add(transcript)
        append_utterances_from_segments(
            session,
            recording_id=recording_id,
            segments=[_final_segment("whisper", "Hello")],
            source="live",
            run_kind=ProcessingRunKind.LIVE,
            state_override=TranscriptUtteranceState.PROVISIONAL,
        )
        session.commit()
        result = publish_live_settling(
            session, request=_request(session, recording_id, 0), words=_words()
        )
        assert result.accepted and result.changed == 0
        session.commit()
        assert not _transcript(session, recording_id).settling_state.get("settled")


@pytest.mark.parametrize("engine", ["whisper", "parakeet", "canary"])
@pytest.mark.parametrize("changed_boundary", [False, True])
def test_settled_final_reprocess_replaces_words_and_same_text_wordless_clears_them(
    publication_engine: Engine, live_labels: str, engine: str, changed_boundary: bool
) -> None:
    with Session(publication_engine) as session:
        recording_id = _recording(session)
        transcript = _transcript(session, recording_id)
        transcript.settling_state = _state()
        session.add(transcript)
        session.commit()
        assert publish_live_settling(
            session,
            request=_request(session, recording_id, 0),
            words=_words(),
            decide_speaker=(
                lambda start, end: LiveLabelDecision("LIVE_PROVISIONAL_1", True, 1.0)
            )
            if live_labels == "auto"
            else None,
        ).accepted
        session.commit()
        live = serialize_canonical_utterances(
            session, recording_id, include_confidence_payload=True
        )[0]
        assert "live_speaker_label" in live["confidence_payload"]
        assert live["speaker"] == (
            "LIVE_PROVISIONAL_1" if live_labels == "auto" else "UNKNOWN"
        )
        assert _transcript(session, recording_id).settling_state["speaker_enabled"] is (
            live_labels == "auto"
        )
        assert close_live_settling(session, recording_id=recording_id)
        session.commit()
        for run, first in enumerate(("Goodbye", "Farewell", "Farewell")):
            final = _final_segment(engine, first)
            if changed_boundary:
                final.update(start=0.0, end=1.0)
            if run == 2:
                final.pop("words")
            finalize_utterances_from_segments(
                session,
                recording_id=recording_id,
                segments=[final],
                idempotency_key=f"settled-final-{run}",
            )
            line = _read(session, recording_id)
            payload = line["confidence_payload"]
            assert line["text"] == first + " world"
            assert (
                "live_text_settling" not in payload
                and "live_speaker_label" not in payload
            )
            if run < 2:
                assert (
                    payload["asr_segments"]
                    == _evidence(engine, first, changed_boundary=changed_boundary)[
                        "asr_segments"
                    ]
                )
            else:
                assert payload["asr_segments"] == [
                    {
                        "start_ms": 0 if changed_boundary else 100,
                        "end_ms": 1000 if changed_boundary else 850,
                        "text": "Farewell world",
                    }
                ]
                assert payload["asr_word_timestamps_available"] is False
        assert _transcript(session, recording_id).settling_state["settled"] is True
