import copy
import logging
from functools import partial
from time import monotonic
from typing import Dict, Generator, List, Optional, Sequence

from requests import HTTPError

from backend.utils.chat_prompt import TranscriptExcerpts
from backend.utils.config_manager import config_manager
from backend.utils.context_window import ContextWindowExceededError
from backend.utils.long_meeting_notes import (
    PartsBudget,
    context_for_notes,
    generate_meeting_intelligence_in_parts,
    generate_notes_in_parts,
)
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
    ESTIMATED_TOKENS_PER_BYTE,
    OllamaContextOverflowError,
    chat_answer_room,
    context_advice,
    context_overflow_from_response,
    estimate_tokens,
    trim_chat_history,
    trim_chat_history_after_overflow,
)
from backend.processing.llm_backends.ollama_model_facts import OllamaModelFactsMixin

# Ollama counted the refused whole prompt, so a part's cost per byte is
# measured; this margin covers a part denser than the whole.
PARTS_MEASURED_MARGIN = 1.1
# Without a count, parts are sized at this many bytes per token. The automatic
# pipeline's "[HH:MM:SS - HH:MM:SS] SPEAKER_00:" lines run denser than the 2.6
# chat trims by: 2.4 measured on four joined AMI meetings with qwen2.5. A part
# still refused is halved.
PARTS_ESTIMATED_BYTES_PER_TOKEN = 2.2

# A live Meeting Edge call repairs a broken answer only with at least this much
# of its deadline left; any less and the repair could not finish in time.
MIN_LIVE_REPAIR_SECONDS = 5.0


