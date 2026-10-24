"""Inferred names are offered as pending names, each anchored where it was given.

SPEAKER_00 (speaker 1) speaks at 0-5 s and 5-8 s (one turn) and at 20-21 s;
SPEAKER_01 (speaker 2) speaks at 8-10 s and 30-32 s.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session

from backend.models.recording import Recording
from backend.models.recording_public import serialize_recording_speaker
from backend.models.speaker import RecordingSpeaker
from backend.tests.sqlite_schemas import (
    GLOBAL_SPEAKERS_SCHEMA,
    P_TAGS_SCHEMA,
    PEOPLE_TAGS_SCHEMA,
    RECORDING_SPEAKER_ALIASES_SCHEMA,
    RECORDING_SPEAKER_DRIFT_REFUSALS_SCHEMA,
    RECORDING_SPEAKERS_SCHEMA,
    RECORDINGS_SCHEMA,
    SPEAKER_NAME_EVENTS_SCHEMA,
    TRANSCRIPT_UTTERANCES_SCHEMA,
    USERS_SCHEMA,
)
from backend.utils.meeting_intelligence import get_speakers_eligible_for_llm_renaming
from backend.utils.speaker_name_suggestions import (
    SpeakerInferenceResult,
    SpeakerInferenceSuggestion,
)
from backend.utils.speaker_names.sources import name_is_pending
from backend.worker.tasks.speaker_name_apply import apply_inferred_names

LINES = [
    (1, 0, 5000),
    (1, 5000, 8000),
    (2, 8000, 10000),
    (1, 20000, 21000),
    (2, 30000, 32000),
]


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    with engine.begin() as connection:
        for schema in (
            USERS_SCHEMA,
            RECORDINGS_SCHEMA,
            GLOBAL_SPEAKERS_SCHEMA,
            RECORDING_SPEAKERS_SCHEMA,
            RECORDING_SPEAKER_DRIFT_REFUSALS_SCHEMA,
            RECORDING_SPEAKER_ALIASES_SCHEMA,
            PEOPLE_TAGS_SCHEMA,
            P_TAGS_SCHEMA,
            TRANSCRIPT_UTTERANCES_SCHEMA,
            SPEAKER_NAME_EVENTS_SCHEMA,
        ):
            connection.execute(text(schema))
        connection.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, status, upload_progress, processing_progress,
                    is_archived, is_deleted, user_id
                ) VALUES (1, :now, :now, 'Meeting', 'rec-1', 'uid-1',
                          '/tmp/m.wav', 'PROCESSED', 100, 100, 0, 0, 1)
                """
            ),
            {"now": _now()},
        )
        for speaker_id in (1, 2):
            connection.execute(
                text(
                    """
                    INSERT INTO recording_speakers (
                        id, created_at, updated_at, public_id, recording_id,
                        diarization_label, name, speaker_status, speaker_kind,
                        identity_locked
                    ) VALUES (:id, :now, :now, :public_id, 1, :label, :name,
                              'active', 'automated', 0)
                    """
                ),
                {
                    "id": speaker_id,
                    "now": _now(),
                    "public_id": f"rs-{speaker_id}",
                    "label": f"SPEAKER_{speaker_id - 1:02d}",
                    "name": f"Speaker {speaker_id}",
                },
            )
        for index, (speaker_id, start, end) in enumerate(LINES, start=1):
            connection.execute(
                text(
                    """
                    INSERT INTO transcript_utterances (
                        id, created_at, updated_at, public_id, recording_id,
                        sort_key, start_ms, end_ms, text, recording_speaker_id,
                        state, source_kind, revision, overlap_rank,
                        manual_text_locked, manual_speaker_locked,
                        speaker_assignment_source, speaker_assignment_authority
                    ) VALUES (:id, :now, :now, :public_id, 1, :sort_key, :start,
                              :end, 'words', :speaker, 'finalized', 'final', 1, 0,
                              0, 0, 'diarization', 'automated')
                    """
                ),
                {
                    "id": index,
                    "now": _now(),
                    "public_id": f"u-{index}",
                    "sort_key": f"{start:012d}",
                    "start": start,
                    "end": end,
                    "speaker": speaker_id,
                },
            )
    with Session(engine) as db:
        yield db
    engine.dispose()


