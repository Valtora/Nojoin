"""CLI OAuth backend — routes inference through a user's Claude subscription.

A thin ``LLMBackend`` adapter over :class:`CliConversationManager`. Because
Claude is the same model family as the BYOK Anthropic backend, every method
reuses the inherited prompt builders and tolerant parsers on ``LLMBackend`` and
only swaps the provider API call for a single-turn Claude Agent SDK query
authenticated by the user's subscription token. Runs inside ``worker-io`` (the
manager imports the SDK lazily); selected per-user via ``usage_model=cli_oauth``.

Any failure raises :class:`CliOAuthUnavailableError`, which ``SecondaryLLMBackend``
catches to degrade to the user's configured BYOK/Ollama fallback when one exists.
"""

from __future__ import annotations

import logging
from typing import Dict, Generator, List, Optional, Sequence

from backend.processing.cli.manager import (
    CliConversationManager,
    CliOAuthUnavailableError,
)
from backend.processing.llm_services import LLMBackend
from backend.utils.meeting_edge import MeetingEdgeRequest, MeetingEdgeResult
from backend.utils.meeting_intelligence import (
    AutomaticMeetingIntelligenceRequest,
    AutomaticMeetingIntelligenceResult,
)
from backend.utils.meeting_notes import MeetingEventContext, NotesPromptContext
from backend.utils.speaker_name_suggestions import SpeakerInferenceResult
from backend.utils.vision import VisionImage

logger = logging.getLogger(__name__)

__all__ = ["CliLLMBackend", "CliOAuthUnavailableError"]

# Curated model lists — a subscription exposes no models endpoint, so the picker
# uses these static sets, ordered most→least capable with full, unambiguous ids.
# Keep in sync with the frontend cliModels.ts. Codex ids are curated (VERIFY
# against the live Codex model set before release).
_CLAUDE_CLI_MODELS = (
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
)
_CODEX_CLI_MODELS = (
    "gpt-5.6-sol",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
)
_MODELS_BY_PROVIDER = {
    "claude_code": _CLAUDE_CLI_MODELS,
    "codex": _CODEX_CLI_MODELS,
}


def models_for_provider(provider: str) -> list[str]:
    """The curated model ids a subscription provider accepts, most capable first."""
    return list(_MODELS_BY_PROVIDER.get(provider, _CLAUDE_CLI_MODELS))


