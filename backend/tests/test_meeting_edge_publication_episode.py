from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text
from sqlmodel import Session

import backend.worker.tasks as tasks
from backend.models.transcript import Transcript
from backend.services.timeline_note_service import RecordingTimelineNotes
from backend.tests.test_automatic_meeting_intelligence_worker import (
    _create_worker_ai_database,
    _sample_llm_config,
)
from backend.tests.test_intelligence_publication_fence import (
    _assert_replacement,
    _replace_publication,
)
from backend.worker.tasks import meeting_edge_stage as stage


def _change_episode(engine: Any, transition: str, same_revision: bool) -> None:
    with engine.connect() as connection:
        revision = connection.execute(
            text("SELECT change_sequence FROM transcripts WHERE id = 1")
        ).scalar_one()
    if transition != "grow":
        _replace_publication(engine)
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE transcripts SET change_sequence = change_sequence + 1")
        )
        if transition == "grow":
            segments = json.loads(
                connection.execute(
                    text("SELECT segments FROM transcripts WHERE id = 1")
                ).scalar_one()
            )
            segments.append(
                {
                    "start": 8.0,
                    "end": 10.0,
                    "speaker": "SPEAKER_00",
                    "text": "More live speech",
                }
            )
            connection.execute(
                text("UPDATE transcripts SET segments = :segments"),
                {"segments": json.dumps(segments)},
            )
        if transition in {"processing", "reprocess"}:
            connection.execute(text("UPDATE recordings SET status = 'PROCESSING'"))
        if transition == "reprocess":
            connection.execute(
                text(
                    "UPDATE transcripts SET settling_state = '{\"reprocess_pending\": true}'"
                )
            )
        if transition == "resume":
            connection.execute(
                text("UPDATE recordings SET capture_epoch = capture_epoch + 1")
            )
        if transition in {"generation", "sealed", "final"}:
            states = {
                "generation": '{"generation": "replacement", "phase": "live"}',
                "sealed": '{"generation": "original", "phase": "live", "sealed": true}',
                "final": '{"generation": "original", "phase": "final"}',
            }
            connection.execute(
                text("UPDATE transcripts SET settling_state = :state"),
                {"state": states[transition]},
            )
        if same_revision:
            # Stop, seal, resume and capture-state writes can leave the public
            # transcript counter unchanged; episode checks must still run.
            connection.execute(
                text("UPDATE transcripts SET change_sequence = :revision"),
                {"revision": revision},
            )


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("same_revision", [False, True])
@pytest.mark.parametrize(
    "transition",
    ["grow", "processing", "reprocess", "resume", "generation", "sealed", "final"],
)
def test_live_meeting_edge_only_accepts_growth_in_its_unsealed_capture_episode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transition: str,
    fails: bool,
    same_revision: bool,
) -> None:
    engine = _create_worker_ai_database(tmp_path)
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE recordings SET status = 'UPLOADING', capture_epoch = 7")
        )
        connection.execute(
            text(
                'UPDATE transcripts SET settling_state = \'{"generation": "original", "phase": "live"}\''
            )
        )

    class Model:
        def generate_meeting_edge(self, *args: Any, **kwargs: Any) -> Any:
            _change_episode(engine, transition, same_revision)
            if fails:
                raise RuntimeError("Live inference failed")
            return SimpleNamespace()

    monkeypatch.setattr(stage, "is_meeting_edge_enabled", lambda settings: True)
    monkeypatch.setattr(
        stage, "load_recording_timeline_notes", lambda *args: RecordingTimelineNotes()
    )
    monkeypatch.setattr(tasks, "_has_meeting_edge_signal", lambda **kwargs: True)
    monkeypatch.setattr(
        stage, "resolve_llm_config", lambda *args, **kwargs: _sample_llm_config()
    )
    monkeypatch.setattr(tasks, "_llm_backend_from_config", lambda config: Model())
    monkeypatch.setattr(
        tasks,
        "serialize_meeting_edge_result",
        lambda result: {"summary": "Live snapshot"},
    )
    with Session(engine) as session:
        stage._refresh_meeting_edge(SimpleNamespace(session=session), 1)
    if transition == "grow":
        with Session(engine) as session:
            transcript = session.get(Transcript, 1)
            assert transcript.meeting_edge_status == ("error" if fails else "ready")
            if fails:
                assert transcript.meeting_edge_error_message == "Live inference failed"
            else:
                assert transcript.meeting_edge_payload["summary"] == "Live snapshot"
    else:
        _assert_replacement(engine)
