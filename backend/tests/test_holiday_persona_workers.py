"""Holiday personas in the worker: notes (only with the notes surface on) and Meeting Edge.

Notes regeneration runs the real task on SQLite with the real settings
resolution and a real Anthropic backend whose SDK client is faked, so the test
reads the prompt the provider would receive. The automatic pipeline and
Meeting Edge reuse those stages' own harnesses.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session, select

import backend.worker.tasks as tasks_module
from backend.models.recording import Recording
from backend.models.speaker import RecordingSpeaker
from backend.models.transcript import Transcript
from backend.processing.llm_services import AnthropicLLMBackend
from backend.services.timeline_note_service import RecordingTimelineNotes
from backend.tests.test_automatic_meeting_intelligence_worker import (
    _create_worker_ai_database,
    _FakeTask,
)
from backend.tests.test_generate_notes_task import (
    _create_notes_task_database,
    _run_generate_notes_task,
)
from backend.tests.test_meeting_edge_toggle import _FakeSession, _run_live_edge_refresh
from backend.utils.llm_config import ResolvedLLMConfig
from backend.utils.meeting_edge import build_meeting_edge_prompt_parts
from backend.utils.meeting_intelligence import (
    AutomaticMeetingIntelligenceResult,
    build_automatic_meeting_intelligence_prompt,
)
from backend.utils.persona_settings import SETTINGS_KEY
from backend.utils.personas import (
    NOTES_GUARD,
    PERSONA_SAFETY,
    TONE_HEADING,
    persona_by_slug,
)

pytestmark = pytest.mark.usefixtures("stub_text_embedding")

PIRATE = persona_by_slug("pirate")
assert PIRATE is not None
NOTES_TONE = f"{TONE_HEADING}\n{PIRATE.notes} {PERSONA_SAFETY} {NOTES_GUARD}"
PIRATE_NOTES = {"slug": "pirate", "reason": "command"}


def _personas(*, notes: bool) -> dict[str, Any]:
    return {
        "surfaces": {"chat": True, "edge": True, "notes": notes},
        "meetings": {"1": "pirate"},
        "notes": {},
    }


def _stored_personas(engine) -> dict[str, Any]:
    with Session(create_engine(str(engine.url), future=True)) as session:
        raw = session.exec(text("SELECT settings FROM users WHERE id = 1")).one()[0]
    settings = json.loads(raw) if isinstance(raw, str) else raw
    return settings.get(SETTINGS_KEY, {})


def _set_personas(engine, personas: dict[str, Any]) -> None:
    with create_engine(str(engine.url), future=True).begin() as connection:
        raw = connection.execute(text("SELECT settings FROM users WHERE id = 1")).one()[
            0
        ]
        settings = json.loads(raw) if isinstance(raw, str) else raw
        settings[SETTINGS_KEY] = personas
        connection.execute(
            text("UPDATE users SET settings = :s WHERE id = 1"),
            {"s": json.dumps(settings)},
        )


def _anthropic(prompts: list[str]) -> AnthropicLLMBackend:
    class FakeStream:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def get_final_message(self):
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="## Summary\nNotes.")],
                stop_reason="end_turn",
            )

    class FakeMessages:
        def stream(self, **kwargs):
            prompts.append(kwargs["messages"][0]["content"])
            return FakeStream()

    backend = object.__new__(AnthropicLLMBackend)
    backend.model = "claude-test"
    backend.client = SimpleNamespace(messages=FakeMessages())
    return backend


@pytest.fixture
def notes_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The regeneration task's database and the prompts its provider is sent."""
    monkeypatch.setattr(tasks_module.config_manager, "get_all", lambda: {})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-valid")

    def make(personas: dict[str, Any]):
        engine = _create_notes_task_database(
            tmp_path,
            owner_settings={
                "llm_provider": "anthropic",
                "anthropic_model": "claude-test",
                "timezone": "UTC",
                SETTINGS_KEY: personas,
            },
        )
        prompts: list[str] = []
        monkeypatch.setattr(tasks_module, "get_sync_session", lambda: Session(engine))
        monkeypatch.setattr(
            "backend.processing.llm_backends.factory.get_llm_backend",
            lambda *a, **k: _anthropic(prompts),
        )
        return engine, prompts

    return make


def test_regenerated_notes_take_the_tone_only_while_the_notes_surface_is_on(
    notes_database,
) -> None:
    engine, prompts = notes_database(_personas(notes=True))

    _run_generate_notes_task(engine)
    with_tone = _stored_personas(engine)["notes"]
    _set_personas(engine, _personas(notes=False))
    _run_generate_notes_task(engine)
    without = _stored_personas(engine)["notes"]

    first, second = prompts
    assert NOTES_TONE in first
    assert first.index("# Notes Format") < first.index(NOTES_TONE)
    assert first.index(NOTES_TONE) < first.index("# Transcript to Analyze")
    assert with_tone == {"1": PIRATE_NOTES}
    # A later regeneration with the surface off: ordinary notes, no egg.
    assert TONE_HEADING not in second and PERSONA_SAFETY not in second
    assert without == {}