class OllamaLLMBackend(OllamaModelFactsMixin, LLMBackend):
    think: bool = False  # Class level: a backend built without __init__ says no.

    def __init__(
        self,
        api_url=None,
        model=None,
        context_window: int | None = None,
        allow_private_api_url: bool = False,
        think: bool | None = None,
        trusted_api_url: str | None = None,
    ):
        import requests

        self.requests = requests
        configured_api_url = config_manager.get("ollama_api_url")
        if api_url is None:
            api_url = configured_api_url
        if not api_url:
            api_url = "http://host.docker.internal:11434"

        # Exempt from the outbound check: the primary's stored address, as
        # always, and the stored address the factory names for this backend's
        # role, such as the fallback's own (#951). Both are the operator's.
        trusted_url = configured_api_url
        role_url = str(trusted_api_url or "").rstrip("/")
        if role_url and str(api_url).rstrip("/") == role_url:
            trusted_url = trusted_api_url
        self.api_url = validate_ollama_api_url(
            api_url,
            allow_private=allow_private_api_url,
            trusted_url=trusted_url,
        )
        self.model = model or config_manager.get("ollama_model")
        self.context_window = context_window or config_manager.get(
            "ollama_context_window"
        )
        # A backend built outside the factory follows the primary's setting.
        self.think = (
            bool(config_manager.get("ollama_think")) if think is None else think
        )

    def for_live_calls(self) -> "OllamaLLMBackend":
        """A copy whose Meeting Edge repair call fits inside the first's deadline.

        Ollama is called without retries already, so the JSON repair after a
        broken answer is the one second request a live call can make.
        """
        live = copy.copy(self)
        live._live_calls = True
        return live

    def _repair_timeout(self, timeout: int, started: float) -> float | None:
        """The repair call's timeout, or None when the deadline leaves no time.

        Outside a live call the repair gets a timeout of its own, as before.
        """
        if not self._live_calls:
            return timeout
        left = timeout - (monotonic() - started)
        return left if left >= MIN_LIVE_REPAIR_SECONDS else None

    def _post_chat(self, payload: dict, **kwargs):
        """POST ``/api/chat`` with Ollama as the judge of whether it fits.

        Every chat request carries ``think`` (see ``_think_field``).
        ``truncate: false`` makes Ollama refuse a prompt longer than num_ctx
        instead of cutting it and answering from the rest; ``shift: false``
        makes an answer that fills the window stop with ``done_reason:
        "length"`` instead of shifting the prompt out. Servers before 0.12.6
        ignore both; ``_raise_if_truncated`` covers them.
        """
        payload = {
            **payload,
            "truncate": False,
            "shift": False,
            **self._think_field(),
        }
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
            if overflow is not None:
                raise overflow from e
            # The status alone does not say what went wrong; Ollama's error does.
            detail = body.get("error") if isinstance(body, dict) else None
            if isinstance(detail, str) and detail:
                raise HTTPError(f"{e}: {detail}", response=resp) from e
            raise
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
        # No prompt_tokens: prompt_eval_count leaves out a cached prompt
        # prefix, and on an older server it is the cut prompt.
        raise ContextWindowExceededError(
            "Ollama stopped because the context window was exhausted, so the "
            "answer is incomplete and was not used "
            f"(prompt_eval_count={prompt_eval_count}, eval_count={eval_count}). "
            f"{context_advice(window < self._configured_window())}",
            window=window,
            answer_incomplete=response_metadata.get("done_reason") == "length",
        )

    @staticmethod
    def _api_error(task: str, error: Exception) -> Exception:
        """What to raise for a failed call.

        A full window keeps its type, so the caller can send a smaller request
        (notes in parts, chat from excerpts) once the secondary provider has had
        the whole one. Anything else is wrapped with the task it failed.
        """
        logger.error(f"Ollama API error ({task}): {error}")
        if isinstance(error, ContextWindowExceededError):
            return error
        return RuntimeError(f"Ollama API error ({task}): {error}")

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

    def _fetch_model_names(self) -> List[str]:
        resp = self._get("/api/tags", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return sorted([m["name"] for m in data.get("models", [])])

    def list_models(self) -> List[str]:
        try:
            return self._fetch_model_names()
        except Exception as e:  # noqa: BLE001 -- boundary: listing is advisory; an unreachable server lists nothing
            logger.error(f"Ollama API error (list models): {e}")
            return []

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
        except Exception as e:  # noqa: BLE001 -- boundary: HTTP and parse errors share no base class; vision refusals are told apart, the rest become one RuntimeError
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
        except Exception as e:  # noqa: BLE001 -- boundary: HTTP and parse errors share no base class; every failure becomes one actionable error
            raise self._api_error("speaker suggestions", e)

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
            return self.finalise_meeting_notes(self.parse_notes(text))
        except Exception as e:  # noqa: BLE001 -- boundary: HTTP and parse errors share no base class; every failure becomes one actionable error
            raise self._api_error("meeting notes", e)

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
        except Exception as e:  # noqa: BLE001 -- boundary: HTTP and parse errors share no base class; every failure becomes one actionable error
            raise self._api_error("meeting intelligence", e)

    def _complete(self, prompt: str, *, timeout: int) -> str:
        """One prompt, one answer: the call long-meeting notes make for each step."""
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": self._chat_options(temperature=0.3),
        }
        try:
            response = self._post_chat(payload, timeout=timeout)
        except HTTPError as e:
            # One server error should not cost the parts already written; a
            # second is reported.
            status = getattr(e.response, "status_code", 0) or 0
            if status < 500:
                raise
            logger.warning("Ollama failed (%s); asking once more", e)
            response = self._post_chat(payload, timeout=timeout)
        response_json = response.json()
        self._raise_if_truncated(response_json)
        return response_json.get("message", {}).get("content", "")

    def _parts_budget(
        self, overflow: ContextWindowExceededError, refused_prompt: str
    ) -> PartsBudget:
        """The window, and what a prompt byte costs, from the refusal of the whole.

        With Ollama's own count (0.34 and later) the cost is measured on this
        meeting's prompt; without one it is estimated.
        """
        size = len(refused_prompt.encode("utf-8"))
        if overflow.prompt_tokens and size:
            per_byte = overflow.prompt_tokens / size * PARTS_MEASURED_MARGIN
        else:
            per_byte = 1 / PARTS_ESTIMATED_BYTES_PER_TOKEN
        window = overflow.window or self._context_window()
        return PartsBudget(window=window, tokens_per_byte=per_byte)

    def generate_meeting_notes_in_parts(  # noqa: PLR0913 - matches generate_meeting_notes
        self,
        transcript: str,
        speaker_mapping: Dict[str, str],
        *,
        overflow: ContextWindowExceededError,
        timeout: int = 60,
        user_notes: Optional[str] = None,
        meeting_context: Optional[MeetingEventContext] = None,
        output_language_instruction: Optional[str] = None,
        notes_context: Optional[NotesPromptContext] = None,
    ) -> str:
        if not self.model:
            raise ValueError(
                "No Ollama model configured. Please select a model in Settings."
            )
        refused = self.build_notes_prompt(
            None,
            transcript,
            speaker_mapping,
            user_notes,
            meeting_context,
            output_language_instruction,
            notes_context,
        )
        context = context_for_notes(
            self.mapping_to_markdown_table(speaker_mapping),
            user_notes,
            meeting_context,
            output_language_instruction,
            notes_context,
        )
        try:
            notes = generate_notes_in_parts(
                transcript,
                context,
                partial(self._complete, timeout=timeout),
                self._parts_budget(overflow, refused),
            )
            return self.finalise_meeting_notes(self.parse_notes(notes))
        except Exception as e:  # noqa: BLE001 -- boundary: HTTP and parse errors share no base class; every failure becomes one actionable error
            raise self._api_error("meeting notes in parts", e)

    def generate_meeting_intelligence_in_parts(
        self,
        request: AutomaticMeetingIntelligenceRequest,
        *,
        overflow: ContextWindowExceededError,
        timeout: int = 60,
    ) -> AutomaticMeetingIntelligenceResult:
        if not self.model:
            raise ValueError(
                "No Ollama model configured. Please select a model in Settings."
            )
        refused = self.build_automatic_meeting_intelligence_prompt(request)
        try:
            return generate_meeting_intelligence_in_parts(
                request,
                partial(self._complete, timeout=timeout),
                self._parts_budget(overflow, refused),
            )
        except Exception as e:  # noqa: BLE001 -- boundary: HTTP and parse errors share no base class; every failure becomes one actionable error
            raise self._api_error("meeting intelligence in parts", e)

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
        except Exception as e:  # noqa: BLE001 -- boundary: HTTP and parse errors share no base class; every failure becomes one actionable error
            raise self._api_error("text generation", e)

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

        started = monotonic()
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
                repair_timeout = self._repair_timeout(timeout, started)
                if repair_timeout is None:
                    raise
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
                repair_resp = self._post_chat(repair_payload, timeout=repair_timeout)
                repair_resp.raise_for_status()
                repair_json = repair_resp.json()
                self._raise_if_truncated(repair_json)
                repair_text = repair_json.get("message", {}).get("content", "")
                return self.parse_meeting_edge_result(repair_text, request)
        except Exception as e:  # noqa: BLE001 -- boundary: HTTP and parse errors share no base class; every failure becomes one actionable error
            raise self._api_error("Meeting Edge", e)

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
        except Exception as e:  # noqa: BLE001 -- boundary: HTTP and parse errors share no base class; every failure becomes one actionable error
            raise self._api_error("meeting analysis", e)

    def _chat_excerpt_bytes(
        self,
        user_question: str,
        meeting_notes: str,
        conversation_history: list | None,
    ) -> int:
        """Room for transcript excerpts in a chat prompt, in bytes.

        What the window holds after the answer, the rest of the prompt and up to
        an eighth of the window for recent turns, priced at the high estimate
        chat trimming uses.
        """
        window = self._context_window()
        rest = self._build_chat_prompt(
            user_question, meeting_notes, TranscriptExcerpts(ranges=())
        )
        turns = [
            {"content": part.get("text")}
            for message in conversation_history or []
            for part in message.get("parts") or []
        ]
        room = (
            window
            - chat_answer_room(window)
            - estimate_tokens([{"content": rest}])
            - min(estimate_tokens(turns), window // 8)
        )
        return max(0, int(room / ESTIMATED_TOKENS_PER_BYTE))

    def _fitted_chat_transcript(
        self,
        diarized_transcript,
        recording_id,
        user_question: str,
        meeting_notes: str,
        conversation_history: list | None,
    ):
        """``_chat_transcript``, with excerpts cut to the room the window leaves."""
        max_bytes = None
        if isinstance(diarized_transcript, TranscriptExcerpts):
            max_bytes = self._chat_excerpt_bytes(
                user_question, meeting_notes, conversation_history
            )
        return self._chat_transcript(
            diarized_transcript, recording_id, max_bytes=max_bytes
        )

    def ask_question_about_meeting(
        self,
        user_question: str,
        meeting_notes: str,
        diarized_transcript: str | None,
        conversation_history: list = None,
        timeout: int = 60,
        recording_id: str = None,
    ):
        diarized_transcript = self._fitted_chat_transcript(
            diarized_transcript,
            recording_id,
            user_question,
            meeting_notes,
            conversation_history,
        )

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
        except Exception as e:  # noqa: BLE001 -- boundary: HTTP and parse errors share no base class; every failure becomes one actionable error
            raise self._api_error("chat", e)

    def ask_question_streaming(
        self,
        user_question: str,
        meeting_notes: str,
        diarized_transcript: str | None,
        conversation_history: list = None,
        timeout: int = 60,
        recording_id: str = None,
    ) -> Generator[str, None, None]:
        diarized_transcript = self._fitted_chat_transcript(
            diarized_transcript,
            recording_id,
            user_question,
            meeting_notes,
            conversation_history,
        )

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
        except Exception as e:  # noqa: BLE001 -- boundary: HTTP and parse errors share no base class; every failure becomes one actionable error
            raise self._api_error("streaming chat", e)

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
        except Exception as e:  # noqa: BLE001 -- boundary: HTTP and parse errors share no base class; every failure becomes one actionable error
            raise self._api_error("meeting title", e)

    def probe_model(self, prompt: str, *, timeout: int, max_output_tokens: int) -> str:
        # The JSON chat request the Meeting Edge and analysis calls send. No
        # output cap: Ollama is local, and real calls send none.
        if not self.model:
            raise ValueError("No Ollama model configured.")
        return self._chat_json(prompt, timeout)

    def validate_api_key(self) -> bool:
        # Not list_models, which answers [] for any failure: validation has to
        # see the error, as the hosted backends' raw models.list() calls do.
        try:
            self._fetch_model_names()
            return True
        except Exception as e:  # noqa: BLE001 -- boundary: any failure means the server did not validate
            logger.error(f"Ollama API validation failed: {e}")
            raise ValueError(f"Ollama API validation failed: {e}")
