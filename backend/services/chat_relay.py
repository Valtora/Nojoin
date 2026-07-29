"""Redis-list relay: stream worker-produced chat tokens back to the API's SSE.

CLI OAuth chat inference runs in the ``worker-io`` Celery lane (the only place
the Claude Agent SDK lives). The worker publishes token envelopes onto a Redis
list keyed by the Celery task id; the API's ``StreamingResponse`` blocks on that
list and re-frames each envelope into the *existing* chat SSE wire format, so the
browser client is unchanged.

A list (not pub/sub) is used deliberately: it buffers chunks the worker emits
before the API subscribes, and lets the relay bound hangs by polling the task
state on each idle interval instead of blocking forever.
"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import redis
import redis.asyncio as aioredis

from backend.core.redis import get_redis_url

logger = logging.getLogger(__name__)

_KEY_PREFIX = "nojoin:cli_chat:"
_KEY_TTL_SECONDS = 900  # reaps the key if the relay never drains it (crash safety)
_BLPOP_TIMEOUT_SECONDS = 5  # per blocking pop; the loop then consults task state
_RELAY_DEADLINE_SECONDS = 300  # overall wait; matches the chat inference timeout

_T_TOKEN = "tok"
_T_DONE = "done"
_T_ERROR = "err"

_GENERIC_ERROR = (
    "An internal error occurred while communicating with the AI service. "
    "Please try again."
)
_DEADLINE_ERROR = "The AI provider took too long to respond. Please try again."


def _key(task_id: str) -> str:
    return f"{_KEY_PREFIX}{task_id}"


class ChatStreamPublisher:
    """Worker side: push chat-token envelopes onto the task's Redis list."""

    def __init__(self, task_id: str) -> None:
        self._key = _key(task_id)
        self._client = redis.from_url(get_redis_url(), decode_responses=True)

    def _push(self, envelope: dict) -> None:
        # Pipeline the push + TTL refresh so a crashed relay can never leak the key.
        pipe = self._client.pipeline()
        pipe.rpush(self._key, json.dumps(envelope))
        pipe.expire(self._key, _KEY_TTL_SECONDS)
        pipe.execute()

    def publish_token(self, text: str) -> None:
        if text:
            self._push({"t": _T_TOKEN, "v": text})

    def publish_done(self) -> None:
        self._push({"t": _T_DONE})

    def publish_error(self, message: str) -> None:
        self._push({"t": _T_ERROR, "v": message})

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # noqa: BLE001 - best effort on teardown
            pass


async def relay_sse_frames(task_id: str) -> AsyncIterator[str]:
    """API side: yield SSE frames byte-identical to the inline chat generator.

    Blocks on the task's Redis list, re-framing each envelope. Bounds hangs by
    consulting the Celery task state on each idle interval and enforcing an
    overall deadline; always terminates with ``data: [DONE]\\n\\n``.
    """
    from celery.result import AsyncResult

    from backend.celery_app import celery_app

    client = aioredis.from_url(get_redis_url(), decode_responses=True)
    key = _key(task_id)
    waited = 0
    try:
        while waited < _RELAY_DEADLINE_SECONDS:
            popped = await client.blpop(key, timeout=_BLPOP_TIMEOUT_SECONDS)
            if popped is None:
                waited += _BLPOP_TIMEOUT_SECONDS
                state = AsyncResult(task_id, app=celery_app).state
                if state in ("FAILURE", "REVOKED"):
                    yield _error_frame(_GENERIC_ERROR)
                    break
                continue
            _list_key, raw = popped
            frame, terminal = _frame_for(raw)
            if frame:
                yield frame
            if terminal:
                break
        else:
            # Deadline reached without a terminal sentinel.
            yield _error_frame(_DEADLINE_ERROR)
    finally:
        await client.aclose()
    yield "data: [DONE]\n\n"


def _frame_for(raw: str) -> tuple[str, bool]:
    """Return ``(sse_frame, is_terminal)`` for a raw envelope; unknown types skipped."""
    try:
        envelope = json.loads(raw)
    except (TypeError, ValueError):
        return "", False
    kind = envelope.get("t")
    if kind == _T_TOKEN:
        return f"data: {json.dumps({'token': str(envelope.get('v', ''))})}\n\n", False
    if kind == _T_ERROR:
        return _error_frame(str(envelope.get("v", "")) or _GENERIC_ERROR), True
    if kind == _T_DONE:
        return "", True
    return "", False


def _error_frame(message: str) -> str:
    return f"data: {json.dumps({'error': message})}\n\n"


def friendly_chat_error(exc: Exception) -> str:
    """Map an upstream failure to a user-facing chat error string.

    Shared by the worker (CLI chat) and the API's inline generator so both speak
    the same copy.
    """
    # A CLI usage-limit error already carries a precise, reset-time-aware message.
    from backend.processing.cli.manager import CliUsageLimitError

    if isinstance(exc, CliUsageLimitError):
        return str(exc)

    error_msg = str(exc).lower()
    if "503" in error_msg or "unavailable" in error_msg or "overloaded" in error_msg:
        return (
            "The AI provider is currently experiencing high demand and is "
            "unavailable. Please try again later."
        )
    if "429" in error_msg or "rate limit" in error_msg or "quota" in error_msg:
        return (
            "You have exceeded your AI provider's rate limit or quota. Please "
            "check your billing or try again later."
        )
    if "timeout" in error_msg or "deadline" in error_msg:
        return "The AI provider took too long to respond. Please try again."
    if "context window was exhausted" in error_msg or "done_reason=length" in error_msg:
        return (
            "The Ollama context window was exhausted before a full answer could "
            "be generated. Increase the Ollama context window or choose a "
            "larger-context model."
        )
    return _GENERIC_ERROR
