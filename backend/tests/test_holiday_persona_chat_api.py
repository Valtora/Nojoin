"""Holiday personas through the real Meeting Chat and recording endpoints.

The routes run on SQLite with the caller loaded in the request's own session,
so a command's settings write is read back by the next request. The provider
is a real backend whose SDK client is faked (the LLM edge), so each test reads
the prompt that would have been sent. The clock is fixed through the persona
module's ``utc_now``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.utils.persona_settings as persona_settings
from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.api.v1.endpoints.transcripts import routes_chat
from backend.celery_app import celery_app
from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.processing.llm_backends.base import LLMBackend
from backend.processing.llm_backends.factory import SecondaryLLMBackend
from backend.processing.llm_services import (
    AnthropicLLMBackend,
    GeminiLLMBackend,
    OllamaLLMBackend,
    OpenAILLMBackend,
)
from backend.tests.sqlite_schemas import (
    DOCUMENTS_SCHEMA,
    RECORDING_SPEAKERS_SCHEMA,
    RECORDING_TAGS_SCHEMA,
    RECORDINGS_SCHEMA,
    TAGS_SCHEMA,
    TIMELINE_NOTES_SCHEMA,
    TRANSCRIPT_UTTERANCES_SCHEMA,
    TRANSCRIPTS_SCHEMA,
    USERS_SCHEMA,
)
from backend.tests.test_chat_cli_relay_api import _fake_async, _rag_and_dispatch_stubs
from backend.tests.test_timeline_notes_api import _session_user
from backend.utils.llm_config import ResolvedLLMConfig
from backend.utils.personas import (
    PERSONA_SAFETY,
    TONE_HEADING,
    Surface,
    persona_by_slug,
)

REC = "rec-1"
CHAT = f"/api/v1/transcripts/{REC}/chat"
DETAIL = f"/api/v1/recordings/{REC}"
NOTES = "# Notes\n- launch moved to May"
TRANSCRIPT = "[00:01] Speaker A: we move the launch to May"
PIRATE_DAY = datetime(2026, 9, 19, 12, 0)
ORDINARY_DAY = datetime(2026, 3, 3, 12, 0)

CHAT_MESSAGES_SCHEMA = """
CREATE TABLE chat_messages (
    id INTEGER PRIMARY KEY, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
    recording_id INTEGER NOT NULL, user_id INTEGER NOT NULL, role VARCHAR NOT NULL,
    content TEXT NOT NULL
)
"""


def _line(slug: str, surface: Surface = "chat") -> str:
    persona = persona_by_slug(slug)
    assert persona is not None
    return persona.line(surface)


# --- the LLM edge: real backends, fake SDK clients ---------------------------


def _anthropic(prompts: list[str]) -> LLMBackend:
    class FakeStream:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def __iter__(self):
            return iter(())

        def get_final_message(self):
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="An answer.")],
                stop_reason="end_turn",
            )

    class FakeMessages:
        def stream(self, **kwargs):
            prompts.append("".join(block["text"] for block in kwargs["system"]))
            return FakeStream()

        def create(self, **kwargs):
            prompts.append("".join(block["text"] for block in kwargs["system"]))
            return FakeStream().get_final_message()

    backend = object.__new__(AnthropicLLMBackend)
    backend.model = "claude-test"
    backend.client = SimpleNamespace(messages=FakeMessages())
    return backend


def _openai(prompts: list[str]) -> LLMBackend:
    class FakeCompletions:
        def create(self, **kwargs):
            prompts.append(kwargs["messages"][0]["content"])
            if kwargs.get("stream"):
                return iter(())
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="An answer."))]
            )

    backend = object.__new__(OpenAILLMBackend)
    backend.model = "gpt-test"
    backend.client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )
    return backend


def _gemini(prompts: list[str]) -> LLMBackend:
    from google import genai

    class FakeModels:
        def generate_content(self, **kwargs):
            prompts.append(kwargs["contents"][-1]["parts"][0]["text"])
            return SimpleNamespace(candidates=[], text="An answer.")

        def generate_content_stream(self, **kwargs):
            prompts.append(kwargs["contents"][-1]["parts"][0]["text"])
            return iter(())

    backend = object.__new__(GeminiLLMBackend)
    backend.model = "gemini-test"
    backend.genai = genai
    backend.client = SimpleNamespace(models=FakeModels())
    return backend


def _ollama(prompts: list[str]) -> LLMBackend:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"message": {"content": "An answer."}, "done": True}

        def iter_lines(self):
            yield json.dumps(self.json()).encode("utf-8")

    class FakeRequests:
        def post(self, url, json, timeout, allow_redirects, stream=False):
            if "messages" in json:
                prompts.append(json["messages"][-1]["content"])
            return FakeResponse()

    backend = object.__new__(OllamaLLMBackend)
    backend.model = "llama-test"
    backend.api_url = "http://ollama.local"
    backend.context_window = 131072
    backend.requests = FakeRequests()
    return backend


PROVIDERS: dict[str, Callable[[list[str]], LLMBackend]] = {
    "anthropic": _anthropic,
    "openai": _openai,
    "gemini": _gemini,
    "ollama": _ollama,
}


# --- the app -----------------------------------------------------------------


@pytest.fixture
async def session_maker() -> AsyncIterator[sessionmaker]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        for schema in (
            USERS_SCHEMA,
            RECORDINGS_SCHEMA,
            TRANSCRIPTS_SCHEMA,
            RECORDING_SPEAKERS_SCHEMA,
            RECORDING_TAGS_SCHEMA,
            TAGS_SCHEMA,
            TIMELINE_NOTES_SCHEMA,
            DOCUMENTS_SCHEMA,
            TRANSCRIPT_UTTERANCES_SCHEMA,
            CHAT_MESSAGES_SCHEMA,
        ):
            await connection.execute(text(schema))
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        session.add(
            User(
                id=1,
                username="mira",
                hashed_password="x",
                role="user",
                settings={"timezone": "UTC"},
            )
        )
        session.add(
            Recording(
                id=1,
                public_id=REC,
                meeting_uid="meeting-1",
                name="Weekly sync",
                audio_path="meeting.webm",
                status=RecordingStatus.PROCESSED,
                user_id=1,
            )
        )
        session.add(
            Transcript(
                recording_id=1,
                segments=[],
                notes=NOTES,
                notes_status="completed",
                transcript_status="completed",
            )
        )
        await session.commit()
    try:
        yield maker
    finally:
        await engine.dispose()


@pytest.fixture
def prompts() -> list[str]:
    """Every prompt a provider was sent, in order."""
    return []


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Callable[[datetime], None]:
    def set_now(now: datetime) -> None:
        monkeypatch.setattr(persona_settings, "utc_now", lambda: now)

    set_now(ORDINARY_DAY)
    return set_now


@pytest.fixture
def use_provider(
    monkeypatch: pytest.MonkeyPatch, prompts: list[str]
) -> Callable[[str], None]:
    """Answer chat with ``provider``'s real backend, behind the secondary wrapper."""

    def use(provider: str) -> None:
        backend = PROVIDERS[provider](prompts)
        monkeypatch.setattr(
            "backend.api.v1.endpoints.transcripts.get_llm_backend_with_secondary",
            lambda _config: SecondaryLLMBackend(primary=backend, secondary=backend),
        )

    use("openai")
    return use


