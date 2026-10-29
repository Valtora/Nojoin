"""Meeting Chat states what it can do, and claims the notes tool only when sent.

Each case drives a real backend chat path against a fake SDK or HTTP client
(the LLM edge) and reads back what actually went out: the tools the request
carried and the prompt text. The capability statement must match the tools:
a path that sends ``update_meeting_notes`` says it can change the notes, and
every other path says it cannot. Neither may offer to act after the reply.

The subscription (CLI) backend is covered for its prompt only: the tools it
gives the Claude Agent SDK are set inside its manager, and the SDK is not
installed outside the io worker, so these tests make no claim about them.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from types import SimpleNamespace

import pytest

from backend.processing.cli_backend import CliLLMBackend
from backend.processing.llm_services import (
    AnthropicLLMBackend,
    GeminiLLMBackend,
    OllamaLLMBackend,
    OpenAILLMBackend,
)
from backend.prompts import render_prompt
from backend.utils.chat_prompt import (
    ChatTranscript,
    LiveChatTranscript,
)

QUESTION = "What is this meeting about?"
NOTES = "# Notes\n- launch moved to May"
TRANSCRIPT = "[00:01] Speaker A: we move the launch to May"
NOTES_TOOL = "update_meeting_notes"

# What one chat request sent: the tool names it offered and its prompt text.
Sent = tuple[set[str], str]


def _assert_offers_nothing_after_the_reply(prompt: str, can_edit_notes: bool) -> None:
    """The restriction itself, checked apart from the prompt constants.

    Comparing against the constants alone would pass if both were rewritten to
    promise monitoring, so the key phrases are pinned here independently.
    """
    assert "cannot watch the meeting" in prompt
    assert "never offer to" in prompt
    assert ("cannot change the meeting notes" in prompt) is not can_edit_notes


def _chat(backend, stream: bool, transcript: ChatTranscript) -> None:
    kwargs = {
        "user_question": QUESTION,
        "meeting_notes": NOTES,
        "diarized_transcript": transcript,
    }
    if stream:
        list(backend.ask_question_streaming(**kwargs))
    else:
        backend.ask_question_about_meeting(**kwargs)


def _anthropic(stream: bool, transcript: ChatTranscript = TRANSCRIPT) -> Sent:
    sent: dict = {}
    reply = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="An answer.")],
        stop_reason="end_turn",
    )

    class FakeStream:
        # The streamed chat iterates events; the one-shot chat reads the final
        # message from the same stream.
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def __iter__(self):
            return iter(())

        def get_final_message(self):
            return reply

    class FakeMessages:
        def create(self, **kwargs):
            sent.update(kwargs)
            return reply

        def stream(self, **kwargs):
            sent.update(kwargs)
            return FakeStream()

    backend = object.__new__(AnthropicLLMBackend)
    backend.model = "claude-opus-5"
    backend.client = SimpleNamespace(messages=FakeMessages())
    _chat(backend, stream, transcript)

    tools = {tool["name"] for tool in sent.get("tools") or []}
    return tools, "".join(block["text"] for block in sent["system"])


def _openai(stream: bool, transcript: ChatTranscript = TRANSCRIPT) -> Sent:
    sent: dict = {}

    class FakeCompletions:
        def create(self, **kwargs):
            sent.update(kwargs)
            if kwargs.get("stream"):
                return iter(())
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="An answer."))]
            )

    backend = object.__new__(OpenAILLMBackend)
    backend.model = "gpt-5.5"
    backend.client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )
    _chat(backend, stream, transcript)

    tools = {tool["function"]["name"] for tool in sent.get("tools") or []}
    return tools, sent["messages"][0]["content"]


def _gemini(stream: bool, transcript: ChatTranscript = TRANSCRIPT) -> Sent:
    from google import genai

    sent: dict = {}

    class FakeModels:
        def generate_content(self, **kwargs):
            sent.update(kwargs)
            return SimpleNamespace(candidates=[], text="An answer.")

        def generate_content_stream(self, **kwargs):
            sent.update(kwargs)
            return iter(())

    backend = object.__new__(GeminiLLMBackend)
    backend.model = "gemini-2.5-pro"
    backend.genai = genai
    backend.client = SimpleNamespace(models=FakeModels())
    _chat(backend, stream, transcript)

    config = sent.get("config")
    tools = {tool.__name__ for tool in (config.tools if config else None) or []}
    return tools, sent["contents"][-1]["parts"][0]["text"]


def _ollama(stream: bool, transcript: ChatTranscript = TRANSCRIPT) -> Sent:
    sent: dict = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"message": {"content": "An answer."}, "done": True}

        def iter_lines(self):
            yield json.dumps(self.json()).encode("utf-8")

    class FakeRequests:
        def post(self, url, json, timeout, allow_redirects, stream=False):
            sent.update(json)
            return FakeResponse()

    backend = object.__new__(OllamaLLMBackend)
    backend.model = "llama3"
    backend.api_url = "http://ollama.local"
    backend.context_window = 131072
    backend.requests = FakeRequests()
    _chat(backend, stream, transcript)

    return set(sent.get("tools") or []), sent["messages"][-1]["content"]


def _cli_prompt(stream: bool) -> str:
    """The prompt the CLI backend hands its manager, which runs the SDK."""
    prompts: list[str] = []

    class FakeManager:
        def run_single_turn(self, user_id, prompt, *, model=None, timeout=300):
            prompts.append(prompt)
            return "An answer."

        def stream_single_turn(self, user_id, prompt, *, model=None, timeout=300):
            prompts.append(prompt)
            yield "An answer."

    backend = CliLLMBackend(model="claude-sonnet-5", user_id=7)
    backend._manager = FakeManager()
    _chat(backend, stream, TRANSCRIPT)

    return prompts[0]


PATHS: dict[str, tuple[Callable[..., Sent], bool, bool]] = {
    # id: (driver, streamed, sends the notes tool)
    "anthropic-stream": (_anthropic, True, True),
    "anthropic-ask": (_anthropic, False, False),
    "openai-stream": (_openai, True, True),
    "openai-ask": (_openai, False, False),
    "gemini-stream": (_gemini, True, True),
    "gemini-ask": (_gemini, False, False),
    "ollama-stream": (_ollama, True, False),
    "ollama-ask": (_ollama, False, False),
}


@pytest.mark.parametrize("path", list(PATHS))
def test_chat_capability_statement_matches_the_tools_sent(path: str) -> None:
    driver, stream, has_notes_tool = PATHS[path]

    tools, prompt = driver(stream)

    assert (NOTES_TOOL in tools) is has_notes_tool
    assert NOTES in prompt and TRANSCRIPT in prompt
    if has_notes_tool:
        assert render_prompt("chat.capabilities_with_notes_tool") in prompt
        assert render_prompt("chat.capabilities_without_tools") not in prompt
    else:
        assert render_prompt("chat.capabilities_without_tools") in prompt
        assert render_prompt("chat.capabilities_with_notes_tool") not in prompt
    _assert_offers_nothing_after_the_reply(prompt, has_notes_tool)


@pytest.mark.parametrize("path", list(PATHS))
def test_in_flight_chat_never_offers_the_notes_tool(path: str) -> None:
    # The pass still running writes the notes when it finishes, over anything
    # the tool would save now.
    driver, stream, _ = PATHS[path]

    tools, prompt = driver(stream, LiveChatTranscript(TRANSCRIPT))

    assert NOTES_TOOL not in tools
    assert NOTES in prompt and TRANSCRIPT in prompt
    assert render_prompt("chat.capabilities_without_tools") in prompt
    assert render_prompt("chat.capabilities_with_notes_tool") not in prompt
    _assert_offers_nothing_after_the_reply(prompt, can_edit_notes=False)


@pytest.mark.parametrize("stream", [True, False], ids=["cli-stream", "cli-ask"])
def test_cli_chat_prompt_offers_no_actions(stream: bool) -> None:
    prompt = _cli_prompt(stream)

    assert NOTES in prompt and TRANSCRIPT in prompt
    assert render_prompt("chat.capabilities_without_tools") in prompt
    assert render_prompt("chat.capabilities_with_notes_tool") not in prompt
    _assert_offers_nothing_after_the_reply(prompt, can_edit_notes=False)