def _suggestion(name: str, speaker_id: int = 1, **fields: Any) -> dict[str, Any]:
    return {
        "diarization_label": f"SPEAKER_{speaker_id - 1:02d}",
        "recording_speaker_id": speaker_id,
        "suggested_name": name,
        "suggested_global_speaker_id": None,
        "confidence": 0.8,
        "source": "llm",
        "status": "pending",
        "evidence_spans": [],
        **fields,
    }


def _apply(db, *suggestions: dict[str, Any]) -> list[dict[str, Any]]:
    applied = apply_inferred_names(
        db,
        db.get(Recording, 1),
        [db.get(RecordingSpeaker, 1), db.get(RecordingSpeaker, 2)],
        list(suggestions),
    )
    db.commit()
    return applied


def _events(db) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            "SELECT kind, name, source, authority, surface, anchor_start_ms, "
            "anchor_end_ms, recording_speaker_id, confidence, evidence "
            "FROM speaker_name_events ORDER BY id"
        )
    ).all()
    events = [dict(row._mapping) for row in rows]
    for event in events:
        if isinstance(event["evidence"], str):
            event["evidence"] = json.loads(event["evidence"])
    return events


def _span(start: float, end: float) -> dict[str, Any]:
    return {
        "quote": "words",
        "reason": "self_introduction",
        "start_seconds": start,
        "end_seconds": end,
    }


@pytest.mark.parametrize(
    "case",
    [
        # A self-introduction is the line the speaker said it on.
        ("deterministic_rule", [_span(20.2, 20.8)], "heuristic", (20200, 20800)),
        # The LLM's evidence is someone addressing the speaker: the name goes on
        # the speaker's own turn nearest that line.
        ("llm", [_span(9.0, 9.5)], "llm", (0, 8000)),
        ("llm", [_span(18.0, 18.5)], "llm", (20000, 21000)),
        # Without evidence, the speaker's longest turn.
        ("llm", [], "llm", (0, 8000)),
    ],
)
def test_an_inferred_name_is_pending_and_anchored_where_it_was_given(
    session, case
) -> None:
    origin, spans, source, anchor = case
    suggestion = _suggestion("Alex", source=origin, evidence_spans=spans)

    applied = _apply(session, suggestion)

    speaker = session.get(RecordingSpeaker, 1)
    assert applied == [suggestion]
    assert (speaker.local_name, speaker.global_speaker_id, speaker.name_source) == (
        "Alex",
        None,
        source,
    )
    assert (speaker.identity_locked, speaker.identity_confidence) == (False, 0.8)
    assert serialize_recording_speaker(
        speaker, recording_public_id="rec-1"
    ).name_pending
    assert suggestion["status"] == "pending"
    assert _events(session) == [
        {
            "kind": "name",
            "name": "Alex",
            "source": source,
            "authority": 10 if source == "heuristic" else 20,
            "surface": "worker",
            "anchor_start_ms": anchor[0],
            "anchor_end_ms": anchor[1],
            "recording_speaker_id": 1,
            "confidence": 0.8,
            "evidence": suggestion,
        }
    ]


def test_a_name_the_user_dismissed_is_not_offered_again(session) -> None:
    session.execute(
        text(
            """
            INSERT INTO speaker_name_events (
                id, created_at, updated_at, public_id, recording_id, kind, name,
                source, authority, anchor_start_ms, anchor_end_ms,
                recording_speaker_id
            ) VALUES (1, :now, :now, 'ev-1', 1, 'dismiss', 'ALEX', 'user', 40,
                      0, 8000, 1),
                     (2, :now, :now, 'ev-2', 1, 'name', 'Alex', 'llm', 20,
                      8000, 10000, 2)
            """
        ),
        {"now": _now()},
    )

    applied = _apply(session, _suggestion("Alex "), _suggestion("Alex", 2))

    # Dismissed for speaker 1 only, whatever its case. Speaker 2 may be Alex:
    # an earlier name event is no dismissal.
    assert [item["recording_speaker_id"] for item in applied] == [2]
    assert session.get(RecordingSpeaker, 1).local_name is None
    assert session.get(RecordingSpeaker, 2).local_name == "Alex"
    assert [(e["kind"], e["recording_speaker_id"]) for e in _events(session)] == [
        ("dismiss", 1),
        ("name", 2),
        ("name", 2),
    ]