@pytest.fixture
async def client(
    session_maker: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
    clock,
    use_provider,
) -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    async def _db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = _session_user(1)
    config = ResolvedLLMConfig(
        provider="openai",
        api_key="sk-test",
        model="gpt-test",
        api_url=None,
        merged_config={},
    )
    monkeypatch.setattr(
        routes_chat, "resolve_llm_config_async", lambda *a, **k: _fake_async(config)
    )
    monkeypatch.setattr(celery_app, "send_task", _rag_and_dispatch_stubs([]))
    monkeypatch.setattr(routes_chat, "async_session_maker", session_maker)
    # The transcript is read through the worker's sync engine, which these
    # SQLite tests do not have.
    monkeypatch.setattr(
        LLMBackend,
        "get_mapped_transcript_for_llm",
        staticmethod(lambda *a, **k: TRANSCRIPT),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as async_client:
        yield async_client


async def _send(client: AsyncClient, message: str) -> str:
    response = await client.post(CHAT, json={"message": message})
    assert response.status_code == 200, response.text
    return response.text


def _reply(body: str) -> str:
    tokens = [
        json.loads(line[6:])["token"]
        for line in body.splitlines()
        if line.startswith('data: {"token"')
    ]
    return "".join(tokens)


async def _stored(session_maker: sessionmaker) -> dict[str, Any]:
    async with session_maker() as session:
        user = await session.get(User, 1)
        assert user is not None
        return (user.settings or {}).get(persona_settings.SETTINGS_KEY, {})


async def _history(session_maker: sessionmaker) -> list[tuple[str, str]]:
    async with session_maker() as session:
        rows = await session.execute(
            text("SELECT role, content FROM chat_messages ORDER BY created_at, id")
        )
        return [(role, content) for role, content in rows.all()]


async def _personas(client: AsyncClient) -> dict[str, Any]:
    response = await client.get(DETAIL)
    assert response.status_code == 200, response.text
    return response.json()["personas"]


async def _ask(client: AsyncClient, prompts: list[str]) -> str:
    """Ask an ordinary question and return the prompt the provider received."""
    before = len(prompts)
    await _send(client, "What was decided?")
    assert len(prompts) == before + 1
    return prompts[-1]


# --- the commands --------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    "case",
    [
        (
            "/persona pirate",
            "Persona for this meeting: pirate.",
            {"meetings": {"1": "pirate"}},
        ),
        (
            "  /PERSONA Pirate \n",
            "Persona for this meeting: pirate.",
            {"meetings": {"1": "pirate"}},
        ),
        ("/eastereggs on", "Holiday personas on.", {"enabled": True}),
        ("/EasterEggs TRUE", "Holiday personas on.", {"enabled": True}),
        ("/eastereggs enabled", "Holiday personas on.", {"enabled": True}),
        ("/eastereggs yes", "Holiday personas on.", {"enabled": True}),
        ("/eastereggs off", "Holiday personas off.", {"enabled": False}),
        ("/eastereggs False", "Holiday personas off.", {"enabled": False}),
        ("/eastereggs disabled", "Holiday personas off.", {"enabled": False}),
        ("/eastereggs NO", "Holiday personas off.", {"enabled": False}),
        (
            "/eastereggs chat off",
            "Holiday personas in Chat: off.",
            {"surfaces": {"chat": False, "edge": True, "notes": False}},
        ),
        (
            "/eastereggs Edge No",
            "Holiday personas in Meeting Edge: off.",
            {"surfaces": {"chat": True, "edge": False, "notes": False}},
        ),
        (
            "/eastereggs notes yes",
            "Holiday personas in notes: on.",
            {"surfaces": {"chat": True, "edge": True, "notes": True}},
        ),
    ],
)
async def test_each_command_form_is_answered_without_the_model(
    client: AsyncClient,
    session_maker: sessionmaker,
    prompts: list[str],
    case: tuple[str, str, dict[str, Any]],
) -> None:
    message, reply, stored = case
    body = await _send(client, message)

    assert _reply(body) == reply
    assert "event: persona_update" in body
    assert prompts == []
    state = await _stored(session_maker)
    for key, value in stored.items():
        assert state[key] == value
    assert await _history(session_maker) == [("user", message), ("assistant", reply)]


