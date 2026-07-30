import logging

from backend.processing.llm_backends.anthropic import AnthropicLLMBackend
from backend.processing.llm_backends.base import LLMBackend
from backend.processing.llm_backends.gemini import GeminiLLMBackend
from backend.processing.llm_backends.ollama import OllamaLLMBackend
from backend.processing.llm_backends.openai import OpenAILLMBackend

logger = logging.getLogger(__name__)


# --- LLM Backend Factory ---
def get_llm_backend(
    provider: str,
    api_key=None,
    model=None,
    api_url=None,
    context_window: int | None = None,
    allow_private_api_url: bool = False,
    cli_user_id: int | None = None,
    cli_provider: str | None = None,
):
    """
    Factory function to instantiate the appropriate LLM backend.
    Heavy dependencies are only imported when needed.
    Args:
        provider (str): 'gemini', 'openai', 'anthropic', or 'ollama'
        api_key (str): API key for the provider (optional)
        model (str): Model name (optional)
        api_url (str): API URL for local providers (optional)
    Returns:
        Instance of the appropriate LLMBackend subclass.
    Raises:
        ValueError: If provider is unknown.
    """
    from backend.utils.config_manager import config_manager

    if model is None:
        if provider == "ollama":
            model = config_manager.get("ollama_model")
        else:
            model = config_manager.get(f"{provider}_model")

    logger.info(
        "Creating LLM backend: provider=%s model=%s api_url=%s",
        provider,
        model,
        api_url,
    )

    if provider == "gemini":
        return GeminiLLMBackend(api_key=api_key, model=model)
    elif provider == "openai":
        return OpenAILLMBackend(api_key=api_key, model=model)
    elif provider == "anthropic":
        return AnthropicLLMBackend(api_key=api_key, model=model)
    elif provider == "ollama":
        return OllamaLLMBackend(
            api_url=api_url,
            model=model,
            context_window=context_window,
            allow_private_api_url=allow_private_api_url,
        )
    elif provider == "cli":
        from backend.processing.cli_backend import CliLLMBackend

        cli = cli_provider or "claude_code"  # subscription CLI: Claude or Codex
        return CliLLMBackend(model=model, user_id=cli_user_id, provider=cli)
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")


