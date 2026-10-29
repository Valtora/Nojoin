"""Asking the AI provider to name a live speaker, from the live naming check.

The real naming check runs against the live lines of ``live_naming_fixtures``
(``LIVE_PROVISIONAL_1`` speaks 16.5 s), with the provider configured in the
owner's settings. The ask it queues runs as the worker would run it, and only
the LLM backend, Celery dispatch and Redis are replaced.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Session

import backend.worker.tasks as tasks_module
from backend.core.single_flight import KEY_PREFIX
from backend.models.transcript import Transcript
from backend.tests.live_naming_fixtures import ONE, TWO
from backend.tests.sqlite_schemas import (
    DOCUMENTS_SCHEMA,
    RECORDING_AUDIO_CHUNKS_SCHEMA,
    RECORDING_TAGS_SCHEMA,
    TAGS_SCHEMA,
    USERS_SCHEMA,
)
from backend.tests.test_live_speaker_naming_api import (
    NOW,
    REC,
    _name,
    _seed,
)
from backend.tests.test_live_speaker_naming_api import (
    client as client,
)
from backend.tests.test_live_speaker_naming_api import (
    session_maker as session_maker,
)
from backend.utils.speaker_name_suggestions import (
    SpeakerInferenceResult,
    SpeakerInferenceSuggestion,
    load_transcript_speaker_suggestions,
    parse_speaker_inference_response,
)
from backend.utils.speaker_names.live_suggestions import (
    mark_live_asks,
    store_live_llm_suggestions,
)
from backend.worker.tasks import live_naming_stage

pytestmark = pytest.mark.anyio

AI_SETTINGS = {
    "llm_provider": "openai",
    "openai_api_key": "test-key",
    "openai_model": "test-model",
}


class FakeLLM:
    """The provider: answers with ``names`` (label -> name), parsed as #683 does."""

    def __init__(self, dispatched: list[Any]) -> None:
        self.names: dict[str, str] = {}
        self.asks: list[list[str]] = []
        self.dispatched = dispatched
        self.ran = 0

    def infer_speaker_suggestions(self, transcript: str, **kwargs: Any) -> Any:
        labels = list(kwargs["eligible_labels"])
        self.asks.append(labels)
        reply = {
            "suggestions": [
                {
                    "diarization_label": label,
                    "suggested_name": name,
                    "confidence": 0.9,
                    "evidence_spans": [{"quote": "live line", "reason": "context"}],
                }
                for label, name in self.names.items()
                if label in labels
            ]
        }
        return parse_speaker_inference_response(
            json.dumps(reply), allowed_labels=labels
        )


@pytest.fixture
def llm(
    monkeypatch: pytest.MonkeyPatch,
    fake_single_flight: Any,
    stub_celery_dispatch: list[Any],
) -> FakeLLM:
    fake = FakeLLM(stub_celery_dispatch)
    monkeypatch.setattr(tasks_module, "_llm_backend_from_config", lambda config: fake)
    return fake


async def _seed_owner(session_maker: Any, settings: dict[str, Any]) -> None:
    await _seed(session_maker)
    async with session_maker() as db:
        await db.execute(text(USERS_SCHEMA))
        await db.execute(
            text(
                "INSERT INTO users (id, created_at, updated_at, username, "
                "hashed_password, is_active, is_superuser, force_password_change, "
                "role, token_version, settings, has_seen_demo_recording) VALUES "
                "(1, :now, :now, 'alice', 'x', 1, 1, 0, 'owner', 0, :settings, 1)"
            ),
            {"now": NOW, "settings": json.dumps(settings)},
        )
        await db.commit()


async def _sql(session_maker: Any, statement: str, **params: Any) -> None:
    async with session_maker() as db:
        await db.execute(text(statement), params)
        await db.commit()


async def _seed_settings(session_maker: Any, settings: dict[str, Any]) -> None:
    await _sql(
        session_maker,
        "UPDATE users SET settings = :settings",
        settings=json.dumps(settings),
    )


async def _say(session_maker: Any, label: str, words: str) -> None:
    await _sql(
        session_maker,
        "UPDATE transcript_utterances SET text = :words WHERE speaker_label = :label",
        words=words,
        label=label,
    )


async def _talk_until(session_maker: Any, end_ms: int) -> None:
    """Stretch the label's 9.5 s turn; its speech is then 6 s + the turn."""
    await _sql(
        session_maker,
        "UPDATE transcript_utterances SET end_ms = :end WHERE start_ms = 9500",
        end=end_ms,
    )


async def _run_task(session_maker: Any, task: Any) -> None:
    """Run a task on the worker's own kind of session."""

    def run(session: Session) -> None:
        original = task._session
        task._session = session
        try:
            task.run(1)
        finally:
            task._session = original

    engine = session_maker.kw["bind"]
    async with AsyncSession(engine, sync_session_class=Session) as db:
        await db.run_sync(run)