@pytest.mark.anyio
@pytest.mark.parametrize("word", ["off", "FALSE", "disabled"])
async def test_persona_off_clears_this_meetings_persona(
    client: AsyncClient, session_maker: sessionmaker, word: str
) -> None:
    await _send(client, "/persona pirate")

    body = await _send(client, f"/persona {word}")

    assert _reply(body) == "Persona for this meeting: off."
    assert (await _stored(session_maker))["meetings"] == {}


@pytest.mark.anyio
@pytest.mark.parametrize("message", ["/persona", "/EASTEREGGS", "  /eastereggs  "])
async def test_a_bare_command_reports_the_state_and_usage(
    client: AsyncClient,
    session_maker: sessionmaker,
    prompts: list[str],
    message: str,
) -> None:
    await _send(client, "/persona hobbit")
    await _send(client, "/eastereggs edge off")

    body = await _send(client, message)

    assert _reply(body) == (
        "Dated personas: off. Surfaces: Chat on, Meeting Edge off, notes off. "
        "This meeting: hobbit. Usage: /persona <name>, /persona off, "
        "/eastereggs on|off, /eastereggs chat|edge|notes on|off."
    )
    assert "event: persona_update" not in body
    assert prompts == []


@pytest.mark.anyio
async def test_an_unknown_name_lists_the_valid_ones_and_changes_nothing(
    client: AsyncClient, session_maker: sessionmaker, prompts: list[str]
) -> None:
    body = await _send(client, "/persona dragon")

    reply = _reply(body)
    assert reply.startswith('Unknown persona "dragon". Valid names: ')
    assert "pirate" in reply and "hobbit" in reply and "crossword" in reply
    assert "event: persona_update" not in body
    assert prompts == []
    assert await _stored(session_maker) == {}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "message",
    [
        "/help",
        "/personas pirate",
        "/persona talk like a pirate",
        "/eastereggs maybe",
        "/eastereggs chat sometimes",
        "/eastereggs on please",
        "Please /persona pirate",
    ],
)
async def test_other_slash_text_goes_to_the_model(
    client: AsyncClient,
    session_maker: sessionmaker,
    prompts: list[str],
    message: str,
) -> None:
    await _send(client, message)

    assert len(prompts) == 1
    assert await _stored(session_maker) == {}
    assert [role for role, _ in await _history(session_maker)] == ["user", "assistant"]


