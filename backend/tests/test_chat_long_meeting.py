"""Meeting Chat on a meeting too long for the provider's window.

When every configured provider refuses the whole transcript before any text
has streamed, chat asks once more with the passages of this meeting most
relevant to the question. The handler is driven directly, as in
test_chat_cli_relay_api; the pgvector search cannot run on SQLite, so the
excerpt lookup is replaced and its parsing tested on its own.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from backend.api.v1.endpoints.transcripts import routes_chat
from backend.api.v1.endpoints.transcripts.helpers import ChatRequest
from backend.celery_app import celery_app
from backend.processing.llm_backends.ollama import OllamaLLMBackend
from backend.tests.test_chat_cli_relay_api import (
    _fake_async,
    _make_session_maker,
    _rag_and_dispatch_stubs,
)
from backend.tests.test_ollama_context_window import (
    FITS,
    _answer,
    _backend,
    _FakeOllama,
)
from backend.utils.chat_prompt import TranscriptExcerpts
from backend.utils.context_window import ContextWindowExceededError
from backend.utils.llm_config import ResolvedLLMConfig

EXCERPTS = TranscriptExcerpts(ranges=((0.0, 30.0),))


class _Backend:
    """Streams ``answers`` per call; an exception in place of text is raised."""

    def __init__(self, *calls):
        self.calls = list(calls)
        self.transcripts: list = []

    def ask_question_streaming(self, **kwargs):
        self.transcripts.append(kwargs["diarized_transcript"])
        for item in self.calls.pop(0):
            if isinstance(item, Exception):
                raise item
            yield item


def _refusal() -> ContextWindowExceededError:
    return ContextWindowExceededError(
        "The Ollama context window was exhausted: Ollama refused the request "
        "because the window is 8,192 tokens and the prompt is 41,000 tokens.",
        prompt_tokens=41_000,
        window=8192,
    )


def _chat(monkeypatch, backend: _Backend) -> tuple[list[str], list]:
    looked_up: list = []

    async def _excerpts(recording_id, query_embedding):
        looked_up.append((recording_id, query_embedding))
        return EXCERPTS

    async def _run():
        engine, maker = await _make_session_maker()
        ollama = ResolvedLLMConfig(
            provider="ollama",
            api_key=None,
            model="qwen3",
            api_url="http://x",
            merged_config={},
        )
        monkeypatch.setattr(
            routes_chat, "resolve_llm_config_async", lambda *a, **k: _fake_async(ollama)
        )
        monkeypatch.setattr(celery_app, "send_task", _rag_and_dispatch_stubs([]))
        monkeypatch.setattr(routes_chat, "async_session_maker", maker)
        monkeypatch.setattr(routes_chat, "_transcript_excerpts", _excerpts)
        monkeypatch.setattr(
            "backend.api.v1.endpoints.transcripts.get_llm_backend_with_secondary",
            lambda cfg: backend,
        )
        user = SimpleNamespace(id=1, settings={})
        async with maker() as session:
            response = await routes_chat.chat_with_meeting(
                "p1",
                ChatRequest(message="What was decided?"),
                db=session,
                current_user=user,
            )
            frames = [chunk async for chunk in response.body_iterator]
        await engine.dispose()
        return frames

    return asyncio.run(_run()), looked_up


def test_a_meeting_refused_whole_is_answered_from_excerpts(monkeypatch):
    backend = _Backend([_refusal()], ["From the excerpts."])

    frames, looked_up = _chat(monkeypatch, backend)

    assert frames == ['data: {"token": "From the excerpts."}\n\n', "data: [DONE]\n\n"]
    assert backend.transcripts == [None, EXCERPTS]
    # The embedding failed in this harness, so the lookup was given none.
    assert looked_up == [(1, None)]


def test_excerpts_refused_too_report_the_context_window(monkeypatch):
    backend = _Backend([_refusal()], [_refusal()])

    frames, _ = _chat(monkeypatch, backend)

    assert len(frames) == 1
    assert "context window was exhausted" in frames[0]
    assert "secondary provider" in frames[0]
    assert backend.transcripts == [None, EXCERPTS]


def test_a_refusal_after_text_has_streamed_is_not_retried(monkeypatch):
    backend = _Backend(["Partial", _refusal()])

    frames, looked_up = _chat(monkeypatch, backend)

    assert frames[0] == 'data: {"token": "Partial"}\n\n'
    assert "context window was exhausted" in frames[1]
    assert backend.transcripts == [None]
    assert looked_up == []


def test_only_timed_transcript_chunks_become_excerpt_ranges():
    metas = [
        {"source": "notes", "chunk_index": 0},
        {"source": "transcript", "start": 60, "end": 90.5},
        None,
        {"source": "transcript", "start": "soon", "end": 10},
        {"source": "transcript", "start": 0.0, "end": 30.0},
    ]

    assert routes_chat._excerpt_ranges(metas) == ((60.0, 90.5), (0.0, 30.0))


def test_without_a_query_embedding_no_passages_are_chosen():
    excerpts = asyncio.run(routes_chat._transcript_excerpts(1, None))

    assert excerpts == TranscriptExcerpts(ranges=())


@pytest.mark.parametrize("notes_size", [0, 8000])
def test_ollama_fits_the_excerpts_to_what_its_window_leaves(monkeypatch, notes_size):
    fake = _FakeOllama(_answer(FITS, "Answer."))
    backend = _backend(fake, context_window=8192)
    budgets: list = []

    def _excerpts(recording_id, ranges, max_bytes):
        budgets.append(max_bytes)
        return TranscriptExcerpts(
            ranges=tuple(ranges), text="[00:05] Ana: We ship Friday."
        )

    monkeypatch.setattr(
        OllamaLLMBackend, "get_transcript_excerpts_for_llm", staticmethod(_excerpts)
    )

    answer = backend.ask_question_about_meeting(
        user_question="When do we ship?",
        meeting_notes="n" * notes_size,
        diarized_transcript=EXCERPTS,
        recording_id=1,
    )

    assert answer == "Answer."
    [budget] = budgets
    # The window less a quarter for the answer and the rest of the prompt,
    # priced at 2.6 bytes a token.
    assert 0 < budget <= (8192 - 2048) * 2.6 - notes_size
    prompt = fake.chat_payloads[0]["messages"][-1]["content"]
    assert "# Transcript Excerpts:" in prompt
    assert "[00:05] Ana: We ship Friday." in prompt