class CliLLMBackend(LLMBackend):
    """LLMBackend that delegates every call to the subscription-CLI manager.

    Reuses the inherited prompt builders and tolerant parsers; the manager routes
    the single provider API call to the selected subscription (Claude Agent SDK
    or Codex CLI)."""

    def __init__(
        self,
        model: Optional[str] = None,
        user_id: Optional[int] = None,
        provider: str = "claude_code",
    ) -> None:
        # user_id is threaded by the resolver for cli configs. If it is missing,
        # construction still succeeds and the first inference raises
        # CliOAuthUnavailableError (so SecondaryLLMBackend degrades cleanly)
        # rather than hard-failing at build time.
        self.model = model
        self.user_id = user_id
        self.provider = provider
        self._manager = CliConversationManager(provider=provider)

    # --- async inference tasks (notes / speakers / title / intelligence) ---

    def infer_speaker_suggestions(  # noqa: PLR0913 - matches the LLMBackend contract
        self,
        transcript: str,
        prompt_template: str = None,
        timeout: int = 60,
        user_notes: Optional[str] = None,
        meeting_context: Optional[MeetingEventContext] = None,
        eligible_labels: Optional[Sequence[str]] = None,
    ) -> SpeakerInferenceResult:
        prompt = self.build_speaker_suggestion_prompt(
            prompt_template, transcript, eligible_labels, user_notes, meeting_context
        )
        text = self._run(prompt)
        return self.parse_speaker_inference_result(text, eligible_labels)

    def generate_meeting_notes(  # noqa: PLR0913 - matches the LLMBackend contract
        self,
        transcript: str,
        speaker_mapping: Dict[str, str],
        prompt_template: str = None,
        timeout: int = 60,
        user_notes: Optional[str] = None,
        meeting_context: Optional[MeetingEventContext] = None,
        output_language_instruction: Optional[str] = None,
        notes_context: Optional[NotesPromptContext] = None,
    ) -> str:
        prompt = self.build_notes_prompt(
            prompt_template,
            transcript,
            speaker_mapping,
            user_notes,
            meeting_context,
            output_language_instruction,
            notes_context,
        )
        text = self._run(prompt)
        return self.finalise_meeting_notes(self.parse_notes(text), user_notes)

    def generate_meeting_intelligence(
        self,
        request: AutomaticMeetingIntelligenceRequest,
        prompt_template: str = None,
        timeout: int = 60,
    ) -> AutomaticMeetingIntelligenceResult:
        prompt = self.build_automatic_meeting_intelligence_prompt(
            request, prompt_template
        )
        text = self._run(prompt)
        return self.parse_automatic_meeting_intelligence_result(text, request)

    def generate_text(
        self,
        prompt: str,
        timeout: int = 60,
        max_tokens: int = 4096,
    ) -> str:
        return self._run(prompt)

    def generate_text_from_images(
        self,
        prompt: str,
        images: Sequence[VisionImage],
        timeout: int = 120,
        max_tokens: int = 8192,
    ) -> str:
        # Both subscription CLIs take images without any tool grant: Codex via
        # `--image <FILE>`, Claude via inline content blocks on the streaming
        # input. The manager picks the right one per provider.
        return self._manager.run_single_turn(
            self.user_id,
            prompt,
            model=self.model,
            timeout=timeout,
            images=images,
        )

    def supports_vision(self) -> Optional[bool]:
        # Both CLIs accept images, but the model actually serving the
        # subscription is chosen upstream and may not. None keeps the honest
        # "attempt it and find out" behaviour of the hosted providers.
        return None

    def generate_meeting_edge(
        self,
        request: MeetingEdgeRequest,
        prompt_template: str = None,
        timeout: int = 60,
    ) -> MeetingEdgeResult:
        # Stateless single-turn, like the other backends: the bounded rolling
        # summary carried in the request provides cross-refresh context, so no
        # resumable session is needed (which would replay unbounded history).
        # self.model is already cli_live_model for the meeting_edge purpose.
        prompt = self.build_meeting_edge_prompt(request, prompt_template)
        text = self._manager.run_single_turn(
            self.user_id, prompt, model=self.model, timeout=timeout
        )
        return self.parse_meeting_edge_result(text, request)

    def infer_meeting_title(
        self,
        transcript: str,
        prompt_template: str = None,
        timeout: int = 60,
        output_language_instruction: Optional[str] = None,
    ) -> str:
        prompt = self.build_title_prompt(
            prompt_template,
            transcript,
            output_language_instruction,
        )
        return self.parse_title(self._run(prompt))

    # --- chat ---

    def ask_question_about_meeting(  # noqa: PLR0913 - matches the LLMBackend contract
        self,
        user_question: str,
        meeting_notes: str,
        diarized_transcript: str,
        conversation_history: list = None,
        timeout: int = 60,
        recording_id: str = None,
    ):
        prompt = self._chat_prompt(
            user_question,
            meeting_notes,
            diarized_transcript,
            conversation_history,
            recording_id,
        )
        return self._run(prompt)

    def ask_question_streaming(  # noqa: PLR0913 - matches the LLMBackend contract
        self,
        user_question: str,
        meeting_notes: str,
        diarized_transcript: str,
        conversation_history: list = None,
        timeout: int = 60,
        recording_id: str = None,
    ) -> Generator[str, None, None]:
        prompt = self._chat_prompt(
            user_question,
            meeting_notes,
            diarized_transcript,
            conversation_history,
            recording_id,
        )
        yield from self._manager.stream_single_turn(
            self.user_id, prompt, model=self.model
        )

    # --- misc contract methods ---

    def list_models(self) -> List[str]:
        # No live models endpoint under a subscription; return the curated set
        # for the active provider.
        return list(_MODELS_BY_PROVIDER.get(self.provider, _CLAUDE_CLI_MODELS))

    def validate_api_key(self) -> bool:
        # A minimal single-turn round-trip proves the subscription token works.
        self._run("Reply with exactly: OK")
        return True

    # --- helpers ---

    def _run(self, prompt: str) -> str:
        return self._manager.run_single_turn(self.user_id, prompt, model=self.model)

    def _chat_prompt(
        self,
        user_question: str,
        meeting_notes: str,
        diarized_transcript: str,
        conversation_history: list,
        recording_id: Optional[str],
    ) -> str:
        # Mirror the other backends: prefer the DB-mapped transcript when a
        # recording id is supplied (resolves diarization labels to names).
        if recording_id is not None:
            diarized_transcript = self.get_mapped_transcript_for_llm(recording_id)
        base = self._build_chat_prompt(
            user_question, meeting_notes, diarized_transcript
        )
        history = self._render_history(conversation_history)
        return f"{history}\n\n{base}" if history else base

    @staticmethod
    def _render_history(conversation_history: list) -> str:
        # The SDK query takes a single prompt string, so prior turns are folded
        # into the prompt (Gemini-style 'model' role → 'Assistant').
        if not conversation_history:
            return ""
        lines: List[str] = []
        for msg in conversation_history:
            if not (isinstance(msg, dict) and msg.get("role") and msg.get("parts")):
                continue
            speaker = "Assistant" if msg["role"] == "model" else "User"
            for part in msg["parts"]:
                text = part.get("text") if isinstance(part, dict) else None
                if text:
                    lines.append(f"{speaker}: {text}")
        if not lines:
            return ""
        return "# Prior conversation in this chat\n" + "\n".join(lines)