class SecondaryLLMBackend(LLMBackend):
    """Wraps a primary and secondary LLM backend. On any error from primary,
    falls back to the secondary backend. If both fail, raises the primary error."""

    def __init__(self, primary: LLMBackend, secondary: LLMBackend):
        self._primary = primary
        self._secondary = secondary
        logger.info(
            "SecondaryLLMBackend: primary=%s secondary=%s",
            getattr(primary, "model", "unknown"),
            getattr(secondary, "model", "unknown"),
        )

    def _call_with_secondary(self, method_name: str, *args, **kwargs):
        try:
            result = getattr(self._primary, method_name)(*args, **kwargs)
            logger.info(
                "Primary LLM (%s) handled %s successfully.",
                getattr(self._primary, "model", "unknown"),
                method_name,
            )
            return result
        except Exception as primary_exc:  # noqa: BLE001 -- boundary: fall back to secondary LLM on any primary failure
            logger.warning(
                "Primary LLM (%s) failed on %s: %s. Falling back to secondary (%s).",
                getattr(self._primary, "model", "unknown"),
                method_name,
                primary_exc,
                getattr(self._secondary, "model", "unknown"),
            )
            try:
                result = getattr(self._secondary, method_name)(*args, **kwargs)
                logger.info(
                    "Secondary LLM call succeeded on %s after primary failure.",
                    method_name,
                )
                return result
            except Exception:  # noqa: BLE001 -- boundary: surface the original primary failure if secondary also fails
                logger.error("Secondary LLM also failed on %s.", method_name)
                raise primary_exc

    def _stream_with_secondary(self, method_name: str, *args, **kwargs):
        has_yielded = False
        try:
            gen = getattr(self._primary, method_name)(*args, **kwargs)
            first_chunk = next(gen)
            try:
                yield first_chunk
                has_yielded = True
                yield from gen
            finally:
                if not has_yielded:
                    gen.close()
        except StopIteration:
            return
        except Exception as primary_exc:
            if has_yielded:
                raise primary_exc
            logger.warning(
                "Primary LLM (%s) failed on streaming %s: %s. Falling back to secondary (%s).",
                getattr(self._primary, "model", "unknown"),
                method_name,
                primary_exc,
                getattr(self._secondary, "model", "unknown"),
            )
            try:
                yield from getattr(self._secondary, method_name)(*args, **kwargs)
            except Exception:  # noqa: BLE001 -- boundary: surface the original primary failure if secondary also fails
                raise primary_exc

    def infer_speaker_suggestions(
        self,
        transcript,
        prompt_template=None,
        timeout=60,
        user_notes=None,
        meeting_context=None,
        eligible_labels=None,
    ):
        return self._call_with_secondary(
            "infer_speaker_suggestions",
            transcript,
            prompt_template,
            timeout,
            user_notes=user_notes,
            meeting_context=meeting_context,
            eligible_labels=eligible_labels,
        )

    def generate_meeting_intelligence(self, request, prompt_template=None, timeout=60):
        return self._call_with_secondary(
            "generate_meeting_intelligence", request, prompt_template, timeout
        )

    def generate_text(self, prompt, timeout=60, max_tokens=4096):
        return self._call_with_secondary(
            "generate_text", prompt, timeout, max_tokens=max_tokens
        )

    def generate_text_from_images(self, prompt, images, timeout=120, max_tokens=8192):
        # Must be forwarded explicitly. Without this the call reaches
        # LLMBackend's default, which raises VisionUnsupportedError, and every
        # document downgrades to a structural parse even when the primary
        # provider handles images perfectly well.
        #
        # The fallback is meaningful here rather than incidental: a primary that
        # cannot accept images fails, the secondary is tried, and only if both
        # fail does the primary's VisionUnsupportedError surface and downgrade
        # the document once. That matches the provider chain the user is shown
        # in Settings.
        return self._call_with_secondary(
            "generate_text_from_images",
            prompt,
            images,
            timeout=timeout,
            max_tokens=max_tokens,
        )

    def supports_vision(self):
        """Whether either provider in the chain is known to accept images.

        Tri-state, like the backends it wraps: a definite yes from either is a
        yes, a definite no from both is a no, and anything else is unknown and
        must be discovered by making the call.
        """
        answers = [
            self._primary.supports_vision(),
            self._secondary.supports_vision(),
        ]
        if any(answer is True for answer in answers):
            return True
        if all(answer is False for answer in answers):
            return False
        return None

    def generate_meeting_edge(self, request, prompt_template=None, timeout=60):
        return self._call_with_secondary(
            "generate_meeting_edge", request, prompt_template, timeout
        )

    def generate_meeting_notes(  # noqa: PLR0913 - matches the LLMBackend contract
        self,
        transcript,
        speaker_mapping,
        prompt_template=None,
        timeout=60,
        user_notes=None,
        meeting_context=None,
        output_language_instruction=None,
        notes_context=None,
    ):
        return self._call_with_secondary(
            "generate_meeting_notes",
            transcript,
            speaker_mapping,
            prompt_template,
            timeout,
            user_notes=user_notes,
            meeting_context=meeting_context,
            output_language_instruction=output_language_instruction,
            notes_context=notes_context,
        )

    def infer_meeting_title(
        self,
        transcript,
        prompt_template=None,
        timeout=60,
        output_language_instruction=None,
    ):
        return self._call_with_secondary(
            "infer_meeting_title",
            transcript,
            prompt_template,
            timeout,
            output_language_instruction=output_language_instruction,
        )

    def ask_question_about_meeting(
        self,
        user_question,
        meeting_notes,
        diarized_transcript,
        conversation_history=None,
        timeout=60,
        recording_id=None,
    ):
        return self._call_with_secondary(
            "ask_question_about_meeting",
            user_question,
            meeting_notes,
            diarized_transcript,
            conversation_history,
            timeout,
            recording_id=recording_id,
        )

    def ask_question_streaming(
        self,
        user_question,
        meeting_notes,
        diarized_transcript,
        conversation_history=None,
        timeout=60,
        recording_id=None,
    ):
        return self._stream_with_secondary(
            "ask_question_streaming",
            user_question,
            meeting_notes,
            diarized_transcript,
            conversation_history,
            timeout,
            recording_id=recording_id,
        )

    def validate_api_key(self) -> bool:
        try:
            return self._primary.validate_api_key()
        except Exception as exc:  # noqa: BLE001 -- boundary: fall back to secondary LLM on any primary failure
            logger.debug(
                "Primary LLM validate_api_key failed: %s. Trying secondary.", exc
            )
            return self._secondary.validate_api_key()

    def list_models(self) -> list:
        try:
            return self._primary.list_models()
        except Exception as exc:  # noqa: BLE001 -- boundary: fall back to secondary LLM on any primary failure
            logger.debug("Primary LLM list_models failed: %s. Trying secondary.", exc)
            return self._secondary.list_models()


def get_llm_backend_with_secondary(
    primary_config,
    purpose: str = "default",
):
    """Instantiate primary LLM backend with optional secondary fallback.

    Args:
        primary_config: A ResolvedLLMConfig (from resolve_llm_config).
        purpose: "default" or "meeting_edge".

    Returns:
        An LLMBackend, wrapped in SecondaryLLMBackend if a secondary
        provider is configured and operational, otherwise just the primary.
    """
    primary = get_llm_backend(
        provider=primary_config.provider,
        api_key=primary_config.api_key,
        model=primary_config.model,
        api_url=primary_config.api_url,
        context_window=primary_config.context_window,
        cli_user_id=primary_config.cli_user_id,
        cli_provider=primary_config.cli_provider,
    )

    secondary_cfg = primary_config.secondary_config()
    if secondary_cfg is None:
        return primary

    try:
        # Build the secondary recursively so it can carry its own fallback: a CLI
        # OAuth config returns the whole server-default chain here, yielding a
        # nested SecondaryLLMBackend(sub, SecondaryLLMBackend(primary, secondary))
        # (user sub -> server primary -> server secondary). The ordinary 2-tier
        # case bottoms out immediately, so its behaviour is unchanged.
        secondary = get_llm_backend_with_secondary(secondary_cfg, purpose)
        return SecondaryLLMBackend(primary=primary, secondary=secondary)
    except Exception as exc:  # noqa: BLE001 -- boundary: continue with primary only if secondary init fails
        logger.warning(
            "Failed to initialise secondary LLM backend (%s): %s. "
            "Continuing with primary only.",
            secondary_cfg.provider,
            exc,
        )
        return primary
