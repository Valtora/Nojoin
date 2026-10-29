"""Live self-introductions remain proposals until a user accepts the name."""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import text
from sqlmodel import Session, select

from backend.models.recording import Recording
from backend.models.transcript import Transcript
from backend.processing import live_persistence
from backend.tests.live_naming_fixtures import ONE, THREE, TWO
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
from backend.worker.tasks import live_naming_stage


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


def _run_naming_task(session: Session) -> None:
    task = live_naming_stage.refresh_live_speaker_names_task
    original_session = task._session
    task._session = session
    try:
        task.run(1)
    finally:
        task._session = original_session


def _publish(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> live_persistence.LivePublicationResult:
    """One live publication whose label step changed the public lines."""
    from backend.core import db

    monkeypatch.setattr(db, "get_sync_session", lambda: Session(session.get_bind()))
    return live_persistence._persist_live_run(
        recording_id=1,
        sequence=0,
        run=[0],
        new_segments=[],
        state={},
        live_config={"_settling_public_changed": True},
        live_config_hash="config",
        live_model_name="model",
    )


def _add_owner(session: Session, settings: dict[str, Any]) -> None:
    session.execute(
        text(
            "INSERT INTO users (id, created_at, updated_at, username, "
            "hashed_password, is_active, is_superuser, force_password_change, "
            "role, token_version, settings, has_seen_demo_recording) VALUES "
            "(1, '2026-10-29', '2026-10-29', 'alice', 'x', 1, 1, 0, 'owner', 0, "
            ":settings, 1)"
        ),
        {"settings": json.dumps(settings)},
    )
    session.commit()


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


def test_suggestions_arrive_with_meeting_edge_off(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    fake_single_flight: Any,
    stub_celery_dispatch: list[Any],
) -> None:
    _add_owner(session, {"enable_meeting_edge": False})
    _introduce(session)

    publication = _publish(session, monkeypatch)
    live_naming_stage.queue_live_naming_check(1)
    _run_naming_task(session)

    assert publication.dispatch_meeting_edge is False
    assert publication.queue_live_naming is True
    assert [name for name, _, _ in stub_celery_dispatch] == [
        live_naming_stage.LIVE_NAMING_TASK
    ]
    assert _pending(session) == {ONE: "Alex"}


@pytest.mark.parametrize("unnamed", [(), (THREE,)])
def test_no_check_is_queued_once_every_live_label_is_named(
    session: Session, monkeypatch: pytest.MonkeyPatch, unnamed: tuple[str, ...]
) -> None:
    for label, name in ((ONE, "Alex"), (TWO, "Blair"), (THREE, "Casey")):
        if label not in unnamed:
            _name_live(session, label, name)

    assert _publish(session, monkeypatch).queue_live_naming is bool(unnamed)


def test_at_most_one_check_waits_per_recording(
    session: Session, fake_single_flight: Any, stub_celery_dispatch: list[Any]
) -> None:
    live_naming_stage.queue_live_naming_check(1)
    live_naming_stage.queue_live_naming_check(1)
    assert len(stub_celery_dispatch) == 1

    # Lines published once the check has started need a check of their own.
    _run_naming_task(session)
    live_naming_stage.queue_live_naming_check(1)
    assert len(stub_celery_dispatch) == 2


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
