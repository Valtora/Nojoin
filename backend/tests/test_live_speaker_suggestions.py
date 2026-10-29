"""Live self-introductions remain proposals until a user accepts the name."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlmodel import Session, select

from backend.models.recording import Recording
from backend.models.transcript import Transcript
from backend.tests.live_naming_fixtures import ONE, TWO
from backend.tests.test_canonical_publication_fence import (
    publication_database as publication_database,
)
from backend.tests.test_live_speaker_naming_carryover import (
    _finalise,
    _name_live,
    _speakers,
)
from backend.tests.test_live_speaker_naming_carryover import (
    session as session,
)
from backend.utils.canonical_pipeline.constants import _load_transcript
from backend.utils.meeting_notes import MeetingEventContext
from backend.utils.speaker_name_suggestions import load_transcript_speaker_suggestions
from backend.utils.speaker_names.live_suggestions import (
    live_speaker_suggestions,
    refresh_live_speaker_suggestions,
    suggest_live_speaker_names,
)
from backend.worker.tasks import meeting_edge_stage


def _introduce(session: Session, label: str = ONE, words: str = "I'm Alex.") -> None:
    session.execute(text("UPDATE transcript_utterances SET text = ''"))
    session.execute(
        text(
            "UPDATE transcript_utterances SET text = :words WHERE speaker_label = :label"
        ),
        {"words": words, "label": label},
    )
    session.commit()


def _suggestions(session: Session) -> list[dict[str, Any]]:
    session.expire_all()
    return load_transcript_speaker_suggestions(session.get(Transcript, 1))


def _pending(session: Session, recording_id: int = 1) -> dict[str, str]:
    transcript = session.exec(
        select(Transcript).where(Transcript.recording_id == recording_id)
    ).one()
    return live_speaker_suggestions(
        session, session.get(Recording, recording_id), transcript
    )


def _run_edge_task(session: Session) -> None:
    task = meeting_edge_stage.refresh_meeting_edge_task
    original_session = task._session
    task._session = session
    try:
        task.run(1)
    finally:
        task._session = original_session


def test_a_short_intro_is_only_one_persisted_proposal(session: Session) -> None:
    _introduce(session)

    refresh_live_speaker_suggestions(session, 1)
    first = _suggestions(session)
    refresh_live_speaker_suggestions(session, 1)

    assert _suggestions(session) == first
    assert len(first) == 1
    assert _pending(session) == {ONE: "Alex"}
    assert first[0]["status"] == "pending"
    assert first[0]["source"] == "deterministic_rule"
    assert first[0]["origin"] == "live_self_introduction"
    assert first[0]["evidence_spans"][0]["quote"] == "I'm Alex."
    for table in ("speaker_name_events", "recording_speakers", "global_speakers"):
        assert session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one() == 0


@pytest.mark.parametrize(
    ("label", "words"),
    [
        (ONE, "I'm going to share."),
        (ONE, "Alex will show the roadmap."),
        (ONE, "Alex here."),  # Without an attendee list this is ambiguous.
        ("UNKNOWN", "I'm Alex."),
        ("SPEAKER_00", "I'm Alex."),
    ],
)
def test_only_a_live_labels_explicit_intro_is_a_proposal(
    session: Session, label: str, words: str
) -> None:
    if label not in {ONE, TWO}:
        session.execute(
            text("UPDATE transcript_utterances SET speaker_label = :label"),
            {"label": label},
        )
    _introduce(session, label, words)

    refresh_live_speaker_suggestions(session, 1)

    assert _suggestions(session) == []


def test_a_name_already_given_by_the_user_blocks_a_proposal(session: Session) -> None:
    _introduce(session)
    _name_live(session, ONE, "Blair")

    refresh_live_speaker_suggestions(session, 1)

    assert _suggestions(session) == []
    assert _pending(session) == {}


def test_a_linked_attendee_disambiguates_a_line_start_intro(session: Session) -> None:
    _introduce(session, words="Alex here.")
    suggest_live_speaker_names(
        session,
        session.get(Transcript, 1),
        [{"speaker": ONE, "text": "Alex here.", "start": 0.0, "end": 4.0}],
        MeetingEventContext(attendees=["Alex Example"]),
    )
    session.commit()

    assert _pending(session) == {ONE: "Alex Example"}
    assert _suggestions(session)[0]["signals"] == [
        "self_identification",
        "meeting_attendee_first_name",
    ]


@pytest.mark.parametrize("relabel", [False, True])
def test_revised_intro_cannot_leave_a_stale_chip_on_a_label_with_other_lines(
    session: Session, relabel: bool
) -> None:
    session.execute(
        text("UPDATE transcript_utterances SET text = 'We should ship Friday.'")
    )
    session.execute(
        text("UPDATE transcript_utterances SET text = :words WHERE start_ms = 0"),
        {"words": "I'm Alex."},
    )
    session.commit()
    refresh_live_speaker_suggestions(session, 1)
    assert _pending(session) == {ONE: "Alex"}

    mutation = (
        "UPDATE transcript_utterances SET speaker_label = :value WHERE start_ms = 0"
        if relabel
        else "UPDATE transcript_utterances SET text = :value WHERE start_ms = 0"
    )
    session.execute(
        text(mutation), {"value": TWO if relabel else "We should ship Friday."}
    )
    session.commit()
    refresh_live_speaker_suggestions(session, 1)

    assert _pending(session) == ({TWO: "Alex"} if relabel else {})
    stale = next(
        item for item in _suggestions(session) if item["diarization_label"] == ONE
    )
    assert stale["status"] == "superseded"
    if not relabel:
        session.execute(
            text("UPDATE transcript_utterances SET text = :words WHERE start_ms = 0"),
            {"words": "I'm Casey."},
        )
        session.commit()
        refresh_live_speaker_suggestions(session, 1)
        assert _pending(session) == {ONE: "Casey"}


@pytest.mark.parametrize("status", ["QUEUED", "PROCESSING", "PROCESSED", "ERROR"])
def test_a_stopped_capture_generates_no_proposal(session: Session, status: str) -> None:
    _introduce(session)
    session.execute(text("UPDATE recordings SET status = :status"), {"status": status})
    session.commit()

    refresh_live_speaker_suggestions(session, 1)

    assert _suggestions(session) == []


def test_a_paused_capture_still_generates_a_proposal(session: Session) -> None:
    _introduce(session)
    session.execute(text("UPDATE recordings SET status = 'PAUSED'"))
    session.commit()

    refresh_live_speaker_suggestions(session, 1)

    assert _pending(session) == {ONE: "Alex"}


@pytest.mark.parametrize("gate", ["held_guard", "edge_disabled", "small_signal"])
def test_cadence_names_before_edge_guards_without_an_ai_call(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    fake_single_flight: Any,
    gate: str,
) -> None:
    from backend.core.single_flight import KEY_PREFIX

    _introduce(session)
    if gate == "held_guard":
        fake_single_flight.keys[f"{KEY_PREFIX}meeting-edge:1"] = "another-worker"
    monkeypatch.setattr(
        meeting_edge_stage,
        "is_meeting_edge_enabled",
        lambda settings: gate != "edge_disabled",
    )
    config_calls: list[bool] = []

    def refuse_ai(*args: Any, **kwargs: Any) -> None:
        config_calls.append(True)
        raise AssertionError(
            "Naming a short live intro must not reach AI configuration"
        )

    monkeypatch.setattr(meeting_edge_stage, "resolve_llm_config", refuse_ai)
    _run_edge_task(session)

    assert _pending(session) == {ONE: "Alex"}
    assert config_calls == []


def test_a_failing_suggestion_pass_does_not_stop_meeting_edge(
    session: Session, monkeypatch: pytest.MonkeyPatch, fake_single_flight: Any
) -> None:
    def broken_pass(db: Session, recording_id: int) -> None:
        db.execute(text("SELECT * FROM missing_review_state"))

    monkeypatch.setattr(
        meeting_edge_stage, "refresh_live_speaker_suggestions", broken_pass
    )
    # With Edge off the refresh's only work is to clear a stale status.
    monkeypatch.setattr(meeting_edge_stage, "is_meeting_edge_enabled", lambda _: False)
    session.execute(
        text(
            "UPDATE transcripts SET meeting_edge_status = 'error', "
            "meeting_edge_error_message = 'stale'"
        )
    )
    session.commit()

    _run_edge_task(session)

    session.expire_all()
    transcript = session.get(Transcript, 1)
    assert transcript.meeting_edge_status == "idle"
    assert transcript.meeting_edge_error_message is None


def test_an_unchanged_trigger_does_not_wait_for_the_capture_fence(
    publication_database: Any,
) -> None:
    engine, recording_id, _ = publication_database
    with Session(engine) as session:
        session.execute(
            text(
                "UPDATE transcript_utterances SET speaker_label = :label, "
                "text = 'I''m Alex.' WHERE public_id = 'fence-b'"
            ),
            {"label": ONE},
        )
        session.commit()
        refresh_live_speaker_suggestions(session, recording_id)
        assert _pending(session, recording_id) == {ONE: "Alex"}

    # A rename, dismissal or Stop holds the fence while this trigger runs.
    with Session(engine) as holder, Session(engine) as trigger:
        _load_transcript(holder, recording_id, lock=True)
        trigger.execute(text("SET lock_timeout = '1s'"))
        refresh_live_speaker_suggestions(trigger, recording_id)
        holder.rollback()
        assert _pending(trigger, recording_id) == {ONE: "Alex"}


@pytest.mark.parametrize("superseded", [False, True])
def test_a_proposal_leaves_the_read_with_its_lines_or_the_capture(
    session: Session, superseded: bool
) -> None:
    _introduce(session)
    refresh_live_speaker_suggestions(session, 1)
    assert _pending(session) == {ONE: "Alex"}
    mutation = (
        "UPDATE transcript_utterances SET state = 'superseded'"
        if superseded
        else "UPDATE recordings SET status = 'QUEUED'"
    )
    session.execute(text(mutation))
    session.commit()
    session.expire_all()

    assert _pending(session) == {}


def test_an_unaccepted_proposal_never_becomes_a_user_name_at_finalisation(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _introduce(session)
    refresh_live_speaker_suggestions(session, 1)

    _finalise(session, monkeypatch)

    assert _speakers(session)["SPEAKER_01"]["local_name"] is None
    assert _speakers(session)["SPEAKER_01"]["locked"] is False
    assert (
        session.execute(text("SELECT COUNT(*) FROM speaker_name_events")).scalar_one()
        == 0
    )