def test_an_owner_who_takes_names_as_given_gets_them_confirmed(session) -> None:
    session.execute(
        text(
            """
            INSERT INTO users (
                id, created_at, updated_at, username, hashed_password, is_active,
                is_superuser, force_password_change, role, token_version, settings,
                has_seen_demo_recording
            ) VALUES (1, :now, :now, 'owner', 'hash', 1, 1, 0, 'owner', 0,
                      :settings, 0)
            """
        ),
        {"now": _now(), "settings": json.dumps({"confirm_suggested_names": True})},
    )
    suggestion = _suggestion("Alex")

    _apply(session, suggestion)

    speaker = session.get(RecordingSpeaker, 1)
    assert (speaker.local_name, speaker.name_source) == ("Alex", "auto")
    assert not name_is_pending(speaker)
    assert (suggestion["status"], suggestion["resolution_reason"]) == (
        "accepted",
        "auto_applied",
    )
    assert [(e["source"], e["authority"]) for e in _events(session)] == [("auto", 35)]


def _speaker(label: str, **fields: Any) -> SimpleNamespace:
    values = {
        "diarization_label": label,
        "name": None,
        "local_name": None,
        "global_speaker_id": None,
        "global_speaker": None,
        "merged_into_id": None,
        "name_source": None,
        "identity_locked": False,
    }
    values.update(fields)
    return SimpleNamespace(**values)


def test_inference_may_replace_only_its_own_pending_names() -> None:
    speakers = [
        _speaker("LLM", local_name="Alex", name_source="llm"),
        _speaker("INTRO", local_name="Sam", name_source="heuristic"),
        _speaker("NEW", name="Speaker 3"),
        _speaker(
            "VOICE",
            name_source="voiceprint",
            global_speaker_id=5,
            global_speaker=SimpleNamespace(name="Bob"),
        ),
        _speaker("USER", local_name="Dana", name_source="user", identity_locked=True),
        _speaker("AUTO", local_name="Eli", name_source="auto"),
        # A user's name carried to a speaker it only partly held.
        _speaker("CARRIED", local_name="Hal", name_source="user"),
        _speaker("OLD", local_name="Fay"),
        _speaker("MERGED", local_name="Gus", name_source="llm", merged_into_id=1),
    ]

    assert get_speakers_eligible_for_llm_renaming(speakers) == ("LLM", "INTRO", "NEW")


def test_a_new_inference_replaces_a_pending_name_but_not_a_voice_match(
    session,
) -> None:
    from backend.worker.tasks.intelligence import (
        _build_persisted_speaker_name_suggestions,
    )

    first = session.get(RecordingSpeaker, 1)
    first.local_name, first.name, first.name_source = "Alex", None, "llm"
    second = session.get(RecordingSpeaker, 2)
    second.name_source = "voiceprint"
    session.execute(
        text(
            """
            INSERT INTO global_speakers (
                id, created_at, updated_at, user_id, name, is_voiceprint_locked
            ) VALUES (5, :now, :now, 1, 'Bob', 0)
            """
        ),
        {"now": _now()},
    )
    second.global_speaker_id = 5
    session.commit()

    persisted = _build_persisted_speaker_name_suggestions(
        session,
        recording=session.get(Recording, 1),
        speakers=[first, second],
        inference_result=SpeakerInferenceResult(
            suggestions=(
                SpeakerInferenceSuggestion("SPEAKER_00", "Alexander", 0.9),
                SpeakerInferenceSuggestion("SPEAKER_01", "Robert", 0.9),
            )
        ),
        origin="speaker_inference",
        provider="openai",
    )

    assert [item["suggested_name"] for item in persisted] == ["Alexander"]


@pytest.mark.parametrize(
    "case",
    [
        ((None, False), False),
        (("auto", False), False),
        (("heuristic", False), True),
        (("llm", False), True),
        (("voiceprint", False), True),
        # A user's name carried to a speaker it only partly held.
        (("user", False), True),
        (("llm", True), False),
        (("user", True), False),
    ],
)
def test_a_name_is_pending_while_it_has_a_source_and_no_lock(case) -> None:
    (source, locked), pending = case

    speaker = _speaker("S", name_source=source, identity_locked=locked)

    assert name_is_pending(speaker) is pending