# --- which persona applies -------------------------------------------------------


@pytest.mark.anyio
async def test_a_meeting_persona_beats_the_dated_one_and_falls_back_to_it(
    client: AsyncClient, prompts: list[str], clock
) -> None:
    clock(PIRATE_DAY)
    # Dated personas are off until turned on: the day alone does nothing.
    assert TONE_HEADING not in await _ask(client, prompts)

    await _send(client, "/eastereggs on")
    dated = await _ask(client, prompts)
    await _send(client, "/persona hobbit")
    chosen = await _ask(client, prompts)
    await _send(client, "/persona off")
    back = await _ask(client, prompts)

    assert _line("pirate") in dated
    assert _line("hobbit") in chosen and _line("pirate") not in chosen
    assert _line("pirate") in back and _line("hobbit") not in back


@pytest.mark.anyio
async def test_a_meeting_persona_applies_without_dated_personas(
    client: AsyncClient, prompts: list[str]
) -> None:
    await _send(client, "/persona sherlock")

    prompt = await _ask(client, prompts)

    assert _line("sherlock") in prompt


@pytest.mark.anyio
async def test_eastereggs_off_suppresses_a_meeting_persona_and_keeps_it(
    client: AsyncClient, session_maker: sessionmaker, prompts: list[str]
) -> None:
    await _send(client, "/persona pirate")
    await _send(client, "/eastereggs off")
    off = await _ask(client, prompts)
    off_personas = await _personas(client)
    reply = _reply(await _send(client, "/persona hobbit"))
    await _send(client, "/eastereggs on")
    on = await _ask(client, prompts)

    assert TONE_HEADING not in off
    assert off_personas == {"chat": None, "edge": None, "notes": None}
    assert "Easter eggs are off" in reply
    assert (await _stored(session_maker))["meetings"] == {"1": "hobbit"}
    assert _line("hobbit") in on


