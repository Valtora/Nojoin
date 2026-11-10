"""Asking the AI provider to name a live speaker, from the live naming check.

The real naming check runs against the live lines of ``live_naming_fixtures``
(``LIVE_PROVISIONAL_1`` speaks 16.5 s, ``LIVE_PROVISIONAL_2`` 4 s), with the
provider configured in the owner's settings. The ask it queues runs as the
worker would run it, and only the LLM backend, Celery dispatch and Redis are
replaced.
"""

from __future__ import annotations

import json
import logging
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
    SpeakerSuggestionContractError,
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
        # Raised instead of answering, as a provider raises a failed call.
        self.error: Exception | None = None
        # The answer's evidence spans, each quoted with these keys besides.
        self.spans: list[dict[str, Any]] = [{}]

    def infer_speaker_suggestions(self, transcript: str, **kwargs: Any) -> Any:
        labels = list(kwargs["eligible_labels"])
        self.asks.append(labels)
        if self.error is not None:
            raise self.error
        reply = {
            "suggestions": [
                {
                    "diarization_label": label,
                    "suggested_name": name,
                    "confidence": 0.9,
                    "evidence_spans": [
                        {"quote": "live line", "reason": "context", **span}
                        for span in self.spans
                    ],
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


async def _speak_for(session_maker: Any, seconds: float) -> None:
    """Stretch the 9.5 s turn until the label has spoken ``seconds`` in all."""
    await _talk_until(session_maker, round((seconds - 6 + 9.5) * 1000))


async def _ask_records(session_maker: Any) -> dict[str, dict[str, Any]]:
    async with session_maker() as db:
        return {
            item["diarization_label"]: item
            for item in load_transcript_speaker_suggestions(await db.get(Transcript, 1))
            if item.get("origin") == "live_llm_ask"
        }


async def _set_ask_records(session_maker: Any, records: list[dict[str, Any]]) -> None:
    """Replace the ask records, as earlier asks would have left them."""
    async with session_maker() as db:
        transcript = await db.get(Transcript, 1)
        transcript.speaker_name_suggestions = [
            item
            for item in load_transcript_speaker_suggestions(transcript)
            if item.get("origin") != "live_llm_ask"
        ] + records
        db.add(transcript)
        await db.commit()


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


async def test_the_speech_between_asks_doubles_up_to_eight_minutes(
    session_maker: Any, llm: FakeLLM
) -> None:
    await _seed_owner(session_maker, AI_SETTINGS)
    asked_at = 0
    for asks, gap in enumerate((60, 120, 240, 480, 480)):
        await _speak_for(session_maker, asked_at + gap - 1)
        await _refresh(session_maker, llm)
        assert len(llm.asks) == asks, f"asked before {gap} s more"
        asked_at += gap + 1
        await _speak_for(session_maker, asked_at)
        await _refresh(session_maker, llm)
        assert llm.asks == [[ONE]] * (asks + 1), f"not asked after {gap} s more"

    assert (await _ask_records(session_maker))[ONE]["asks"] == 5


async def test_an_ask_recorded_before_asks_were_counted_counts_as_one(
    session_maker: Any, llm: FakeLLM
) -> None:
    await _seed_owner(session_maker, AI_SETTINGS)
    await _set_ask_records(
        session_maker,
        [
            {
                "origin": "live_llm_ask",
                "diarization_label": ONE,
                "speech_seconds": 61.0,
                "asked_at": "2026-01-01T09:00:00+00:00",
            }
        ],
    )

    await _speak_for(session_maker, 61 + 119)
    await _refresh(session_maker, llm)
    assert llm.asks == []
    await _speak_for(session_maker, 61 + 121)
    await _refresh(session_maker, llm)

    assert llm.asks == [[ONE]]
    assert (await _ask_records(session_maker))[ONE]["asks"] == 2


async def test_a_new_introduction_asks_at_once_however_often_asked_before(
    session_maker: Any, llm: FakeLLM
) -> None:
    await _seed_owner(session_maker, AI_SETTINGS)
    await _set_ask_records(
        session_maker,
        [
            {
                "origin": "live_llm_ask",
                "diarization_label": ONE,
                "speech_seconds": 16.5,
                "asked_at": "2026-01-01T09:00:00+00:00",
                "asks": 7,
            }
        ],
    )
    await _say(session_maker, ONE, "I'm Alex.")

    await _refresh(session_maker, llm)

    assert llm.asks == [[ONE]]
    assert (await _ask_records(session_maker))[ONE]["asks"] == 8


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


async def test_a_pending_ai_name_is_not_asked_about_again(
    client: AsyncClient, session_maker: Any, llm: FakeLLM
) -> None:
    await _seed_owner(session_maker, AI_SETTINGS)
    llm.names = {ONE: "Alex"}
    await _speak_for(session_maker, 61)
    await _refresh(session_maker, llm)
    assert llm.asks == [[ONE]]

    llm.names = {ONE: "Blair"}
    for speech in (200, 1000, 3000):
        await _speak_for(session_maker, speech)
        await _refresh(session_maker, llm)

    assert llm.asks == [[ONE]]
    read = await _read(client)
    assert read["live_speaker_suggestions"] == {ONE: "Alex"}
    assert read["live_speaker_suggestion_sources"] == {ONE: "llm"}


async def test_a_late_answer_leaves_a_pending_ai_name(
    client: AsyncClient, session_maker: Any, llm: FakeLLM
) -> None:
    await _seed_owner(session_maker, AI_SETTINGS)
    llm.names = {ONE: "Alex"}
    await _speak_for(session_maker, 61)
    await _refresh(session_maker, llm)
    engine = session_maker.kw["bind"]
    # An answer to an ask already in flight when that name was offered.
    answer = SpeakerInferenceResult((SpeakerInferenceSuggestion(ONE, "Blair", 0.9),))
    async with AsyncSession(engine, sync_session_class=Session) as db:
        added = await db.run_sync(
            lambda sync: store_live_llm_suggestions(sync, 1, answer, "test")
        )

    assert added == 0
    assert (await _read(client))["live_speaker_suggestions"] == {ONE: "Alex"}
    assert [item["status"] for item in await _review(session_maker)] == ["pending"]


async def test_an_introduction_already_asked_about_is_not_asked_about_again(
    client: AsyncClient, session_maker: Any, llm: FakeLLM
) -> None:
    await _seed_owner(session_maker, AI_SETTINGS)
    await _say(session_maker, ONE, "I'm Alex.")
    await _refresh(session_maker, llm)
    assert llm.asks == [[ONE]]

    for speech in (200, 1000, 3000):
        await _speak_for(session_maker, speech)
        await _refresh(session_maker, llm)

    assert llm.asks == [[ONE]]
    assert (await _read(client))["live_speaker_suggestions"] == {ONE: "Alex"}


async def test_a_retired_name_leaves_the_label_due_after_its_next_window(
    client: AsyncClient, session_maker: Any, llm: FakeLLM
) -> None:
    await _seed_owner(session_maker, AI_SETTINGS)
    await _say(session_maker, ONE, "I'm Alex.")
    await _refresh(session_maker, llm)
    await _speak_for(session_maker, 500)
    await _refresh(session_maker, llm)
    assert llm.asks == [[ONE]]

    # The words change, so the rule's name is retired with nobody deciding.
    await _say(session_maker, ONE, "Thanks, carry on.")
    await _refresh(session_maker, llm)
    assert (await _read(client))["live_speaker_suggestions"] == {}
    assert llm.asks == [[ONE]]

    # One ask so far: the next needs 120 s more speech, counted from then.
    await _speak_for(session_maker, 500 + 119)
    await _refresh(session_maker, llm)
    assert llm.asks == [[ONE]]
    await _speak_for(session_maker, 500 + 121)
    await _refresh(session_maker, llm)
    assert llm.asks == [[ONE], [ONE]]


async def test_a_merge_carries_the_merged_labels_asks_to_the_label_joined(
    client: AsyncClient, session_maker: Any, llm: FakeLLM
) -> None:
    await _seed_owner(session_maker, AI_SETTINGS)
    await _speak_for(session_maker, 61)
    await _refresh(session_maker, llm)
    assert llm.asks == [[ONE]]

    merged = await client.post(
        f"/api/v1/speakers/recordings/{REC}/live/{ONE}/merge",
        json={"into_label": TWO},
    )
    assert merged.status_code == 200, merged.text
    await _refresh(session_maker, llm)

    # The group's 65 s are not a fresh label's first minute.
    assert llm.asks == [[ONE]]
    records = await _ask_records(session_maker)
    assert set(records) == {TWO}
    assert (records[TWO]["asks"], records[TWO]["speech_seconds"]) == (1, 61.0)

    # It is asked after the label's next window: 120 s more of the group's
    # speech, which counts the joined label's 4 s.
    await _speak_for(session_maker, 61 + 119 - 4)
    await _refresh(session_maker, llm)
    assert llm.asks == [[ONE]]
    await _speak_for(session_maker, 61 + 121 - 4)
    await _refresh(session_maker, llm)
    assert llm.asks == [[ONE], [TWO]]


async def test_a_merge_of_two_asked_labels_keeps_the_longer_wait(
    client: AsyncClient, session_maker: Any, llm: FakeLLM
) -> None:
    await _seed_owner(session_maker, AI_SETTINGS)
    await _speak_for(session_maker, 61)
    await _set_ask_records(
        session_maker,
        [
            {
                "origin": "live_llm_ask",
                "diarization_label": ONE,
                "speech_seconds": 61.0,
                "asked_at": "2026-01-01T09:00:00+00:00",
                "asks": 1,
            },
            {
                "origin": "live_llm_ask",
                "diarization_label": TWO,
                "speech_seconds": 4.0,
                "asked_at": "2026-01-01T09:05:00+00:00",
                "asks": 3,
            },
        ],
    )

    merged = await client.post(
        f"/api/v1/speakers/recordings/{REC}/live/{ONE}/merge",
        json={"into_label": TWO},
    )
    assert merged.status_code == 200, merged.text

    records = await _ask_records(session_maker)
    assert set(records) == {TWO}
    assert {
        key: records[TWO][key] for key in ("asks", "speech_seconds", "asked_at")
    } == {"asks": 3, "speech_seconds": 65.0, "asked_at": "2026-01-01T09:05:00+00:00"}


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


@pytest.mark.parametrize("answer", ["Accountant", "Software Architect"])
async def test_a_job_title_is_no_ai_name(
    client: AsyncClient, session_maker: Any, llm: FakeLLM, answer: str
) -> None:
    await _seed_owner(session_maker, AI_SETTINGS)
    await _talk_until(session_maker, 64_000)
    llm.names = {ONE: answer}

    await _refresh(session_maker, llm)

    assert llm.asks == [[ONE]]
    assert (await _read(client))["live_speaker_suggestions"] == {}


@pytest.mark.parametrize("answer", ["李明", "محمد", "Ana Maria de Souza"])
async def test_a_name_in_any_script_or_length_is_an_ai_name(
    client: AsyncClient, session_maker: Any, llm: FakeLLM, answer: str
) -> None:
    await _seed_owner(session_maker, AI_SETTINGS)
    await _talk_until(session_maker, 64_000)
    llm.names = {ONE: answer}

    await _refresh(session_maker, llm)

    read = await _read(client)
    assert read["live_speaker_suggestions"] == {ONE: answer}
    assert read["live_speaker_suggestion_sources"] == {ONE: "llm"}


async def test_with_live_suggestions_off_a_check_proposes_and_asks_nothing(
    client: AsyncClient, session_maker: Any, llm: FakeLLM
) -> None:
    await _seed_owner(
        session_maker, {**AI_SETTINGS, "suggest_live_speaker_names": False}
    )
    await _say(session_maker, ONE, "I'm Alex.")
    await _talk_until(session_maker, 64_000)

    await _refresh(session_maker, llm)

    assert llm.dispatched == []
    assert (await _read(client))["live_speaker_suggestions"] == {}


async def test_an_ask_queued_before_suggestions_were_turned_off_asks_nothing(
    client: AsyncClient, session_maker: Any, llm: FakeLLM
) -> None:
    await _seed_owner(session_maker, AI_SETTINGS)
    llm.names = {ONE: "Priya Raman"}
    await _talk_until(session_maker, 64_000)
    await _run_task(session_maker, live_naming_stage.refresh_live_speaker_names_task)
    assert [name for name, _, _ in llm.dispatched] == [
        live_naming_stage.LIVE_NAMING_ASK_TASK
    ]

    await _sql(
        session_maker,
        "UPDATE users SET settings = :settings",
        settings=json.dumps({**AI_SETTINGS, "suggest_live_speaker_names": False}),
    )
    await _run_task(session_maker, live_naming_stage.ask_live_speaker_names_task)

    assert llm.asks == []
    assert (await _read(client))["live_speaker_suggestions"] == {}


async def _say_at(session_maker: Any, start_ms: int, words: str) -> None:
    await _sql(
        session_maker,
        "UPDATE transcript_utterances SET text = :words WHERE start_ms = :start",
        words=words,
        start=start_ms,
    )


async def test_a_self_introduction_is_read_with_where_it_was_said(
    client: AsyncClient, session_maker: Any, llm: FakeLLM
) -> None:
    await _seed_owner(session_maker, NO_PROVIDER)
    await _say_at(session_maker, 9500, "Hello, I'm Alex.")

    await _refresh(session_maker, llm)

    read = await _read(client)
    assert read["live_speaker_suggestions"] == {ONE: "Alex"}
    assert read["live_speaker_suggestion_starts"] == {ONE: 9.5}


@pytest.mark.parametrize(
    ("spans", "starts"),
    [
        ([{}], {}),
        ([{"start_seconds": 9.5, "end_seconds": 20.0}], {ONE: 9.5}),
        (
            [
                {"start_seconds": 0.0, "end_seconds": 4.0},
                {"start_seconds": 9.5, "end_seconds": 20.0},
            ],
            {},
        ),
    ],
    ids=["untimed", "one-timed-span", "two-spans"],
)
async def test_an_ai_name_is_placed_only_by_a_single_timed_span(
    client: AsyncClient,
    session_maker: Any,
    llm: FakeLLM,
    spans: list[dict[str, Any]],
    starts: dict[str, float],
) -> None:
    await _seed_owner(session_maker, AI_SETTINGS)
    await _talk_until(session_maker, 64_000)
    llm.names = {ONE: "Priya Raman"}
    llm.spans = spans

    await _refresh(session_maker, llm)

    read = await _read(client)
    assert read["live_speaker_suggestions"] == {ONE: "Priya Raman"}
    assert read["live_speaker_suggestion_starts"] == starts


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError(
            "Ollama API error (speaker suggestions): Read timed out. (read timeout=90)"
        ),
        # A CLI subscription's unreadable answer.
        SpeakerSuggestionContractError("timed out waiting for a readable answer"),
    ],
    ids=["provider-timeout", "unreadable-answer"],
)
async def test_a_failed_ask_gives_up_quietly_and_counts_as_an_ask(
    client: AsyncClient,
    session_maker: Any,
    llm: FakeLLM,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    await _seed_owner(session_maker, AI_SETTINGS)
    await _speak_for(session_maker, 61)
    llm.error = error

    with caplog.at_level(logging.INFO, logger=live_naming_stage.__name__):
        await _refresh(session_maker, llm)

    assert llm.asks == [[ONE]]
    assert [
        (record.levelname, "timed out" in record.getMessage())
        for record in caplog.records
        if record.name == live_naming_stage.__name__
    ] == [("WARNING", True)]
    assert (await _read(client))["live_speaker_suggestions"] == {}

    # No retry within the next window, however many checks run: the failed
    # ask counts, so that window is 120 s.
    llm.error = None
    llm.names = {ONE: "Priya Raman"}
    await _speak_for(session_maker, 61 + 119)
    await _refresh(session_maker, llm)
    await _refresh(session_maker, llm)
    assert llm.asks == [[ONE]]

    # Past it, the label is asked again: the failed ask released its slot.
    await _speak_for(session_maker, 61 + 121)
    await _refresh(session_maker, llm)
    assert llm.asks == [[ONE], [ONE]]
    assert (await _read(client))["live_speaker_suggestions"] == {ONE: "Priya Raman"}
