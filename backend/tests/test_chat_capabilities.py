"""Meeting Chat states what it can do, and claims the notes tool only when sent.

Each case drives a real backend chat path against a fake SDK or HTTP client
(the LLM edge) and reads back what actually went out: the tools the request
carried and the prompt text. The capability statement must match the tools:
a path that sends ``update_meeting_notes`` says it can change the notes, and
every other path says it cannot. Neither may offer to act after the reply.
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
from backend.utils.chat_prompt import (
    CAPABILITIES_WITH_NOTES_TOOL,
    CAPABILITIES_WITHOUT_TOOLS,
)

QUESTION = "What is this meeting about?"
NOTES = "# Notes\n- launch moved to May"
TRANSCRIPT = "[00:01] Speaker A: we move the launch to May"
NOTES_TOOL = "update_meeting_notes"

# What one chat request sent: the tool names it offered and its prompt text.
Sent = tuple[set[str], str]


def _chat(backend, stream: bool) -> None:
    kwargs = {
        "user_question": QUESTION,
        "meeting_notes": NOTES,
        "diarized_transcript": TRANSCRIPT,
    }
    if stream:
        list(backend.ask_question_streaming(**kwargs))
    else:
        backend.ask_question_about_meeting(**kwargs)


def _anthropic(stream: bool) -> Sent:
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
    _chat(backend, stream)

    tools = {tool["name"] for tool in sent.get("tools") or []}
    return tools, "".join(block["text"] for block in sent["system"])


def _openai(stream: bool) -> Sent:
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
    _chat(backend, stream)

    tools = {tool["function"]["name"] for tool in sent.get("tools") or []}
    return tools, sent["messages"][0]["content"]


def _gemini(stream: bool) -> Sent:
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
    _chat(backend, stream)

    config = sent.get("config")
    tools = {tool.__name__ for tool in (config.tools if config else None) or []}
    return tools, sent["contents"][-1]["parts"][0]["text"]


def _ollama(stream: bool) -> Sent:
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
    _chat(backend, stream)

    return set(sent.get("tools") or []), sent["messages"][-1]["content"]


def _cli(stream: bool) -> Sent:
    prompts: list[str] = []

    class FakeManager:
        # The real manager runs the SDK with allowed_tools=[] (cli/manager.py),
        # so a subscription chat never has the notes tool.
        def run_single_turn(self, user_id, prompt, *, model=None, timeout=300):
            prompts.append(prompt)
            return "An answer."

        def stream_single_turn(self, user_id, prompt, *, model=None, timeout=300):
            prompts.append(prompt)
            yield "An answer."

    backend = CliLLMBackend(model="claude-sonnet-5", user_id=7)
    backend._manager = FakeManager()
    _chat(backend, stream)

    return set(), prompts[0]


PATHS: dict[str, tuple[Callable[[bool], Sent], bool, bool]] = {
    # id: (driver, streamed, sends the notes tool)
    "anthropic-stream": (_anthropic, True, True),
    "anthropic-ask": (_anthropic, False, False),
    "openai-stream": (_openai, True, True),
    "openai-ask": (_openai, False, False),
    "gemini-stream": (_gemini, True, True),
    "gemini-ask": (_gemini, False, False),
    "ollama-stream": (_ollama, True, False),
    "ollama-ask": (_ollama, False, False),
    "cli-stream": (_cli, True, False),
    "cli-ask": (_cli, False, False),
}


@pytest.mark.parametrize("path", list(PATHS))
def test_chat_capability_statement_matches_the_tools_sent(path: str) -> None:
    driver, stream, has_notes_tool = PATHS[path]

    tools, prompt = driver(stream)

    assert (NOTES_TOOL in tools) is has_notes_tool
    assert NOTES in prompt and TRANSCRIPT in prompt
    if has_notes_tool:
        assert CAPABILITIES_WITH_NOTES_TOOL in prompt
        assert CAPABILITIES_WITHOUT_TOOLS not in prompt
    else:
        assert CAPABILITIES_WITHOUT_TOOLS in prompt
        assert CAPABILITIES_WITH_NOTES_TOOL not in prompt