@pytest.mark.anyio
async def test_the_chat_surface_switch_keeps_the_persona_out_of_chat_only(
    client: AsyncClient, prompts: list[str]
) -> None:
    await _send(client, "/persona pirate")
    await _send(client, "/eastereggs chat off")

    prompt = await _ask(client, prompts)
    personas = await _personas(client)

    assert TONE_HEADING not in prompt
    assert personas["chat"] is None
    assert personas["edge"]["slug"] == "pirate"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("now", "slug"),
    [
        # First Saturday of February, which in 2026 is also e Day: the earlier
        # in the list wins.
        (datetime(2026, 2, 7, 12), "ice-cream"),
        (datetime(2027, 2, 6, 12), "ice-cream"),
        (datetime(2027, 2, 7, 12), "e-day"),
        (datetime(2026, 2, 14, 12), None),
        # The Saturday nearest 17 June: 20 June in 2026, 19 June in 2027.
        (datetime(2026, 6, 20, 12), "juggling"),
        (datetime(2027, 6, 19, 12), "juggling"),
        (datetime(2026, 6, 13, 12), None),
        (datetime(2027, 6, 26, 12), None),
    ],
)
async def test_date_rules_and_clashes_pick_the_days_persona(
    client: AsyncClient, clock, now: datetime, slug: str | None
) -> None:
    clock(now)
    await _send(client, "/eastereggs on")

    chat = (await _personas(client))["chat"]

    assert (chat["slug"] if chat else None) == slug


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("zone", "slug"),
    [("Europe/London", "pirate"), ("America/New_York", None)],
)
async def test_the_users_time_zone_decides_the_day(
    client: AsyncClient,
    session_maker: sessionmaker,
    clock,
    zone: str,
    slug: str | None,
) -> None:
    # 03:00 UTC on 19 September: already the 19th in London, still the 18th
    # in New York.
    clock(datetime(2026, 9, 19, 3, 0))
    async with session_maker() as session:
        user = await session.get(User, 1)
        assert user is not None
        user.settings = {**user.settings, "timezone": zone}
        await session.commit()
    await _send(client, "/eastereggs on")

    chat = (await _personas(client))["chat"]
    today = persona_settings.user_today({"timezone": zone})

    assert today.day == (19 if zone == "Europe/London" else 18)
    assert (chat["slug"] if chat else None) == slug


# --- the prompt each provider receives -------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("provider", list(PROVIDERS))
async def test_every_provider_gets_the_tone_section_and_none_when_off(
    client: AsyncClient, prompts: list[str], use_provider, provider: str
) -> None:
    use_provider(provider)
    await _send(client, "/persona pirate")
    on = await _ask(client, prompts)
    await _send(client, "/persona off")
    off = await _ask(client, prompts)

    section = f"{TONE_HEADING}\n{_line('pirate')} {PERSONA_SAFETY}"
    assert section in on
    # After the instructions, before the meeting content.
    assert on.index("# WHAT YOU CAN DO") < on.index(section) < on.index(NOTES)
    assert on.index(section) < on.index(TRANSCRIPT)
    assert TONE_HEADING not in off and PERSONA_SAFETY not in off


@pytest.mark.parametrize("provider", list(PROVIDERS))
def test_the_one_shot_chat_gets_the_tone_section(provider: str) -> None:
    prompts: list[str] = []
    backend = PROVIDERS[provider](prompts)
    wrapped = SecondaryLLMBackend(primary=backend, secondary=backend)

    wrapped.ask_question_about_meeting(
        "What was decided?", NOTES, TRANSCRIPT, tone="Speak softly."
    )
    wrapped.ask_question_about_meeting("What was decided?", NOTES, TRANSCRIPT)

    assert f"{TONE_HEADING}\nSpeak softly." in prompts[0]
    assert TONE_HEADING not in prompts[1]


