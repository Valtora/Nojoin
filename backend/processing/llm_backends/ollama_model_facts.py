"""Cached model capabilities and context-window metadata from Ollama's /api/show."""

import logging
import time
from typing import Optional

from requests import RequestException

logger = logging.getLogger("backend.processing.llm_backends.ollama")

# Ollama's num_ctx defaults to 2048, which silently truncates meeting-length prompts.
OLLAMA_DEFAULT_NUM_CTX = 8192

# /api/show facts are reused for this long per server and model: a model
# pulled again under the same name can change its context length or
# capabilities, and /api/show carries no digest to key on.
MODEL_SHOW_TTL_SECONDS = 300.0
_model_facts_cache: dict[tuple[str, str], tuple[float, dict]] = {}


class OllamaModelFactsMixin:
    """Model capabilities and context options for the Ollama backend."""

    def _model_facts(self) -> dict:
        """The model's trained context length and capabilities from ``/api/show``.

        Cached per server and model (backends are built per call, so the cache
        is per process). A failure is raised and not cached.
        """
        if not self.model:
            return {}
        key = (self.api_url, self.model)
        cached = _model_facts_cache.get(key)
        now = time.monotonic()
        if cached and now - cached[0] < MODEL_SHOW_TTL_SECONDS:
            return cached[1]
        resp = self._post("/api/show", json={"model": self.model}, timeout=10)
        resp.raise_for_status()
        body = resp.json()
        if not isinstance(body, dict):
            raise ValueError("Ollama /api/show returned a non-object body")
        info = body.get("model_info")
        length = None
        if isinstance(info, dict):
            length = info.get(f"{info.get('general.architecture')}.context_length")
        facts = {
            "context_length": length
            if isinstance(length, int) and length > 0
            else None,
            "capabilities": body.get("capabilities"),
        }
        _model_facts_cache[key] = (now, facts)
        return facts

    def _model_context_length(self) -> Optional[int]:
        """The model's trained context length, or None when unknown.

        Ollama clamps num_ctx to this length when it loads the model, so it is
        the real ceiling of the window.
        """
        try:
            return self._model_facts().get("context_length")
        except (RequestException, ValueError) as e:
            # An unknown length falls back to the configured window.
            logger.debug(f"Ollama context length probe failed for {self.model}: {e}")
            return None

    def _configured_window(self) -> int:
        # num_ctx must always be sent; an unset one lets Ollama fall back to its
        # 2048 default, which silently truncates meeting-length prompts.
        return int(getattr(self, "context_window", None) or OLLAMA_DEFAULT_NUM_CTX)

    def _context_window(self) -> int:
        """The num_ctx to send: the configured window, clamped to the model's.

        The configured window is the operator's VRAM budget (see
        ``ollama_context_window``), so it is never raised automatically.
        """
        configured = self._configured_window()
        trained = self._model_context_length()
        return min(configured, trained) if trained else configured

    def _chat_options(self, *, temperature: float) -> dict[str, object]:
        # No num_predict: with shift off the window already bounds the answer,
        # and sending one would override a Modelfile's own limit.
        return {"temperature": temperature, "num_ctx": self._context_window()}

    def supports_thinking(self) -> Optional[bool]:
        """Whether the model can think (``/api/show``), or None when unknown."""
        if not self.model:
            return None
        try:
            capabilities = self._model_facts().get("capabilities")
        except (RequestException, ValueError) as e:
            logger.debug(f"Ollama capability probe failed for {self.model}: {e}")
            return None
        if not isinstance(capabilities, list):
            return None
        return "thinking" in {str(c).lower() for c in capabilities}

    def _think_field(self) -> dict[str, bool]:
        """``think`` as the setting has it, unless the model cannot think.

        Ollama thinks by default, and a reasoning model can take minutes to its
        first answer token, so the request must say no. A model known not to
        think gets no key (a server may refuse it); unknown capabilities send it.
        """
        if self.supports_thinking() is False:
            return {}
        return {"think": self.think}

    def supports_vision(self) -> Optional[bool]:
        """Whether the selected Ollama model accepts images.

        Ollama is the one provider that answers this honestly up front:
        ``/api/show`` returns a ``capabilities`` list. That matters here because
        a self-hosted user picks their own model and has no other way to learn
        that document parsing will silently downgrade -- the cloud providers
        offer no equivalent, so they report ``None`` and are discovered on the
        first call instead.

        Returns ``None`` when the server is unreachable or predates the
        capabilities field, which the caller treats as "try it and see" rather
        than as a refusal.
        """
        if not self.model:
            return None
        try:
            capabilities = self._model_facts().get("capabilities")
        except Exception as e:  # noqa: BLE001 -- boundary: the capability probe is advisory; unknown means try it and see
            logger.debug(f"Ollama capability probe failed for {self.model}: {e}")
            return None
        if not isinstance(capabilities, list):
            return None
        return "vision" in {str(c).lower() for c in capabilities}