def test_notes_ignore_a_meeting_persona_by_default(notes_database) -> None:
    personas = _personas(notes=False)
    del personas["surfaces"]
    engine, prompts = notes_database(personas)

    _run_generate_notes_task(engine)

    assert TONE_HEADING not in prompts[0]
    assert _stored_personas(engine) == personas


def _run_unified_stage(tmp_path: Path, monkeypatch, personas: dict[str, Any]):
    engine = _create_worker_ai_database(tmp_path)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, created_at, updated_at, username, "
                "hashed_password, is_active, is_superuser, force_password_change, "
                "role, token_version, settings, has_seen_demo_recording) VALUES "
                "(1, '2026-09-19', '2026-09-19', 'mira', 'x', 1, 0, 0, 'owner', 0, "
                ":settings, 0)"
            ),
            {"settings": json.dumps({SETTINGS_KEY: personas})},
        )
    captured: dict[str, Any] = {}

    class FakeLLM:
        def generate_meeting_intelligence(self, request, timeout: int = 60):
            captured["request"] = request
            return AutomaticMeetingIntelligenceResult(
                speaker_mapping={},
                title="Launch Review",
                notes_markdown="## Summary\nNotes.",
            )

    monkeypatch.setattr(tasks_module, "_llm_backend_from_config", lambda _c: FakeLLM())
    config = ResolvedLLMConfig(
        provider="openai",
        api_key="sk-test",
        model="gpt-test",
        api_url=None,
        # The resolver merges the user's settings in, persona state included.
        merged_config={"llm_provider": "openai", SETTINGS_KEY: personas},
    )
    with Session(engine) as session:
        tasks_module._run_automatic_meeting_intelligence_stage(
            session=session,
            task=_FakeTask(),
            recording=session.get(Recording, 1),
            transcript=session.exec(
                select(Transcript).where(Transcript.recording_id == 1)
            ).first(),
            speakers=session.exec(
                select(RecordingSpeaker).where(RecordingSpeaker.recording_id == 1)
            ).all(),
            transcript_text="[00:00:00 - 00:00:04] SPEAKER_00: Opening update.",
            unresolved_speakers=("SPEAKER_00",),
            llm_config=config,
            prefer_short_titles=True,
            device_suffix="",
            timeline_notes=RecordingTimelineNotes(),
        )
    stored = _stored_personas(engine)
    engine.dispose()
    return captured["request"], stored


@pytest.mark.parametrize("notes_on", [True, False], ids=["notes-on", "notes-off"])
def test_the_automatic_pipeline_notes_follow_the_notes_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, notes_on: bool
) -> None:
    request, stored = _run_unified_stage(
        tmp_path, monkeypatch, _personas(notes=notes_on)
    )

    prompt = build_automatic_meeting_intelligence_prompt(request)
    if notes_on:
        assert NOTES_TONE in prompt
        assert "applies to notes_markdown only" in prompt
        assert prompt.index("# Notes Markdown Requirements") < prompt.index(NOTES_TONE)
        assert prompt.index(NOTES_TONE) < prompt.index("# Transcript")
        assert stored["notes"] == {"1": PIRATE_NOTES}
    else:
        assert TONE_HEADING not in prompt
        assert stored["notes"] == {}


@pytest.mark.parametrize("edge_on", [True, False], ids=["edge-on", "edge-off"])
def test_meeting_edge_takes_the_tone_in_its_stable_prefix(
    monkeypatch: pytest.MonkeyPatch, fake_single_flight, edge_on: bool
) -> None:
    personas = {
        "surfaces": {"chat": True, "edge": edge_on, "notes": False},
        "meetings": {"1": "pirate"},
    }
    session = _FakeSession(
        SimpleNamespace(id=1, status=tasks_module.RecordingStatus.UPLOADING, user_id=7),
        SimpleNamespace(
            meeting_edge_status=tasks_module.MEETING_EDGE_STATUS_READY,
            meeting_edge_error_message=None,
            meeting_edge_payload={"summary": "Earlier guidance"},
            meeting_edge_source_signature="old-signature",
            segments=[],
            meeting_edge_focus=None,
        ),
        SimpleNamespace(
            id=7, settings={"enable_meeting_edge": True, SETTINGS_KEY: personas}
        ),
    )
    captured: dict[str, Any] = {}

    class FakeLLM:
        def generate_meeting_edge(self, request, timeout):
            captured["request"] = request
            return "Guidance"

    _run_live_edge_refresh(monkeypatch, session, FakeLLM())

    prefix, suffix = build_meeting_edge_prompt_parts(captured["request"])
    section = f"{TONE_HEADING}\n{PIRATE.edge} {PERSONA_SAFETY}"
    if edge_on:
        assert prefix.rstrip().endswith(section)
        assert TONE_HEADING not in suffix
    else:
        assert TONE_HEADING not in prefix + suffix