@pytest.mark.parametrize("stream", [True, False], ids=["stream", "one-shot"])
def test_the_subscription_backend_prompt_gets_the_tone_section(stream: bool) -> None:
    from backend.processing.cli_backend import CliLLMBackend

    prompts: list[str] = []

    class FakeManager:
        def run_single_turn(self, user_id, prompt, *, model=None, timeout=300):
            prompts.append(prompt)
            return "An answer."

        def stream_single_turn(self, user_id, prompt, *, model=None, timeout=300):
            prompts.append(prompt)
            yield "An answer."

    backend = CliLLMBackend(model="claude-test", user_id=7)
    backend._manager = FakeManager()
    ask = (
        backend.ask_question_streaming if stream else backend.ask_question_about_meeting
    )
    answer = ask("What was decided?", NOTES, TRANSCRIPT, tone="Speak softly.")
    if stream:
        list(answer)

    assert f"{TONE_HEADING}\nSpeak softly." in prompts[0]
    assert prompts[0].index(TONE_HEADING) < prompts[0].index(NOTES)


# --- the API state behind the egg ------------------------------------------------


@pytest.mark.anyio
async def test_the_recording_read_says_which_persona_applies_and_why(
    client: AsyncClient, session_maker: sessionmaker, clock
) -> None:
    clock(PIRATE_DAY)
    nothing = await _personas(client)
    await _send(client, "/eastereggs on")
    dated = await _personas(client)
    await _send(client, "/persona hobbit")
    chosen = await _personas(client)
    async with session_maker() as session:
        user = await session.get(User, 1)
        assert user is not None
        state = dict(user.settings[persona_settings.SETTINGS_KEY])
        state["notes"] = {"1": {"slug": "pirate", "reason": "dated"}}
        user.settings = {**user.settings, persona_settings.SETTINGS_KEY: state}
        await session.commit()
    noted = await _personas(client)

    assert nothing == {"chat": None, "edge": None, "notes": None}
    assert dated["chat"] == {
        "slug": "pirate",
        "name": "Talk Like a Pirate Day",
        "tooltip": "Talk Like a Pirate Day: friendly storybook pirate speech.",
        "reason": "Today is Talk Like a Pirate Day.",
    }
    assert dated["edge"] == dated["chat"] and dated["notes"] is None
    assert chosen["chat"]["slug"] == "hobbit"
    assert chosen["chat"]["reason"] == "Set with /persona."
    assert noted["notes"]["slug"] == "pirate"
    assert noted["notes"]["reason"] == "Written on Talk Like a Pirate Day."


@pytest.mark.anyio
async def test_a_persona_change_ends_the_recording_reads_304s(
    client: AsyncClient,
) -> None:
    etag = (await client.get(DETAIL)).headers["etag"]
    unchanged = await client.get(DETAIL, headers={"If-None-Match": etag})
    await _send(client, "/persona pirate")
    changed = await client.get(DETAIL, headers={"If-None-Match": etag})

    assert unchanged.status_code == 304
    assert changed.status_code == 200
    assert changed.json()["personas"]["chat"]["slug"] == "pirate"


@pytest.mark.anyio
async def test_the_settings_page_never_sees_the_persona_state_and_keeps_it(
    client: AsyncClient, session_maker: sessionmaker
) -> None:
    await _send(client, "/persona pirate")

    shown = await client.get("/api/v1/settings")
    saved = await client.post("/api/v1/settings", json={"timezone": "Europe/London"})

    assert shown.status_code == 200 and saved.status_code == 200, saved.text
    assert persona_settings.SETTINGS_KEY not in shown.json()
    assert persona_settings.SETTINGS_KEY not in saved.json()
    assert (await _stored(session_maker))["meetings"] == {"1": "pirate"}
