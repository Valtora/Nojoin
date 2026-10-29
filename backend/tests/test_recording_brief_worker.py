"""Final intelligence publishes a gist without reclaiming an edited title."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlmodel import Session, select

import backend.worker.tasks as tasks_module
from backend.models.recording import Recording
from backend.models.transcript import Transcript
from backend.services.timeline_note_service import RecordingTimelineNotes
from backend.tests.test_automatic_meeting_intelligence_worker import (
    _create_worker_ai_database,
    _sample_llm_config,
)
from backend.utils.meeting_intelligence import AutomaticMeetingIntelligenceResult


def _generate_final(session: Session) -> None:
    recording = session.get(Recording, 1)
    transcript = session.exec(
        select(Transcript).where(Transcript.recording_id == 1)
    ).one()
    tasks_module._run_automatic_meeting_intelligence_stage(
        session=session,
        task=None,
        recording=recording,
        transcript=transcript,
        speakers=[],
        transcript_text="[00:00 - 00:08] Dana: We agreed the launch date.",
        unresolved_speakers=(),
        llm_config=_sample_llm_config(),
        prefer_short_titles=True,
        device_suffix="",
        timeline_notes=RecordingTimelineNotes(),
        update_processing_status=False,
    )


@pytest.mark.parametrize("rename_during_call", [False, True])
def test_final_gist_publishes_but_user_title_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rename_during_call: bool
) -> None:
    engine = _create_worker_ai_database(tmp_path)

    def rename() -> None:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE recordings SET name = 'My launch notes', title_user_edited = 1 WHERE id = 1"
                )
            )

    class FakeLLM:
        def generate_meeting_intelligence(
            self, request: Any, timeout: int
        ) -> AutomaticMeetingIntelligenceResult:
            if rename_during_call:
                rename()
            return AutomaticMeetingIntelligenceResult(
                speaker_mapping={},
                title="Generated title",
                notes_markdown="## Summary\nThe launch date was agreed.",
                gist="The team agreed the launch date.",
            )

    monkeypatch.setattr(
        tasks_module, "_llm_backend_from_config", lambda config: FakeLLM()
    )
    if not rename_during_call:
        rename()
    try:
        with Session(engine) as session:
            _generate_final(session)
        with Session(engine) as session:
            recording = session.get(Recording, 1)
            transcript = session.exec(select(Transcript)).one()
            assert recording.name == "My launch notes"
            assert recording.title_user_edited is True
            assert recording.gist == "The team agreed the launch date."
            assert transcript.notes_status == "completed"
            assert "launch date" in transcript.notes
    finally:
        engine.dispose()


def test_final_generation_replaces_the_first_live_gist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _create_worker_ai_database(tmp_path)
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE recordings SET gist = 'The team opened with introductions.'")
        )

    class FakeLLM:
        def generate_meeting_intelligence(
            self, request: Any, timeout: int
        ) -> AutomaticMeetingIntelligenceResult:
            return AutomaticMeetingIntelligenceResult(
                speaker_mapping={},
                title="Final launch review",
                notes_markdown="## Summary\nThe launch date was agreed.",
                gist="The team agreed the launch date after reviewing open risks.",
            )

    monkeypatch.setattr(
        tasks_module, "_llm_backend_from_config", lambda config: FakeLLM()
    )
    try:
        with Session(engine) as session:
            _generate_final(session)
        with Session(engine) as session:
            recording = session.get(Recording, 1)
            assert recording.name == "Final launch review"
            assert (
                recording.gist
                == "The team agreed the launch date after reviewing open risks."
            )
    finally:
        engine.dispose()
