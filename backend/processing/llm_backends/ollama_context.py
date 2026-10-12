"""Context-window handling for the Ollama backend.

Ollama decides whether a prompt fits. Every ``/api/chat`` request is sent with
``truncate: false`` and ``shift: false`` (both honoured since Ollama 0.12.6), so
an oversized prompt is refused with a 400 before anything is generated, and an
answer that fills the window ends with ``done_reason: "length"``. Left at their
defaults, Ollama cuts the prompt to fit (to about half the window on current
releases) and answers from what is left without saying so.

Chat is the one request that relied on that server-side cut, to drop old turns
of a long conversation. Those turns are trimmed here instead, so the meeting
context and the question on the last message are never cut.
"""

from __future__ import annotations

import json
from typing import Optional, Sequence

# Ollama 0.12.6 (and Ollama's own runners) refuse with this plain message and no
# counts. 0.34 and later relay llama-server's error as a JSON string, with type
# "exceed_context_size_error", "n_prompt_tokens" and "n_ctx".
_RUNNER_OVERFLOW = "the input length exceeds the context length"
_LLAMA_SERVER_OVERFLOW = "exceed_context_size_error"

# Prose and transcripts run 2.6 (short timestamped turns) to 5 UTF-8 bytes per
# token, measured on Ollama, so this estimate over-counts them and history
# trimmed against it leaves the room it was meant to. Numbers and code run
# denser; for those the retry on Ollama's own count takes over. Kept as a
# ratio of integers so the estimate stays exact integer arithmetic.
_ESTIMATE_BYTES, _ESTIMATE_TOKENS = 26, 10

# Room chat history trimming leaves for the answer: a quarter of the window,
# at most this many tokens. Chat answers are short; one that still runs out
# ends in a "length" stop and is refused, never saved.
CHAT_ANSWER_ROOM_MAX_TOKENS = 2048


def context_advice(model_maximum: bool) -> str:
    """What to do about a full window, given whether it is the model's own limit.

    When the window was already lowered to the model's trained maximum, a larger
    configured window changes nothing.
    """
    if model_maximum:
        return (
            "The meeting is too long for this model's context; select a model "
            "with a larger context."
        )
    return "Increase the Ollama context window or select a model with a larger context."


class OllamaContextOverflowError(RuntimeError):
    """Ollama refused a prompt longer than its context window."""

    def __init__(
        self, prompt_tokens: Optional[int], window: int, *, model_maximum: bool
    ):
        self.prompt_tokens = prompt_tokens
        self.window = window
        size = (
            f"the prompt is {prompt_tokens:,} tokens"
            if prompt_tokens
            else "the prompt is longer than that"
        )
        limit = "this model's maximum context" if model_maximum else "the window"
        super().__init__(
            "The Ollama context window was exhausted: Ollama refused the request "
            f"because {limit} is {window:,} tokens and {size}. "
            f"{context_advice(model_maximum)}"
        )


def _llama_server_error(error: str) -> Optional[dict]:
    """The structured error inside a relayed llama-server message, if any."""
    try:
        detail = json.loads(error)
    except ValueError:
        return None
    inner = detail.get("error") if isinstance(detail, dict) else None
    return inner if isinstance(inner, dict) else None


def _count(value: object) -> Optional[int]:
    return value if isinstance(value, int) and value > 0 else None


def context_overflow_from_response(
    body: object, sent_window: int, configured_window: int
) -> Optional[OllamaContextOverflowError]:
    """The overflow refusal in an Ollama error body, or None.

    ``sent_window`` is the num_ctx that was sent; the n_ctx Ollama reports
    wins. A window below ``configured_window`` is the model's own maximum.
    """
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if not isinstance(error, str):
        return None
    detail = _llama_server_error(error)
    if detail is not None:
        if detail.get("type") != _LLAMA_SERVER_OVERFLOW:
            return None
        prompt_tokens = _count(detail.get("n_prompt_tokens"))
        window = _count(detail.get("n_ctx")) or sent_window
    elif _RUNNER_OVERFLOW in error.lower():
        prompt_tokens, window = None, sent_window
    else:
        return None
    return OllamaContextOverflowError(
        prompt_tokens, window, model_maximum=window < configured_window
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
