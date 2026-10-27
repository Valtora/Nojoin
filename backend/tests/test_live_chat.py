"""Current Chat snapshots, overflow recovery and CLI metadata/persistence."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

import backend.worker.tasks as tasks
from backend.processing.llm_backends.base import LLMBackend
from backend.services.live_chat import _recent_text, stream_live_chat
from backend.tests.test_chat_mapped_transcript import (
    RECORDING_ID,
    _insert_note,
    _insert_speaker,
    _insert_transcript,
    _insert_utterance,
)
from backend.tests.test_chat_mapped_transcript import (
    engine as engine,
)
from backend.tests.test_meeting_chat_task import (
    _chat_rows,
    _FakePublisher,
    _wire,
)
from backend.utils.chat_prompt import LiveChatTranscript, build_chat_context
from backend.utils.context_window import ContextWindowExceededError


def test_recent_context_preserves_the_latest_words_of_one_long_line() -> None:
    original = "[00:00] Ana: " + "older wording " * 100 + "Latest café decision."

    shortened = _recent_text(original, 100)

    assert shortened.startswith("[00:00] Ana: ")
    assert shortened.endswith("Latest café decision.")
    assert len(shortened.encode("utf-8")) <= 100
    assert shortened != original


def test_live_snapshot_only_trims_after_overflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = (
        "[00:00] Ana: " + "old wording " * 100 + "\n[01:00] Ana: Latest decision."
    )
    monkeypatch.setattr(
        LLMBackend, "get_mapped_transcript_for_llm", lambda *a, **k: original
    )
    seen: list[LiveChatTranscript] = []

    def ask(transcript: LiveChatTranscript) -> Iterator[str]:
        seen.append(transcript)
        if len(seen) == 1:
            raise ContextWindowExceededError("context window was exhausted")
        yield "Answer."

    chunks = list(stream_live_chat(ask, 1))

    assert seen[0].text == original
    assert not seen[0].trimmed
    assert seen[1].text.endswith("[01:00] Ana: Latest decision.")
    assert len(seen[1].text.encode("utf-8")) <= len(original.encode("utf-8")) // 2
    assert seen[1].text != original
    assert chunks == [
        {"type": "context", "provisional": True, "transcript_trimmed": True},
        "Answer.",
        "\n\n_Based on the transcript so far; wording and speakers may still change. "
        "Only the most recent part of the transcript was included to fit the model’s context._",
    ]
    context = build_chat_context("", seen[1])
    assert "wording and speakers may still change" in context
    assert "older wording was trimmed" in context
    assert "notes above cover the whole meeting" not in context


@pytest.mark.parametrize(
    "error",
    [
        "The input token count (135538) exceeds the maximum number of tokens allowed (131072).",
        "prompt is too long",
    ],
)
def test_live_retry_ignores_empty_initial_chunks_and_recognises_counted_overflow(
    monkeypatch: pytest.MonkeyPatch, error: str
) -> None:
    monkeypatch.setattr(
        LLMBackend,
        "get_mapped_transcript_for_llm",
        lambda *a, **k: "[00:01] Mira: " + "current text " * 20,
    )
    calls: list[LiveChatTranscript] = []

    def ask(transcript: LiveChatTranscript) -> Iterator[str]:
        calls.append(transcript)
        yield ""
        if len(calls) == 1:
            raise RuntimeError(error)
        yield "Answer."

    result = list(stream_live_chat(ask, 1))

    assert len(calls) == 2
    assert result[0] == {
        "type": "context",
        "provisional": True,
        "transcript_trimmed": True,
    }
    assert result[1] == "Answer."
    assert "Only the most recent part" in result[-1]


def test_live_success_appends_a_persistent_disclosure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        LLMBackend, "get_mapped_transcript_for_llm", lambda *a, **k: "Current text"
    )
    chunks = list(stream_live_chat(lambda _: iter(["Answer."]), 1))
    assert chunks == [
        {"type": "context", "provisional": True, "transcript_trimmed": False},
        "Answer.",
        "\n\n_Based on the transcript so far; wording and speakers may still change._",
    ]


def test_live_overflow_never_passes_an_empty_replacement_as_recent_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        LLMBackend, "get_mapped_transcript_for_llm", lambda *a, **k: "[00:00] Mira: Hi"
    )
    seen: list[LiveChatTranscript] = []

    def ask(transcript: LiveChatTranscript) -> Iterator[str]:
        seen.append(transcript)
        if len(seen) == 1:
            raise ContextWindowExceededError("context window was exhausted")
        yield "Answer without the transcript."

    with pytest.raises(ContextWindowExceededError):
        list(stream_live_chat(ask, 1))
    assert len(seen) == 1


def test_live_snapshot_does_not_retry_after_a_partial_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        LLMBackend, "get_mapped_transcript_for_llm", lambda *a, **k: "Full text"
    )
    calls: list[LiveChatTranscript] = []

    def ask(transcript: LiveChatTranscript) -> Iterator[str]:
        calls.append(transcript)
        yield "Partial answer"
        raise ContextWindowExceededError("context window was exhausted")

    stream = stream_live_chat(ask, 1)
    assert next(stream)["transcript_trimmed"] is False
    assert next(stream) == "Partial answer"
    with pytest.raises(ContextWindowExceededError):
        next(stream)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("rate limit"),
        ContextWindowExceededError("answer too long", answer_incomplete=True),
    ],
)
def test_live_snapshot_does_not_trim_for_unrelated_or_output_errors(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    monkeypatch.setattr(
        LLMBackend, "get_mapped_transcript_for_llm", lambda *a, **k: "Full text"
    )
    calls: list[LiveChatTranscript] = []

    def ask(transcript: LiveChatTranscript) -> Iterator[str]:
        calls.append(transcript)
        raise failure
        yield "unreachable"

    with pytest.raises(type(failure)):
        list(stream_live_chat(ask, 1))
    assert len(calls) == 1


def test_cli_live_overflow_uses_current_names_notes_and_persists_once(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE chat_messages (id INTEGER PRIMARY KEY, created_at DATETIME NOT NULL, "
                "updated_at DATETIME NOT NULL, recording_id INTEGER NOT NULL, user_id INTEGER NOT NULL, "
                "role VARCHAR NOT NULL, content TEXT NOT NULL)"
            )
        )
    _insert_transcript(engine, [])
    _insert_speaker(engine, "SPEAKER_00", local_name="Mira")
    _insert_utterance(engine, 1, (0, 1000), "SPEAKER_00", "older text " * 100)
    _insert_utterance(
        engine,
        2,
        (2000, 3000),
        "SPEAKER_00",
        "ongoing wording " * 100 + "Latest decision.",
    )
    _insert_note(engine, "point", "Current user note.", anchor_ms=2500)
    with engine.begin() as connection:
        connection.execute(text("UPDATE recordings SET status='PROCESSING' WHERE id=1"))

    class Backend(LLMBackend):
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def ask_question_streaming(self, **kwargs: Any) -> Iterator[str]:
            transcript = self._chat_transcript(
                kwargs["diarized_transcript"], RECORDING_ID
            )
            self.prompts.append(build_chat_context(kwargs["meeting_notes"], transcript))
            if len(self.prompts) == 1:
                raise RuntimeError("prompt is too long")
            yield "Answer."

    backend = Backend()
    _wire(monkeypatch, engine, backend)
    tasks.meeting_chat_task.run(RECORDING_ID, "What is the latest decision?", [])

    events = _FakePublisher.instances[0].events
    footer = (
        "\n\n_Based on the transcript so far; wording and speakers may still change. "
        "Only the most recent part of the transcript was included to fit the model’s context._"
    )
    assert events == [
        ("context", {"provisional": True, "transcript_trimmed": True}),
        ("tok", "Answer."),
        ("tok", footer),
        ("done", None),
        ("close", None),
    ]
    assert "[00:02] Mira:" in backend.prompts[1]
    assert "Latest decision." in backend.prompts[1]
    assert "Current user note." in backend.prompts[1]
    assert "older text" not in backend.prompts[1]
    rows = _chat_rows(engine)
    assert [(row.role, row.content) for row in rows] == [
        ("assistant", "Answer." + footer)
    ]
