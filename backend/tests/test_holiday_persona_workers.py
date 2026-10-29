"""Holiday personas in the worker: notes (only with the notes surface on) and Meeting Edge.

Notes regeneration and Meeting Edge run their real tasks on SQLite with the
real settings resolution (and, for Edge, the real refresh policy) and real
backends whose SDK clients are faked, so each test reads the prompt the
provider would receive. The automatic pipeline reuses its stage's harness.
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
from backend.tests.sqlite_schemas import (
    GLOBAL_SPEAKERS_SCHEMA,
    TRANSCRIPT_UTTERANCES_SCHEMA,
)
from backend.tests.test_automatic_meeting_intelligence_worker import (
    _create_worker_ai_database,
    _FakeTask,
)
from backend.tests.test_generate_notes_task import (
    _create_notes_task_database,
    _run_generate_notes_task,
)
from backend.utils.llm_config import ResolvedLLMConfig
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
HOBBIT = persona_by_slug("hobbit")
assert PIRATE is not None and HOBBIT is not None
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


# --- Meeting Edge through the real refresh task ----------------------------------

EDGE_SEGMENTS = [
    {
        "start": 0.0,
        "end": 80.0,
        "speaker": "SPEAKER_00",
        "text": "The team discussed the launch plan. " * 30,
    }
]


@pytest.fixture
def edge_meeting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_single_flight):
    """A meeting in capture for the real Edge task, and what the provider is sent.

    The refresh policy, settings and provider resolution are real; the OpenAI
    and Anthropic SDK clients are the fake edge. Each call is recorded as its
    text blocks: one for OpenAI, the cached prefix and the suffix for Anthropic.
    """
    monkeypatch.setattr(tasks_module.config_manager, "get_all", lambda: {})
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    calls: list[list[str]] = []

    def answer() -> str:
        return json.dumps({"summary": f"Guidance {len(calls)}"})

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append([kwargs["messages"][0]["content"]])
            message = SimpleNamespace(content=answer())
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    class FakeMessages:
        def create(self, **kwargs):
            content = kwargs["messages"][0]["content"]
            calls.append([block["text"] for block in content])
            block = SimpleNamespace(type="text", text=answer())
            return SimpleNamespace(content=[block], stop_reason="end_turn")

    class FakeAnthropic:
        def __init__(self, **_kwargs):
            self.messages = FakeMessages()

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    monkeypatch.setattr("anthropic.Anthropic", FakeAnthropic)
    engines = []

    def make(personas: dict[str, Any], provider: str = "openai"):
        engine = _create_notes_task_database(
            tmp_path,
            owner_settings={
                "llm_provider": provider,
                f"{provider}_model": f"{provider}-test",
                "enable_meeting_edge": True,
                "timezone": "UTC",
                SETTINGS_KEY: personas,
            },
            transcript_segments=EDGE_SEGMENTS,
        )
        with engine.begin() as connection:
            connection.execute(text(GLOBAL_SPEAKERS_SCHEMA))
            connection.execute(text(TRANSCRIPT_UTTERANCES_SCHEMA))
            connection.execute(text("UPDATE recordings SET status = 'UPLOADING'"))
        engines.append(engine)
        return engine, calls

    yield make
    for engine in engines:
        engine.dispose()


def _refresh_edge(engine) -> dict[str, Any]:
    """Run one Edge refresh and return the stored payload."""
    task = tasks_module.refresh_meeting_edge_task
    previous = task._session
    with Session(engine) as session:
        task._session = session
        try:
            task.run(1)
        finally:
            task._session = previous
        transcript = session.exec(select(Transcript)).one()
        return dict(transcript.meeting_edge_payload or {})


@pytest.mark.parametrize("edge_on", [True, False], ids=["edge-on", "edge-off"])
def test_meeting_edge_takes_the_tone_in_its_stable_prefix(
    edge_meeting, edge_on: bool
) -> None:
    surfaces = {"chat": True, "edge": edge_on, "notes": False}
    engine, calls = edge_meeting(
        {"surfaces": surfaces, "meetings": {"1": "pirate"}}, provider="anthropic"
    )

    _refresh_edge(engine)

    [(prefix, suffix)] = calls
    section = f"{TONE_HEADING}\n{PIRATE.edge} {PERSONA_SAFETY}"
    assert "The team discussed the launch plan." in suffix
    if edge_on:
        assert prefix.rstrip().endswith(section)
        assert TONE_HEADING not in suffix
    else:
        assert TONE_HEADING not in prefix + suffix


def test_a_persona_change_refreshes_meeting_edge_at_once(edge_meeting) -> None:
    engine, calls = edge_meeting({"meetings": {"1": "pirate"}})

    _refresh_edge(engine)
    _set_personas(engine, {"meetings": {"1": "hobbit"}})
    hobbit = _refresh_edge(engine)
    _set_personas(engine, {"enabled": False, "meetings": {"1": "hobbit"}})
    off = _refresh_edge(engine)
    unchanged = _refresh_edge(engine)

    # The speech has not moved on, so only the tone changes ask the model again.
    prompts = ["".join(call) for call in calls]
    assert len(prompts) == 3
    assert PIRATE.edge in prompts[0]
    assert HOBBIT.edge in prompts[1] and PIRATE.edge not in prompts[1]
    assert TONE_HEADING not in prompts[2]
    assert hobbit["summary"] == "Guidance 2"
    assert off["summary"] == unchanged["summary"] == "Guidance 3"
