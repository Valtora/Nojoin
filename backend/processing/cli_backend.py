"""M1 stub backend for CLI OAuth AI mode.

Selecting ``usage_model=cli_oauth`` resolves to ``provider="cli"`` (see
``backend.utils.llm_config``). Until the Claude Agent SDK subprocess lands
(later milestones), this backend raises a clear error from every
``LLMBackend`` method. When the user has a fallback provider configured,
``SecondaryLLMBackend`` catches the error and degrades to it; otherwise the
error surfaces to the caller so the unfinished state is unmistakable.
"""

from __future__ import annotations

from typing import Generator

from backend.processing.llm_services import LLMBackend

_UNAVAILABLE_MESSAGE = (
    "CLI OAuth AI mode is not yet available. Choose Ollama or BYOK as your usage "
    "model in Settings > AI, or configure a fallback provider."
)


class CliOAuthUnavailableError(RuntimeError):
    """Raised when CLI OAuth inference is selected but not yet operational."""


class CliLLMBackend(LLMBackend):
    """Placeholder for the CLI OAuth backend.

    Real inference via the Claude Agent SDK subprocess arrives in a later
    milestone; every contract method here raises ``CliOAuthUnavailableError``.
    Signatures accept ``*args``/``**kwargs`` so the raise fires cleanly whether
    called positionally or by keyword (e.g. through ``SecondaryLLMBackend``).
    """

    def __init__(self, model: str | None = None):
        self.model = model

    def _unavailable(self) -> None:
        raise CliOAuthUnavailableError(_UNAVAILABLE_MESSAGE)

    def infer_speaker_suggestions(self, *args, **kwargs):
        self._unavailable()

    def generate_meeting_notes(self, *args, **kwargs):
        self._unavailable()

    def generate_meeting_intelligence(self, *args, **kwargs):
        self._unavailable()

    def generate_meeting_edge(self, *args, **kwargs):
        self._unavailable()

    def infer_meeting_title(self, *args, **kwargs):
        self._unavailable()

    def ask_question_about_meeting(self, *args, **kwargs):
        self._unavailable()

    def ask_question_streaming(self, *args, **kwargs) -> Generator[str, None, None]:
        # A generator function: calling it returns a generator; the raise fires
        # on first iteration, which SecondaryLLMBackend handles like any other
        # primary failure before the first yield.
        self._unavailable()
        yield  # pragma: no cover - unreachable; keeps this a generator

    def list_models(self, *args, **kwargs):
        self._unavailable()

    def validate_api_key(self, *args, **kwargs) -> bool:
        self._unavailable()
        return False
