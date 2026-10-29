"""Pending display state preserves canonical identities through publication."""

from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

from backend.api.v1.endpoints.transcripts.helpers import TranscriptUtteranceRead
from backend.models.recording_public import serialize_transcript
from backend.models.transcript import Transcript
from backend.tests.test_live_settling_publication import (
    _request,
    _seed,
    _words,
)
from backend.tests.test_live_settling_publication import (
    settling_engine as settling_engine,
)
from backend.utils.canonical_pipeline.core import (
    refresh_transcript_projection_from_canonical,
    serialize_canonical_utterances,
)
from backend.utils.canonical_pipeline.live_labels import (
    LiveLabelDecision,
    apply_live_speaker_labels,
)
from backend.utils.canonical_pipeline.live_settling import (
    close_live_settling,
    expire_live_settling,
    publish_live_settling,
)
from backend.utils.canonical_pipeline.projection import serialize_canonical_delta
from backend.utils.transcript_settling import load_capture_publication


def _enable_speakers(session: Session, recording_id: int) -> Transcript:
    transcript, _ = load_capture_publication(session, recording_id)
    assert transcript is not None
    transcript.settling_state = {
        **(transcript.settling_state or {}),
        "speaker_enabled": True,
    }
    session.add(transcript)
    return transcript


def _split_decisions(start: int, _end: int) -> LiveLabelDecision:
    if start < 500:
        return LiveLabelDecision("LIVE_PROVISIONAL_1", False, 1.0)
    return LiveLabelDecision("UNKNOWN", False, None, start >= 1000)


def test_pending_and_covered_unknown_have_distinct_public_lines_and_delta(
    settling_engine: Engine,
) -> None:
    recording_id = _seed(settling_engine)
    with Session(settling_engine) as session:
        _enable_speakers(session, recording_id)
        publish_live_settling(
            session,
            request=_request(session, recording_id, 0),
            words=_words(("Hello", " covered", " tail")),
            decide_speaker=_split_decisions,
        )
        session.commit()
        cursor, lines, _ = serialize_canonical_delta(session, recording_id)
        assert [line["speaker"] for line in lines] == [
            "LIVE_PROVISIONAL_1",
            "UNKNOWN",
            "UNKNOWN",
        ]
        assert [line["diarisation_pending"] for line in lines] == [False, False, True]
        ids = [word["id"] for line in lines for word in line["words"]]
        assert all(
            TranscriptUtteranceRead(**line).diarisation_pending
            == line["diarisation_pending"]
            for line in lines
        )
        refresh_transcript_projection_from_canonical(session, recording_id)
        transcript, _ = load_capture_publication(session, recording_id)
        assert transcript is not None
        compatibility = serialize_transcript(
            transcript, recording_public_id="recording"
        )
        assert [line["diarisation_pending"] for line in compatibility.segments] == [
            False,
            False,
            True,
        ]

        def resolved(start: int, _end: int) -> LiveLabelDecision:
            speaker = "UNKNOWN" if 500 <= start < 1000 else "LIVE_PROVISIONAL_1"
            return LiveLabelDecision(speaker, False, None)

        apply_live_speaker_labels(
            session, recording_id=recording_id, settled_ms=0, decide=resolved
        )
        session.commit()
        _, changed, _ = serialize_canonical_delta(
            session, recording_id, after_revision=cursor
        )
        assert len(changed) == 1 and changed[0]["diarisation_pending"] is False
        current = serialize_canonical_utterances(session, recording_id)
        assert [line["speaker"] for line in current] == [
            "LIVE_PROVISIONAL_1",
            "UNKNOWN",
            "LIVE_PROVISIONAL_1",
        ]
        assert [word["id"] for line in current for word in line["words"]] == ids


def _labelled_then_pending(start: int, _end: int) -> LiveLabelDecision:
    if start < 500:
        return LiveLabelDecision("LIVE_PROVISIONAL_1", False, 1.0)
    return LiveLabelDecision("UNKNOWN", False, None, True)


def test_close_continues_the_previous_speaker_over_a_pending_tail(
    settling_engine: Engine,
) -> None:
    recording_id = _seed(settling_engine)
    with Session(settling_engine) as session:
        _enable_speakers(session, recording_id)
        publish_live_settling(
            session,
            request=_request(session, recording_id, 0),
            words=_words(("Hello", " pending", " tail")),
            decide_speaker=_labelled_then_pending,
        )
        session.commit()
        before = serialize_canonical_utterances(session, recording_id)
        assert [line["diarisation_pending"] for line in before] == [False, True]
        close_live_settling(session, recording_id=recording_id)
        session.commit()
        closed = serialize_canonical_utterances(session, recording_id)
        assert {line["speaker"] for line in closed} == {"LIVE_PROVISIONAL_1"}
        assert all(
            line["locked"] and not line["diarisation_pending"] for line in closed
        )
        assert [word["id"] for line in closed for word in line["words"]] == [
            word["id"] for line in before for word in line["words"]
        ]