async def _refresh(session_maker: Any, llm: FakeLLM) -> None:
    """One naming check, then any ask it queued."""
    await _run_task(session_maker, live_naming_stage.refresh_live_speaker_names_task)
    for name, _, _ in llm.dispatched[llm.ran :]:
        llm.ran += 1
        assert name == live_naming_stage.LIVE_NAMING_ASK_TASK
        await _run_task(session_maker, live_naming_stage.ask_live_speaker_names_task)


async def _read(client: AsyncClient) -> dict[str, Any]:
    response = await client.get(f"/api/v1/transcripts/{REC}/utterances")
    assert response.status_code == 200, response.text
    return response.json()


async def _review(session_maker: Any, label: str = ONE) -> list[dict[str, Any]]:
    async with session_maker() as db:
        return [
            item
            for item in load_transcript_speaker_suggestions(await db.get(Transcript, 1))
            if item["diarization_label"] == label and "status" in item
        ]


async def test_a_self_introduction_prompts_one_ask(
    client: AsyncClient, session_maker: Any, llm: FakeLLM
) -> None:
    await _seed_owner(session_maker, AI_SETTINGS)
    await _say(session_maker, ONE, "I'm Alex.")

    await _refresh(session_maker, llm)
    await _refresh(session_maker, llm)

    assert llm.asks == [[ONE]]
    read = await _read(client)
    assert read["live_speaker_suggestions"] == {ONE: "Alex"}
    assert read["live_speaker_suggestion_sources"] == {ONE: "heuristic"}


async def test_little_speech_and_no_introduction_prompts_no_ask(
    session_maker: Any, llm: FakeLLM
) -> None:
    await _seed_owner(session_maker, AI_SETTINGS)

    await _refresh(session_maker, llm)

    assert llm.asks == []


async def test_each_further_minute_of_speech_prompts_one_ask(
    session_maker: Any, llm: FakeLLM
) -> None:
    await _seed_owner(session_maker, AI_SETTINGS)
    await _talk_until(session_maker, 64_000)  # 60.5 s in all

    await _refresh(session_maker, llm)
    await _refresh(session_maker, llm)
    await _talk_until(session_maker, 110_000)  # 46 s more: not yet
    await _refresh(session_maker, llm)
    await _talk_until(session_maker, 125_000)  # 61 s more
    await _refresh(session_maker, llm)

    assert llm.asks == [[ONE], [ONE]]


@pytest.mark.parametrize(
    "answer", [None, "Industrial Designer", "industrial designer", "The Manager"]
)
async def test_an_unsure_or_role_answer_is_no_proposal(
    client: AsyncClient, session_maker: Any, llm: FakeLLM, answer: str | None
) -> None:
    await _seed_owner(session_maker, AI_SETTINGS)
    await _say(session_maker, ONE, "I'm Alex.")
    if answer is not None:
        llm.names = {ONE: answer}

    await _refresh(session_maker, llm)

    assert llm.asks == [[ONE]]
    read = await _read(client)
    # The rule's own proposal stands; the answer neither replaced nor retracted it.
    assert read["live_speaker_suggestions"] == {ONE: "Alex"}
    assert read["live_speaker_suggestion_sources"] == {ONE: "heuristic"}


async def test_an_ai_name_shows_as_suggested_by_ai_and_replaces_the_rules(
    client: AsyncClient, session_maker: Any, llm: FakeLLM
) -> None:
    await _seed_owner(session_maker, AI_SETTINGS)
    await _say(session_maker, ONE, "I'm Alex.")
    llm.names = {ONE: "Priya Raman"}

    await _refresh(session_maker, llm)
    # The rule still hears "Alex"; it must not offer it beside the AI's name.
    await _refresh(session_maker, llm)

    read = await _read(client)
    assert read["live_speaker_suggestions"] == {ONE: "Priya Raman"}
    assert read["live_speaker_suggestion_sources"] == {ONE: "llm"}
    assert [item["status"] for item in await _review(session_maker)] == [
        "superseded",
        "pending",
    ]


async def test_a_repeated_answer_shows_once_and_a_newer_one_replaces_it(
    client: AsyncClient, session_maker: Any, llm: FakeLLM
) -> None:
    await _seed_owner(session_maker, AI_SETTINGS)
    llm.names = {ONE: "Priya"}
    await _talk_until(session_maker, 64_000)
    await _refresh(session_maker, llm)
    await _talk_until(session_maker, 125_000)
    await _refresh(session_maker, llm)
    assert len(await _review(session_maker)) == 1

    llm.names = {ONE: "Priya Raman"}
    await _talk_until(session_maker, 186_000)
    await _refresh(session_maker, llm)
    assert (await _read(client))["live_speaker_suggestions"] == {ONE: "Priya Raman"}

    dismissed = await client.post(
        f"/api/v1/speakers/recordings/{REC}/live/{ONE}/suggestions/reject"
    )
    assert dismissed.status_code == 200, dismissed.text
    # The older answer was closed, so nothing resurfaces once the newer goes.
    assert (await _read(client))["live_speaker_suggestions"] == {}
    assert len(llm.asks) == 3


NO_PROVIDER = {"llm_provider": "openai"}


