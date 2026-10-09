"""Context-window handling for the Ollama backend.

Ollama decides whether a prompt fits. Every ``/api/chat`` request is sent with
``truncate: false`` and ``shift: false`` (both honoured since Ollama 0.12.6), so
an oversized prompt is refused with a 4xx before anything is generated, and an
answer that fills the window ends with ``done_reason: "length"``. Left at their
defaults, Ollama cuts the prompt to fit (to about half the window on current
releases) and answers from what is left without saying so.

Chat is the one request that relied on that server-side cut, to drop old turns
of a long conversation. Those turns are trimmed here instead, so the meeting
context and the question on the last message are never cut.
"""

from __future__ import annotations

import re
from typing import Optional, Sequence

CONTEXT_EXHAUSTED_ADVICE = (
    "Increase the Ollama context window or select a model with a larger context."
)

# Phrases of Ollama's prompt-too-long refusal. 0.40 relays llama-server's
# ``exceed_context_size_error`` ("request (N tokens) exceeds the available
# context size (M tokens)"); 0.12.6 and Ollama's own runners answer "the input
# length exceeds the context length"; 0.40 with context shift disabled answers
# "the prompt is longer than the context length ...".
_OVERFLOW_PHRASES = (
    "exceed_context_size_error",
    "exceeds the available context size",
    "the input length exceeds the context length",
    "the prompt is longer than the context length",
)
_N_PROMPT_TOKENS = re.compile(r'"n_prompt_tokens"\s*:\s*(\d+)')
_N_CTX = re.compile(r'"n_ctx"\s*:\s*(\d+)')
_REQUEST_TOKENS = re.compile(r"request \((\d+) tokens\)")

# Transcript text runs 2.6 (short turns, each opening with a timestamp) to 3.5
# (prose) UTF-8 bytes per token, measured on Ollama. The low end over-counts,
# so history trimmed against it leaves the room it was meant to. Kept as a
# ratio of integers so the estimate stays exact integer arithmetic.
_ESTIMATE_BYTES, _ESTIMATE_TOKENS = 26, 10

# Room chat history trimming leaves for the answer: a quarter of the window,
# at most this many tokens. Chat answers are short; one that still runs out
# ends in a "length" stop and is refused, never saved.
CHAT_ANSWER_ROOM_MAX_TOKENS = 2048


class OllamaContextOverflowError(RuntimeError):
    """Ollama refused a prompt longer than its context window."""

    def __init__(self, prompt_tokens: Optional[int], window: int):
        self.prompt_tokens = prompt_tokens
        self.window = window
        size = (
            f"the prompt is {prompt_tokens:,} tokens"
            if prompt_tokens
            else "the prompt is longer than that"
        )
        super().__init__(
            "The Ollama context window was exhausted: Ollama refused the "
            f"request because the window is {window:,} tokens and {size}. "
            f"{CONTEXT_EXHAUSTED_ADVICE}"
        )


def context_overflow_from_response(
    status_code: int, body: object, window: int
) -> Optional[OllamaContextOverflowError]:
    """The overflow refusal in an Ollama error response, or None.

    ``window`` is the num_ctx that was sent; the count Ollama reports wins.
    """
    if status_code not in (400, 413) or not isinstance(body, dict):
        return None
    error = body.get("error")
    if not isinstance(error, str):
        return None
    if not any(phrase in error.lower() for phrase in _OVERFLOW_PHRASES):
        return None
    prompt = _N_PROMPT_TOKENS.search(error) or _REQUEST_TOKENS.search(error)
    n_ctx = _N_CTX.search(error)
    return OllamaContextOverflowError(
        int(prompt.group(1)) if prompt else None,
        int(n_ctx.group(1)) if n_ctx else window,
    )


def _content_bytes(message: dict) -> int:
    return len(str(message.get("content") or "").encode("utf-8"))


def estimate_tokens(messages: Sequence[dict]) -> int:
    """A deliberately high token estimate for the text of ``messages``."""
    size = sum(_content_bytes(m) for m in messages)
    return -(-size * _ESTIMATE_TOKENS // _ESTIMATE_BYTES)


def chat_answer_room(window: int) -> int:
    return min(CHAT_ANSWER_ROOM_MAX_TOKENS, window // 4)


def _split(messages: list[dict]) -> tuple[list[dict], list[dict], dict]:
    """System messages, the droppable turns between, and the last message."""
    *history, last = messages
    system = [m for m in history if m.get("role") == "system"]
    turns = [m for m in history if m.get("role") != "system"]
    return system, turns, last


def _keep_newest(messages: list[dict], budget: float, tokens_per_byte: float):
    """System messages, the newest turns within ``budget``, and the last message."""
    system, turns, last = _split(messages)
    kept: list[dict] = []
    for message in reversed(turns):
        budget -= _content_bytes(message) * tokens_per_byte
        if budget < 0:
            break
        kept.insert(0, message)
    # A conversation the model reads should open on a user turn.
    while kept and kept[0].get("role") == "assistant":
        kept.pop(0)
    return [*system, *kept, last]


def trim_chat_history(messages: list[dict], window: int) -> list[dict]:
    """Drop the oldest chat turns that would not fit beside the last message."""
    if len(messages) < 2:
        return messages
    system, _, last = _split(messages)
    budget = window - chat_answer_room(window) - estimate_tokens([*system, last])
    return _keep_newest(messages, budget, _ESTIMATE_TOKENS / _ESTIMATE_BYTES)


def trim_chat_history_after_overflow(
    messages: list[dict], overflow: OllamaContextOverflowError
) -> Optional[list[dict]]:
    """Fewer turns for one retry after Ollama refused ``messages``, or None.

    With Ollama's exact count, enough of the oldest turns go to bring that
    count under the window, priced at this request's own tokens per byte.
    Without a count, every turn goes. None when no turn is left to drop.
    """
    system, turns, last = _split(messages)
    if not turns:
        return None
    total_bytes = sum(_content_bytes(m) for m in messages)
    if not overflow.prompt_tokens or not total_bytes:
        return [*system, last]
    tokens_per_byte = overflow.prompt_tokens / total_bytes
    excess = overflow.prompt_tokens - (
        overflow.window - chat_answer_room(overflow.window)
    )
    turns_tokens = sum(_content_bytes(m) for m in turns) * tokens_per_byte
    return _keep_newest(messages, turns_tokens - excess, tokens_per_byte)
