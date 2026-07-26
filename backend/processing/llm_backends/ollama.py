import logging
from typing import Dict, Generator, List, Optional, Sequence

from backend.utils.config_manager import config_manager
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

logger = logging.getLogger(__name__)

from backend.processing.llm_backends.base import (
    JSON_CONTRACT_ERRORS,
    LLMBackend,
    summarize_llm_response_shape,
)

# Ollama's num_ctx defaults to 2048, which silently truncates meeting-length prompts.
OLLAMA_DEFAULT_NUM_CTX = 8192


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

    def _chat_options(self, *, temperature: float) -> dict[str, object]:
        # num_ctx must always be sent; an unset one lets Ollama fall back to its
        # 2048 default, which silently truncates meeting-length prompts.
        ctx = getattr(self, "context_window", None) or OLLAMA_DEFAULT_NUM_CTX
        return {"temperature": temperature, "num_ctx": int(ctx)}

    @staticmethod
    def _raise_if_truncated(response_metadata: dict | None) -> None:
        if not response_metadata or response_metadata.get("done_reason") != "length":
            return
        prompt_eval_count = response_metadata.get("prompt_eval_count")
        eval_count = response_metadata.get("eval_count")
        raise RuntimeError(
            "Ollama stopped because the context window was exhausted "
            f"(prompt_eval_count={prompt_eval_count}, eval_count={eval_count}). "
            "Increase the Ollama context window or select a model with a larger context."
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
            resp = self._post("/api/chat", json=payload, timeout=timeout)
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
            resp = self._post("/api/chat", json=payload, timeout=timeout)
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
            resp = self._post("/api/chat", json=payload, timeout=timeout)
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
                repair_resp = self._post(
                    "/api/chat", json=repair_payload, timeout=timeout
                )
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
            resp = self._post("/api/chat", json=payload, timeout=timeout)
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
            resp = self._post("/api/chat", json=payload, timeout=timeout)
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
                repair_resp = self._post(
                    "/api/chat", json=repair_payload, timeout=timeout
                )
                repair_resp.raise_for_status()
                repair_json = repair_resp.json()
                self._raise_if_truncated(repair_json)
                repair_text = repair_json.get("message", {}).get("content", "")
                return self.parse_meeting_edge_result(repair_text, request)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Ollama API error (Meeting Edge): {e}")
            raise RuntimeError(f"Ollama API error (Meeting Edge): {e}")

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
            resp = self._post("/api/chat", json=payload, timeout=timeout)
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
            resp = self._post("/api/chat", json=payload, stream=True, timeout=timeout)
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
            resp = self._post("/api/chat", json=payload, timeout=timeout)
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
