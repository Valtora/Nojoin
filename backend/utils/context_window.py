"""The error a backend raises when a request does not fit the model's window.

Only the Ollama backend raises it: a hosted provider's window is far larger than
any meeting. It is typed so that callers can tell it from every other failure
and fall back to a smaller request (notes in parts, chat from excerpts) once the
secondary provider, when one is configured, has had the whole request.
"""

from __future__ import annotations

from typing import Optional


class ContextWindowExceededError(RuntimeError):
    """A prompt, or a prompt and its answer, did not fit the context window.

    ``prompt_tokens`` is the provider's own count for the prompt when it gave
    one, and ``window`` the window in tokens when it is known.
    ``answer_incomplete`` means the prompt fitted and the answer ran out of
    room, so a shorter answer to the same prompt could still fit.
    """

    def __init__(
        self,
        message: str,
        *,
        prompt_tokens: Optional[int] = None,
        window: Optional[int] = None,
        answer_incomplete: bool = False,
    ):
        super().__init__(message)
        self.prompt_tokens = prompt_tokens
        self.window = window
        self.answer_incomplete = answer_incomplete