_MICROPHONE = {"channel_count": 2, "sample_count": 100, "channel_rms": [1, 0]}
_SHARED = {"channel_count": 2, "sample_count": 100, "channel_rms": [0, 1]}


@pytest.mark.parametrize(
    ("sources", "sealed"),
    [
        ((_SHARED, _MICROPHONE), ["LIVE_PROVISIONAL_2", "UNKNOWN"]),
        (
            (_MICROPHONE, _SHARED, _MICROPHONE),
            ["LIVE_PROVISIONAL_1", "LIVE_PROVISIONAL_2", "LIVE_PROVISIONAL_1"],
        ),
    ],
)
def test_close_continues_a_speaker_only_from_the_same_source(
    settling_engine: Engine,
    sources: tuple[dict[str, Any], ...],
    sealed: list[str],
) -> None:
    labels = {id(_MICROPHONE): "LIVE_PROVISIONAL_1", id(_SHARED): "LIVE_PROVISIONAL_2"}
    starts = {100 + index * 450: source for index, source in enumerate(sources)}

    def decide(start: int, _end: int) -> LiveLabelDecision:
        if start == max(starts):
            return LiveLabelDecision("UNKNOWN", False, None, True)
        return LiveLabelDecision(labels[id(starts[start])], False, 1.0)

    words = [
        {**word, "source_channel_activity": source}
        for word, source in zip(
            _words(tuple(f" w{index}" for index in range(len(sources)))),
            sources,
            strict=True,
        )
    ]
    recording_id = _seed(settling_engine)
    with Session(settling_engine) as session:
        _enable_speakers(session, recording_id)
        publish_live_settling(
            session,
            request=_request(session, recording_id, 0),
            words=words,
            decide_speaker=decide,
        )
        session.commit()
        assert serialize_canonical_utterances(session, recording_id)[-1][
            "diarisation_pending"
        ]
        close_live_settling(session, recording_id=recording_id)
        session.commit()
        closed = serialize_canonical_utterances(session, recording_id)
        assert [line["speaker"] for line in closed] == sealed


def test_close_after_resume_continues_the_sealed_speaker(
    settling_engine: Engine,
) -> None:
    recording_id = _seed(settling_engine)
    with Session(settling_engine) as session:
        _enable_speakers(session, recording_id)
        publish_live_settling(
            session,
            request=_request(session, recording_id, 0),
            words=_words(("Hello",)),
            decide_speaker=_labelled_then_pending,
        )
        close_live_settling(session, recording_id=recording_id)
        session.commit()
        publish_live_settling(
            session,
            request=_request(session, recording_id, 1, end_ms=5000),
            words=[{"word": "Again", "start_ms": 3000, "end_ms": 3300}],
            decide_speaker=_labelled_then_pending,
        )
        session.commit()
        assert serialize_canonical_utterances(session, recording_id)[-1][
            "diarisation_pending"
        ]
        close_live_settling(session, recording_id=recording_id)
        session.commit()
        closed = serialize_canonical_utterances(session, recording_id)
        assert [line["speaker"] for line in closed] == ["LIVE_PROVISIONAL_1"] * 2
        assert closed[-1]["text"] == "Again" and closed[-1]["locked"] is True


def test_pending_survives_expiry_then_closes_unknown(
    settling_engine: Engine,
) -> None:
    recording_id = _seed(settling_engine)
    with Session(settling_engine) as session:
        _enable_speakers(session, recording_id)
        publish_live_settling(
            session, request=_request(session, recording_id, 0), words=_words()
        )
        session.commit()
        original = serialize_canonical_utterances(session, recording_id)[0]
        assert original["diarisation_pending"] is True
        expire_live_settling(
            session,
            request=replace(
                _request(session, recording_id, 1, end_ms=12000), window_start_ms=2000
            ),
        )
        session.commit()
        expired = serialize_canonical_utterances(session, recording_id)[0]
        assert expired["diarisation_pending"] is True and expired["text_locked"] is True
        assert expired["locked"] is False
        close_live_settling(session, recording_id=recording_id)
        session.commit()
        closed = serialize_canonical_utterances(session, recording_id)[0]
        assert closed["speaker"] == "UNKNOWN" and closed["diarisation_pending"] is False
        assert closed["locked"] is True
        assert [word["id"] for word in closed["words"]] == [
            word["id"] for word in original["words"]
        ]