@pytest.mark.parametrize("answered", ["named", "dismissed"])
async def test_a_label_the_user_answered_is_never_asked_about(
    client: AsyncClient, session_maker: Any, llm: FakeLLM, answered: str
) -> None:
    await _seed_owner(session_maker, NO_PROVIDER)
    await _say(session_maker, ONE, "I'm Alex.")
    await _refresh(session_maker, llm)  # No provider: the rule proposes alone.
    if answered == "named":
        assert (await _name(client, ONE, "Dana")).status_code == 200
    else:
        response = await client.post(
            f"/api/v1/speakers/recordings/{REC}/live/{ONE}/suggestions/reject"
        )
        assert response.status_code == 200, response.text
    await _seed_settings(session_maker, AI_SETTINGS)
    await _talk_until(session_maker, 200_000)

    await _refresh(session_maker, llm)

    assert llm.asks == []


async def test_no_ask_without_a_provider(
    client: AsyncClient, session_maker: Any, llm: FakeLLM
) -> None:
    await _seed_owner(session_maker, NO_PROVIDER)
    await _say(session_maker, ONE, "I'm Alex.")
    await _talk_until(session_maker, 200_000)

    await _refresh(session_maker, llm)

    assert llm.asks == []
    assert llm.dispatched == []
    assert (await _read(client))["live_speaker_suggestions"] == {ONE: "Alex"}


async def test_an_ai_name_arrives_with_meeting_edge_off(
    client: AsyncClient, session_maker: Any, llm: FakeLLM
) -> None:
    await _seed_owner(session_maker, {**AI_SETTINGS, "enable_meeting_edge": False})
    llm.names = {ONE: "Priya Raman"}
    await _talk_until(session_maker, 64_000)

    await _refresh(session_maker, llm)

    assert (await _read(client))["live_speaker_suggestions"] == {ONE: "Priya Raman"}


async def test_no_ask_while_another_is_in_flight(
    session_maker: Any, llm: FakeLLM, fake_single_flight: Any
) -> None:
    await _seed_owner(session_maker, AI_SETTINGS)
    await _say(session_maker, TWO, "My name is Blair.")
    in_flight = f"{KEY_PREFIX}live-naming-ask:1"
    fake_single_flight.keys[in_flight] = "claimed"

    await _refresh(session_maker, llm)
    assert llm.dispatched == []

    # Once that ask ends, the next check asks about the introduction it missed.
    del fake_single_flight.keys[in_flight]
    await _refresh(session_maker, llm)
    assert llm.asks == [[TWO]]
    assert in_flight not in fake_single_flight.keys


async def test_the_finished_transcript_read_leaves_out_ask_records(
    client: AsyncClient, session_maker: Any, llm: FakeLLM
) -> None:
    await _seed_owner(session_maker, AI_SETTINGS)
    await _talk_until(session_maker, 64_000)
    await _refresh(session_maker, llm)
    assert llm.asks == [[ONE]]
    async with session_maker() as db:
        # The further tables the recording detail read joins.
        for schema in (
            DOCUMENTS_SCHEMA,
            RECORDING_AUDIO_CHUNKS_SCHEMA,
            TAGS_SCHEMA,
            RECORDING_TAGS_SCHEMA,
        ):
            await db.execute(text(schema))
        await db.execute(text("UPDATE recordings SET status = 'PROCESSED'"))
        await db.commit()

    response = await client.get(f"/api/v1/recordings/{REC}")

    assert response.status_code == 200, response.text
    assert response.json()["transcript"]["speaker_name_suggestions"] == []


async def test_an_answer_to_an_older_ask_leaves_a_newer_introduction_due(
    client: AsyncClient, session_maker: Any, llm: FakeLLM, fake_single_flight: Any
) -> None:
    await _seed_owner(session_maker, AI_SETTINGS)
    await _say(session_maker, ONE, "I'm Alex.")
    await _run_task(session_maker, live_naming_stage.refresh_live_speaker_names_task)
    engine = session_maker.kw["bind"]
    async with AsyncSession(engine, sync_session_class=Session) as db:
        # The queued ask starts: it is recorded, then the provider is called.
        marked = await db.run_sync(lambda sync: mark_live_asks(sync, 1))
        assert marked is not None and marked[0] == [ONE]
        # While the provider answers, the speaker corrects the introduction.
        await _say(session_maker, ONE, "I'm Priya.")
        await _run_task(
            session_maker, live_naming_stage.refresh_live_speaker_names_task
        )
        answer = SpeakerInferenceResult((SpeakerInferenceSuggestion(ONE, "Alex", 0.9),))
        await db.run_sync(
            lambda sync: store_live_llm_suggestions(sync, 1, answer, "test")
        )

    read = await _read(client)
    assert read["live_speaker_suggestions"] == {ONE: "Priya"}
    assert read["live_speaker_suggestion_sources"] == {ONE: "heuristic"}

    # Once that ask ends, the next check asks about the newer introduction.
    del fake_single_flight.keys[f"{KEY_PREFIX}live-naming-ask:1"]
    llm.ran = len(llm.dispatched)
    await _refresh(session_maker, llm)
    assert llm.asks == [[ONE]]
