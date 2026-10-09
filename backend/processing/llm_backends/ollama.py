import logging
import time
from typing import Dict, Generator, List, Optional, Sequence

from requests import HTTPError, RequestException

from backend.utils.config_manager import config_manager
from backend.utils.meeting_analysis import (
    MeetingAnalysisRequest,
    MeetingAnalysisResult,
)
from backend.utils.meeting_edge import (
    MeetingEdgeRequest,
    MeetingEdgeResult,
)
from backend.utils.meeting_intelligence import (
    AutomaticMeetingIntelligenceRequest,
    AutomaticMeetingIntelligenceResult,
)
from backend.utils.meeting_notes import (
    MeetingEventContext,
    NotesPromptContext,
)
from backend.utils.ollama_url_policy import validate_ollama_api_url
from backend.utils.speaker_name_suggestions import (
    SpeakerInferenceResult,
)
from backend.utils.vision import VisionImage, VisionUnsupportedError

logger = logging.getLogger(__name__)

from backend.processing.llm_backends.base import (
    JSON_CONTRACT_ERRORS,
    LLMBackend,
    is_vision_unsupported_error,
    summarize_llm_response_shape,
)
from backend.processing.llm_backends.ollama_context import (
    OllamaContextOverflowError,
    context_advice,
    context_overflow_from_response,
    trim_chat_history,
    trim_chat_history_after_overflow,
)

# Ollama's num_ctx defaults to 2048, which silently truncates meeting-length prompts.
OLLAMA_DEFAULT_NUM_CTX = 8192

# /api/show facts are reused for this long per server and model: a model
# pulled again under the same name can change its context length or
# capabilities, and /api/show carries no digest to key on.
MODEL_SHOW_TTL_SECONDS = 300.0
_model_facts_cache: dict[tuple[str, str], tuple[float, dict]] = {}


