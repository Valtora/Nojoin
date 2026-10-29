"""One early brief per capture, fenced against Stop and explicit title edits."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlmodel import Session, select

from backend.models.recording import Recording
from backend.models.transcript import Transcript
from backend.tests.test_automatic_meeting_intelligence_worker import (
    _create_worker_ai_database,
    _sample_llm_config,
    _set_owner_settings,
)
from backend.worker.tasks import live_title


@pytest.fixture
def live_brief(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[Any, Session, list[str]]]:
    engine = _create_worker_ai_database(tmp_path)
    with engine.begin() as connection:
        _set_owner_settings(connection, {"live_title_delay_minutes": 1})
        connection.execute(
            text(
                "UPDATE recordings SET status = 'UPLOADING', client_status = 'RECORDING'"
            )
        )
    monkeypatch.setattr(
        live_title, "resolve_llm_config", lambda *args, **kwargs: _sample_llm_config()
    )
    calls: list[str] = []

    class FakeLLM:
        def generate_text(self, prompt: str, timeout: int) -> str:
            calls.append(prompt)
            return json.dumps(
                {
                    "title": "Launch review",
                    "gist": "The team discussed the Friday launch.",
                }
            )

    monkeypatch.setattr(
        live_title, "_llm_backend_from_config", lambda config: FakeLLM()
    )
    try:
        with Session(engine) as session:
            monkeypatch.setattr(
                live_title.generate_live_title_task, "_session", session
            )
            yield engine, session, calls
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "capture_state", [("UPLOADING", "RECORDING"), ("PAUSED", "PAUSED")]
)
def test_live_title_and_gist_come_from_the_stored_transcript(
    live_brief: Any,
    fake_single_flight: Any,
    capture_state: tuple[str, str],
) -> None:
    engine, session, calls = live_brief
    status, client_status = capture_state
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE recordings SET status = :status, client_status = :client_status"
            ),
            {"status": status, "client_status": client_status},
        )
    live_title.generate_live_title_task.run(1)
    assert len(calls) == 1
    # The prompt is built from the recording's own transcript, with its times.
    assert (
        "[00:04 - 00:08] SPEAKER_01 (with SPEAKER_00): Confirmed the rollout date."
        in calls[0]
    )
    with Session(engine) as check:
        recording = check.get(Recording, 1)
        assert recording.gist == "The team discussed the Friday launch."
        assert recording.gist_live is True
        assert recording.name == "Launch review"
        transcript = check.exec(select(Transcript)).one()
        assert transcript.notes is None
        assert transcript.notes_status == "pending"
        assert recording.status == status
        assert recording.client_status == client_status


@pytest.mark.parametrize(
    "settings, called",
    [("{}", True), ('{"live_title_delay_minutes": 0}', False)],
)
def test_live_llm_runs_by_default_and_not_once_turned_off(
    live_brief: Any, fake_single_flight: Any, settings: str, called: bool
) -> None:
    engine, session, calls = live_brief
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE users SET settings = :settings"), {"settings": settings}
        )
    live_title.generate_live_title_task.run(1)
    assert len(calls) == (1 if called else 0)


def test_duplicate_task_skips_the_model_when_a_call_is_in_flight(
    live_brief: Any, fake_single_flight: Any
) -> None:
    from backend.core.single_flight import KEY_PREFIX

    engine, session, calls = live_brief
    fake_single_flight.keys[f"{KEY_PREFIX}live-title:1"] = "another-worker"
    live_title.generate_live_title_task.run(1)
    assert calls == []


def test_live_title_task_is_registered_and_routed_to_io() -> None:
    from backend.celery_app import IO_QUEUE, celery_app

    celery_app.loader.import_default_modules()
    name = "backend.worker.tasks.generate_live_title_task"
    assert name in celery_app.tasks
    assert celery_app.conf.task_routes[name]["queue"] == IO_QUEUE


def test_later_text_does_not_refresh_a_successful_live_brief(
    live_brief: Any, fake_single_flight: Any
) -> None:
    engine, session, calls = live_brief
    live_title.generate_live_title_task.run(1)
    live_title.generate_live_title_task.run(1)
    assert len(calls) == 1
    with Session(engine) as check:
        assert check.get(Recording, 1).gist == "The team discussed the Friday launch."


@pytest.mark.parametrize("event", ["rename", "stop", "takeover", "delete"])
def test_state_changes_during_the_call_are_respected(
    live_brief: Any,
    fake_single_flight: Any,
    monkeypatch: pytest.MonkeyPatch,
    event: str,
) -> None:
    engine, session, calls = live_brief

    class RacingLLM:
        def generate_text(self, prompt: str, timeout: int) -> str:
            assignments = {
                "rename": "name = 'My title', title_user_edited = 1",
                "stop": "status = 'PROCESSING', client_status = 'IDLE'",
                "takeover": "capture_epoch = 1",
                "delete": "is_deleted = 1",
            }
            with engine.begin() as connection:
                connection.execute(
                    text(f"UPDATE recordings SET {assignments[event]} WHERE id = 1")
                )
            return json.dumps({"title": "Generated title", "gist": "Generated gist."})

    monkeypatch.setattr(
        live_title, "_llm_backend_from_config", lambda config: RacingLLM()
    )
    live_title.generate_live_title_task.run(1)
    with Session(engine) as check:
        recording = check.get(Recording, 1)
        if event == "rename":
            assert recording.name == "My title"
            assert recording.gist == "Generated gist."
        else:
            assert recording.name == "Planning"
            assert recording.gist is None


def test_bad_response_can_retry_without_partial_title_write(
    live_brief: Any, fake_single_flight: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, session, calls = live_brief
    answers = iter(
        [
            json.dumps({"title": "Incomplete"}),
            json.dumps({"title": "Retried", "gist": "Valid gist."}),
        ]
    )

    class RetryLLM:
        def generate_text(self, prompt: str, timeout: int) -> str:
            return next(answers)

    monkeypatch.setattr(
        live_title, "_llm_backend_from_config", lambda config: RetryLLM()
    )
    live_title.generate_live_title_task.run(1)
    with Session(engine) as check:
        recording = check.get(Recording, 1)
        assert recording.name == "Planning"
        assert recording.gist is None
    live_title.generate_live_title_task.run(1)
    with Session(engine) as check:
        recording = check.get(Recording, 1)
        assert recording.name == "Retried"
        assert recording.gist == "Valid gist."