class OllamaLLMBackend(LLMBackend):
    def __init__(
        self,
        api_url=None,
        model=None,
        context_window: int | None = None,
        allow_private_api_url: bool = False,
    ):
        import requests

        self.requests = requests
        trusted_api_url = config_manager.get("ollama_api_url")
        if api_url is None:
            api_url = trusted_api_url
        if not api_url:
            api_url = "http://host.docker.internal:11434"

        self.api_url = validate_ollama_api_url(
            api_url,
            allow_private=allow_private_api_url,
            trusted_url=trusted_api_url,
        )
        self.model = model or config_manager.get("ollama_model")
        self.context_window = context_window or config_manager.get(
            "ollama_context_window"
        )

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

    def _post_chat(self, payload: dict, **kwargs):
        """POST ``/api/chat`` with Ollama as the judge of whether it fits.

        ``truncate: false`` makes Ollama refuse a prompt longer than num_ctx
        instead of cutting it and answering from the rest; ``shift: false``
        makes an answer that fills the window stop with ``done_reason:
        "length"`` instead of shifting the prompt out. Servers before 0.12.6
        ignore both; ``_raise_if_truncated`` covers them.
        """
        payload = {**payload, "truncate": False, "shift": False}
        resp = self._post("/api/chat", json=payload, **kwargs)
        try:
            resp.raise_for_status()
        except HTTPError as e:
            try:
                body = resp.json()
            except ValueError:
                body = None
            overflow = context_overflow_from_response(
                body,
                int(payload["options"]["num_ctx"]),
                self._configured_window(),
            )
            if overflow is None:
                raise
            raise overflow from e
        return resp

    def _post_chat_trimming_history(self, payload: dict, **kwargs):
        """POST a conversation, dropping the oldest turns that do not fit.

        The meeting context and the question ride on the last message and are
        never cut. Turns are trimmed against an estimate first; if Ollama still
        refuses, once more against its own count, and then the refusal stands.
        """
        window = int(payload["options"]["num_ctx"])
        messages = trim_chat_history(payload["messages"], window)
        try:
            return self._post_chat({**payload, "messages": messages}, **kwargs)
        except OllamaContextOverflowError as overflow:
            retry = trim_chat_history_after_overflow(messages, overflow)
            if retry is None:
                raise
            logger.info(
                "Ollama refused %s chat messages (%s tokens, window %s); "
                "retrying with %s",
                len(messages),
                overflow.prompt_tokens,
                overflow.window,
                len(retry),
            )
            return self._post_chat({**payload, "messages": retry}, **kwargs)

    def _raise_if_truncated(self, response_metadata: dict | None) -> None:
        """Refuse a response whose answer or prompt ran out of window.

        A length stop means the answer is incomplete. The token counts are a
        fallback for servers before 0.12.6, which ignore ``truncate`` and
        ``shift``: they cut an oversized prompt to exactly num_ctx tokens and
        shift the context when the answer fills it, each with an ordinary stop.
        """
        if not response_metadata:
            return
        prompt_eval_count = response_metadata.get("prompt_eval_count")
        eval_count = response_metadata.get("eval_count")
        window = self._context_window()
        if response_metadata.get("done_reason") != "length":
            if not isinstance(prompt_eval_count, int):
                return
            # A cut prompt counts the whole window, so any answer takes the
            # total past it; so does an answer that shifted the prompt out.
            if prompt_eval_count + (eval_count or 0) <= window:
                return
        raise RuntimeError(
            "Ollama stopped because the context window was exhausted, so the "
            "answer is incomplete and was not used "
            f"(prompt_eval_count={prompt_eval_count}, eval_count={eval_count}). "
            f"{context_advice(window < self._configured_window())}"
        )

    def _get(self, path: str, **kwargs):
        return self.requests.get(
            f"{self.api_url}{path}",
            allow_redirects=False,
            **kwargs,
        )

    def _post(self, path: str, **kwargs):
        return self.requests.post(
            f"{self.api_url}{path}",
            allow_redirects=False,
            **kwargs,
        )

    def list_models(self) -> List[str]:
        try:
            resp = self._get("/api/tags", timeout=10)
            resp.raise_for_status()
            data = resp.json()
            return sorted([m["name"] for m in data.get("models", [])])
        except Exception as e:  # noqa: BLE001
            logger.error(f"Ollama API error (list models): {e}")
            return []

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
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Ollama capability probe failed for {self.model}: {e}")
            return None
        if not isinstance(capabilities, list):
            return None
        return "vision" in {str(c).lower() for c in capabilities}

    def generate_text_from_images(
        self,
        prompt: str,
        images: Sequence[VisionImage],
        timeout: int = 120,
        max_tokens: int = 8192,
    ) -> str:
        if not self.model:
            raise ValueError(
                "No Ollama model configured. Please select a model in Settings."
            )
        # Ollama takes images as bare base64 strings on the message, not as
        # typed content blocks -- no media_type is sent or wanted.
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [image.to_base64() for image in images],
                }
            ],
            "stream": False,
            "options": self._chat_options(temperature=0.0),
        }
        try:
            resp = self._post_chat(payload, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            self._raise_if_truncated(data)
            return (data.get("message") or {}).get("content", "")
        except Exception as e:  # noqa: BLE001
            if is_vision_unsupported_error(e):
                raise VisionUnsupportedError(
                    f"The selected Ollama model ({self.model}) does not accept images. "
                    "Choose a vision-capable model in Settings."
                ) from e
            logger.error(f"Ollama API error (image generation): {e}")
            raise RuntimeError(f"Ollama API error (image generation): {e}")

    def infer_speaker_suggestions(
        self,
        transcript: str,
        prompt_template: str = None,
        timeout: int = 60,
        user_notes: Optional[str] = None,
        meeting_context: Optional[MeetingEventContext] = None,
        eligible_labels: Optional[Sequence[str]] = None,
    ) -> SpeakerInferenceResult:
        prompt = self.build_speaker_suggestion_prompt(
            prompt_template,
            transcript,
            eligible_labels,
            user_notes,
            meeting_context,
        )
        if not self.model:
            raise ValueError(
                "No Ollama model configured. Please select a model in Settings."
            )

        try:
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": self._chat_options(temperature=0.3),
            }
            resp = self._post_chat(payload, timeout=timeout)
            resp.raise_for_status()
            response_json = resp.json()
            self._raise_if_truncated(response_json)
            text = response_json.get("message", {}).get("content", "")
            return self.parse_speaker_inference_result(text, eligible_labels)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Ollama API error (speaker suggestions): {e}")
            raise RuntimeError(f"Ollama API error (speaker suggestions): {e}")

    def infer_speakers(
        self,
        transcript: str,
        prompt_template: str = None,
        timeout: int = 60,
        user_notes: Optional[str] = None,
        meeting_context: Optional[MeetingEventContext] = None,
        eligible_labels: Optional[Sequence[str]] = None,
    ) -> Dict[str, str]:
        return self.infer_speaker_suggestions(
            transcript,
            prompt_template,
            timeout,
            user_notes=user_notes,
            meeting_context=meeting_context,
            eligible_labels=eligible_labels,
        ).mapping

    def generate_meeting_notes(
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
        if not self.model:
            raise ValueError(
                "No Ollama model configured. Please select a model in Settings."
            )

        try:
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": self._chat_options(temperature=0.3),
            }
            resp = self._post_chat(payload, timeout=timeout)
            resp.raise_for_status()
            response_json = resp.json()
            self._raise_if_truncated(response_json)
            text = response_json.get("message", {}).get("content", "")
            return self.finalise_meeting_notes(self.parse_notes(text), user_notes)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Ollama API error (meeting notes): {e}")
            raise RuntimeError(f"Ollama API error (meeting notes): {e}")

    def generate_meeting_intelligence(
        self,
        request: AutomaticMeetingIntelligenceRequest,
        prompt_template: str = None,
        timeout: int = 60,
    ) -> AutomaticMeetingIntelligenceResult:
        prompt = self.build_automatic_meeting_intelligence_prompt(
            request,
            prompt_template,
        )
        if not self.model:
            raise ValueError(
                "No Ollama model configured. Please select a model in Settings."
            )

        try:
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "format": "json",
                "options": self._chat_options(temperature=0.3),
            }
            resp = self._post_chat(payload, timeout=timeout)
            resp.raise_for_status()
            response_json = resp.json()
            self._raise_if_truncated(response_json)
            text = response_json.get("message", {}).get("content", "")
            try:
                return self.parse_automatic_meeting_intelligence_result(text, request)
            except JSON_CONTRACT_ERRORS as parse_error:
                logger.warning(
                    "Ollama meeting intelligence response failed JSON contract; retrying repair: %s; response_shape=%s",
                    parse_error,
                    summarize_llm_response_shape(text),
                )
                repair_payload = {
                    "model": self.model,
                    "messages": [
                        {
                            "role": "user",
                            "content": self.build_json_repair_prompt(
                                original_prompt=prompt,
                                invalid_response=text,
                                validation_error=parse_error,
                            ),
                        }
                    ],
                    "stream": False,
                    "format": "json",
                    "options": self._chat_options(temperature=0.0),
                }
                repair_resp = self._post_chat(repair_payload, timeout=timeout)
                repair_resp.raise_for_status()
                repair_json = repair_resp.json()
                self._raise_if_truncated(repair_json)
                repair_text = repair_json.get("message", {}).get("content", "")
                return self.parse_automatic_meeting_intelligence_result(
                    repair_text, request
                )
        except Exception as e:  # noqa: BLE001
            logger.error(f"Ollama API error (meeting intelligence): {e}")
            raise RuntimeError(f"Ollama API error (meeting intelligence): {e}")

    def generate_text(
        self,
        prompt: str,
        timeout: int = 60,
        max_tokens: int = 4096,
    ) -> str:
        if not self.model:
            raise ValueError(
                "No Ollama model configured. Please select a model in Settings."
            )
        try:
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": self._chat_options(temperature=0.3),
            }
            resp = self._post_chat(payload, timeout=timeout)
            resp.raise_for_status()
            response_json = resp.json()
            self._raise_if_truncated(response_json)
            return response_json.get("message", {}).get("content", "")
        except Exception as e:  # noqa: BLE001
            logger.error(f"Ollama API error (text generation): {e}")
            raise RuntimeError(f"Ollama API error (text generation): {e}")

    def generate_meeting_edge(
        self,
        request: MeetingEdgeRequest,
        prompt_template: str = None,
        timeout: int = 60,
    ) -> MeetingEdgeResult:
        prompt = self.build_meeting_edge_prompt(request, prompt_template)
        if not self.model:
            raise ValueError(
                "No Ollama model configured. Please select a model in Settings."
            )

        try:
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "format": "json",
                "options": self._chat_options(temperature=0.3),
            }
            resp = self._post_chat(payload, timeout=timeout)
            resp.raise_for_status()
            response_json = resp.json()
            self._raise_if_truncated(response_json)
            text = response_json.get("message", {}).get("content", "")
            try:
                return self.parse_meeting_edge_result(text, request)
            except JSON_CONTRACT_ERRORS as parse_error:
                logger.warning(
                    "Ollama Meeting Edge response failed JSON contract; retrying repair: %s; response_shape=%s",
                    parse_error,
                    summarize_llm_response_shape(text),
                )
                repair_payload = {
                    "model": self.model,
                    "messages": [
                        {
                            "role": "user",
                            "content": self.build_json_repair_prompt(
                                original_prompt=prompt,
                                invalid_response=text,
                                validation_error=parse_error,
                            ),
                        }
                    ],
                    "stream": False,
                    "format": "json",
                    "options": self._chat_options(temperature=0.0),
                }
                repair_resp = self._post_chat(repair_payload, timeout=timeout)
                repair_resp.raise_for_status()
                repair_json = repair_resp.json()
                self._raise_if_truncated(repair_json)
                repair_text = repair_json.get("message", {}).get("content", "")
                return self.parse_meeting_edge_result(repair_text, request)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Ollama API error (Meeting Edge): {e}")
            raise RuntimeError(f"Ollama API error (Meeting Edge): {e}")

    def _chat_json(self, prompt: str, timeout: int) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": "json",
            "options": self._chat_options(temperature=0.2),
        }
        resp = self._post_chat(payload, timeout=timeout)
        resp.raise_for_status()
        response_json = resp.json()
        self._raise_if_truncated(response_json)
        return response_json.get("message", {}).get("content", "")

    def generate_meeting_analysis(
        self,
        request: MeetingAnalysisRequest,
        prompt_template: str = None,
        timeout: int = 300,
    ) -> MeetingAnalysisResult:
        prompt = self.build_meeting_analysis_prompt(request, prompt_template)
        if not self.model:
            raise ValueError(
                "No Ollama model configured. Please select a model in Settings."
            )

        try:
            text = self._chat_json(prompt, timeout)
            try:
                return self.parse_meeting_analysis_result(text, request)
            except JSON_CONTRACT_ERRORS as parse_error:
                # Only a broken envelope reaches here. A model that returned
                # valid JSON with unusable items has already had them dropped
                # and counted, and repairing that would be asking it to try
                # harder at inventing evidence.
                logger.warning(
                    "Ollama meeting analysis failed the JSON contract; retrying repair: %s; response_shape=%s",
                    parse_error,
                    summarize_llm_response_shape(text),
                )
                repair_text = self._chat_json(
                    self.build_json_repair_prompt(
                        original_prompt=prompt,
                        invalid_response=text,
                        validation_error=parse_error,
                    ),
                    timeout,
                )
                return self.parse_meeting_analysis_result(repair_text, request)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Ollama API error (meeting analysis): {e}")
            raise RuntimeError(f"Ollama API error (meeting analysis): {e}")

    def ask_question_about_meeting(
        self,
        user_question: str,
        meeting_notes: str,
        diarized_transcript: str,
        conversation_history: list = None,
        timeout: int = 60,
        recording_id: str = None,
    ):
        if recording_id is not None:
            diarized_transcript = self.get_mapped_transcript_for_llm(recording_id)

        prompt = self._build_chat_prompt(
            user_question, meeting_notes, diarized_transcript
        )

        messages = []
        if conversation_history:
            for msg in conversation_history:
                if msg.get("role") and msg.get("parts"):
                    # Ollama's /api/chat accepts only user/assistant/system, so
                    # normalise the Gemini-style 'model' history role to 'assistant'.
                    role = "assistant" if msg["role"] == "model" else msg["role"]
                    for part in msg["parts"]:
                        messages.append({"role": role, "content": part["text"]})
        messages.append({"role": "user", "content": prompt})

        if not self.model:
            raise ValueError(
                "No Ollama model configured. Please select a model in Settings."
            )

        try:
            payload = {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "options": self._chat_options(temperature=0.3),
            }
            resp = self._post_chat_trimming_history(payload, timeout=timeout)
            resp.raise_for_status()
            response_json = resp.json()
            self._raise_if_truncated(response_json)
            return response_json.get("message", {}).get("content", "")
        except Exception as e:  # noqa: BLE001
            logger.error(f"Ollama API error (chat): {e}")
            raise RuntimeError(f"Ollama API error (chat): {e}")

    def ask_question_streaming(
        self,
        user_question: str,
        meeting_notes: str,
        diarized_transcript: str,
        conversation_history: list = None,
        timeout: int = 60,
        recording_id: str = None,
    ) -> Generator[str, None, None]:
        if recording_id is not None:
            diarized_transcript = self.get_mapped_transcript_for_llm(recording_id)

        prompt = self._build_chat_prompt(
            user_question, meeting_notes, diarized_transcript
        )

        messages = []
        if conversation_history:
            for msg in conversation_history:
                if msg.get("role") and msg.get("parts"):
                    # Ollama's /api/chat accepts only user/assistant/system, so
                    # normalise the Gemini-style 'model' history role to 'assistant'.
                    role = "assistant" if msg["role"] == "model" else msg["role"]
                    for part in msg["parts"]:
                        messages.append({"role": role, "content": part["text"]})
        messages.append({"role": "user", "content": prompt})

        if not self.model:
            raise ValueError(
                "No Ollama model configured. Please select a model in Settings."
            )

        try:
            payload = {
                "model": self.model,
                "messages": messages,
                "stream": True,
                "options": self._chat_options(temperature=0.3),
            }
            resp = self._post_chat_trimming_history(
                payload, stream=True, timeout=timeout
            )
            resp.raise_for_status()

            import json

            final_metadata = None
            for line in resp.iter_lines():
                if line:
                    try:
                        chunk = json.loads(line)
                        if chunk.get("done"):
                            final_metadata = chunk
                        content = chunk.get("message", {}).get("content", "")
                        if content:
                            yield content
                    except json.JSONDecodeError:
                        pass
            self._raise_if_truncated(final_metadata)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Ollama API error (streaming chat): {e}")
            raise RuntimeError(f"Ollama API error (streaming chat): {e}")

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
        if not self.model:
            raise ValueError(
                "No Ollama model configured. Please select a model in Settings."
            )
        try:
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": self._chat_options(temperature=0.3),
            }
            resp = self._post_chat(payload, timeout=timeout)
            resp.raise_for_status()
            response_json = resp.json()
            self._raise_if_truncated(response_json)
            text = response_json.get("message", {}).get("content", "")
            return self.parse_title(text)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Ollama API error (meeting title): {e}")
            raise RuntimeError(f"Ollama API error (meeting title): {e}")

    def validate_api_key(self) -> bool:
        try:
            self.list_models()
            return True
        except Exception as e:  # noqa: BLE001
            logger.error(f"Ollama API validation failed: {e}")
            raise ValueError(f"Ollama API validation failed: {e}")
