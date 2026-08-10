import json
import logging
from typing import Any, Dict, Generator, List, Optional, Sequence

from backend.utils.chat_prompt import (
    build_chat_context,
    build_chat_messages,
)
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
from backend.utils.speaker_name_suggestions import (
    SpeakerInferenceResult,
)
from backend.utils.vision import VisionImage, VisionUnsupportedError

logger = logging.getLogger(__name__)

from backend.processing.llm_backends.base import (
    LLMBackend,
    TruncatedNotesError,
    _get_default_model_for_provider,
    is_vision_unsupported_error,
    raise_if_output_truncated,
)


class OpenAILLMBackend(LLMBackend):
    def __init__(self, api_key=None, model=None):
        import openai

        if api_key is None:
            api_key = config_manager.get("openai_api_key")
        if not api_key:
            raise ValueError(
                "OpenAI API key is not set. Please provide it in settings."
            )
        self.api_key = api_key
        self.model = model or _get_default_model_for_provider("openai")
        self.client = openai.OpenAI(api_key=self.api_key)

    def list_models(self) -> List[str]:
        """
        List available OpenAI models.
        """
        try:
            models = self.client.models.list()
            # Filter for gpt models to avoid clutter (dall-e, whisper, etc)
            # Also exclude models not supported by chat endpoint if possible, but it's hard to know for sure.
            model_list = []
            for m in models:
                if "gpt" in m.id and "audio" not in m.id and "realtime" not in m.id:
                    model_list.append(m.id)
                elif "o1" in m.id:  # Add support for reasoning models
                    model_list.append(m.id)
            return sorted(model_list)
        except Exception as e:  # noqa: BLE001
            logger.error(f"OpenAI API error (list models): {e}")
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
        """
        Run speaker inference on the transcript and return structured suggestions.
        Can be called independently of meeting notes generation.
        """
        prompt = self.build_speaker_suggestion_prompt(
            prompt_template,
            transcript,
            eligible_labels,
            user_notes,
            meeting_context,
        )
        if not self.model:
            raise ValueError(
                "No OpenAI model configured. Please select a model in Settings."
            )
        try:
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                timeout=timeout,
                stream=True,
            )
            text_chunks = []
            for chunk in stream:
                if chunk.choices[0].delta.content:
                    text_chunks.append(chunk.choices[0].delta.content)
            text = "".join(text_chunks)
            return self.parse_speaker_inference_result(text, eligible_labels)
        except Exception as e:  # noqa: BLE001
            if "not a chat model" in str(e) or "404" in str(e):
                logger.error(
                    f"OpenAI API error (speaker suggestions): Invalid model {self.model}. {e}"
                )
                raise ValueError(
                    f"The model '{self.model}' appears to be invalid or is not a chat model. Please check the model name in Settings."
                )
            logger.error(f"OpenAI API error (speaker suggestions): {e}")
            raise RuntimeError(f"OpenAI API error (speaker suggestions): {e}")

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
        """
        Generate meeting notes using the provided speaker mapping. Should be called after user relabeling.
        """
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
                "No OpenAI model configured. Please select a model in Settings."
            )
        try:
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                timeout=timeout,
                stream=True,
            )
            text_chunks = []
            finish_reason = None
            for chunk in stream:
                if chunk.choices[0].delta.content:
                    text_chunks.append(chunk.choices[0].delta.content)
                # Arrives on the final chunk; "length" means the output limit cut
                # the notes off mid-sentence.
                finish_reason = chunk.choices[0].finish_reason or finish_reason
            raise_if_output_truncated("OpenAI", finish_reason)
            text = "".join(text_chunks)
            notes = self.finalise_meeting_notes(self.parse_notes(text), user_notes)
            return notes
        except TruncatedNotesError:
            raise
        except Exception as e:  # noqa: BLE001
            if "not a chat model" in str(e) or "404" in str(e):
                logger.error(
                    f"OpenAI API error (meeting notes): Invalid model {self.model}. {e}"
                )
                raise ValueError(
                    f"The model '{self.model}' appears to be invalid or is not a chat model. Please check the model name in Settings."
                )
            logger.error(f"OpenAI API error (meeting notes): {e}")
            raise RuntimeError(f"OpenAI API error (meeting notes): {e}")

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
                "No OpenAI model configured. Please select a model in Settings."
            )
        request_kwargs = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "timeout": timeout,
        }
        if self.model.startswith("gpt") or "gpt" in self.model:
            request_kwargs["temperature"] = 0.2
        try:
            response = self.client.chat.completions.create(**request_kwargs)
            raise_if_output_truncated(
                "OpenAI", getattr(response.choices[0], "finish_reason", None)
            )
            text = response.choices[0].message.content or ""
            return self.parse_automatic_meeting_intelligence_result(text, request)
        except TruncatedNotesError:
            raise
        except Exception as e:  # noqa: BLE001
            if "not a chat model" in str(e) or "404" in str(e):
                logger.error(
                    f"OpenAI API error (meeting intelligence): Invalid model {self.model}. {e}"
                )
                raise ValueError(
                    f"The model '{self.model}' appears to be invalid or is not a chat model. Please check the model name in Settings."
                )
            logger.error(f"OpenAI API error (meeting intelligence): {e}")
            raise RuntimeError(f"OpenAI API error (meeting intelligence): {e}")

    def generate_text(
        self,
        prompt: str,
        timeout: int = 60,
        max_tokens: int = 4096,
    ) -> str:
        if not self.model:
            raise ValueError(
                "No OpenAI model configured. Please select a model in Settings."
            )
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                timeout=timeout,
            )
            return response.choices[0].message.content or ""
        except Exception as e:  # noqa: BLE001
            logger.error(f"OpenAI API error (text generation): {e}")
            raise RuntimeError(f"OpenAI API error (text generation): {e}")

    def generate_text_from_images(
        self,
        prompt: str,
        images: Sequence[VisionImage],
        timeout: int = 120,
        max_tokens: int = 8192,
    ) -> str:
        if not self.model:
            raise ValueError(
                "No OpenAI model configured. Please select a model in Settings."
            )
        content: list[dict] = [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{image.media_type};base64,{image.to_base64()}"
                },
            }
            for image in images
        ]
        content.append({"type": "text", "text": prompt})
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": content}],
                timeout=timeout,
            )
            return response.choices[0].message.content or ""
        except Exception as e:  # noqa: BLE001
            if is_vision_unsupported_error(e):
                raise VisionUnsupportedError(
                    f"The selected OpenAI model ({self.model}) does not accept images."
                ) from e
            logger.error(f"OpenAI API error (image generation): {e}")
            raise RuntimeError(f"OpenAI API error (image generation): {e}")

    def generate_meeting_edge(
        self,
        request: MeetingEdgeRequest,
        prompt_template: str = None,
        timeout: int = 60,
    ) -> MeetingEdgeResult:
        prompt = self.build_meeting_edge_prompt(request, prompt_template)
        if not self.model:
            raise ValueError(
                "No OpenAI model configured. Please select a model in Settings."
            )

        request_kwargs = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "timeout": timeout,
        }
        if self.model.startswith("gpt") or "gpt" in self.model:
            request_kwargs["temperature"] = 0.2

        try:
            try:
                response = self.client.chat.completions.create(
                    **request_kwargs,
                    response_format={"type": "json_object"},
                )
            except Exception as json_mode_error:  # noqa: BLE001
                # OpenAI-compatible endpoints may reject response_format; retry without it.
                logger.warning(
                    "OpenAI JSON mode failed for Meeting Edge (%s); retrying without response_format.",
                    json_mode_error,
                )
                response = self.client.chat.completions.create(**request_kwargs)
            text = response.choices[0].message.content or ""
            return self.parse_meeting_edge_result(text, request)
        except Exception as e:  # noqa: BLE001
            if "not a chat model" in str(e) or "404" in str(e):
                logger.error(
                    f"OpenAI API error (Meeting Edge): Invalid model {self.model}. {e}"
                )
                raise ValueError(
                    f"The model '{self.model}' appears to be invalid or is not a chat model. Please check the model name in Settings."
                )
            logger.error(f"OpenAI API error (Meeting Edge): {e}")
            raise RuntimeError(f"OpenAI API error (Meeting Edge): {e}")

    def generate_meeting_analysis(
        self,
        request: MeetingAnalysisRequest,
        prompt_template: str = None,
        timeout: int = 300,
    ) -> MeetingAnalysisResult:
        prompt = self.build_meeting_analysis_prompt(request, prompt_template)
        if not self.model:
            raise ValueError(
                "No OpenAI model configured. Please select a model in Settings."
            )

        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "timeout": timeout,
        }
        if self.model.startswith("gpt") or "gpt" in self.model:
            request_kwargs["temperature"] = 0.2

        try:
            try:
                response = self.client.chat.completions.create(
                    **request_kwargs,
                    response_format={"type": "json_object"},
                )
            except Exception as json_mode_error:  # noqa: BLE001
                # OpenAI-compatible endpoints may reject response_format; the
                # prompt already mandates a bare JSON object and the parser is
                # tolerant of fencing, so a plain retry is worth one round trip.
                logger.warning(
                    "OpenAI JSON mode failed for meeting analysis (%s); retrying without response_format.",
                    json_mode_error,
                )
                response = self.client.chat.completions.create(**request_kwargs)
            text = response.choices[0].message.content or ""
            return self.parse_meeting_analysis_result(text, request)
        except Exception as e:  # noqa: BLE001
            logger.error(f"OpenAI API error (meeting analysis): {e}")
            raise RuntimeError(f"OpenAI API error (meeting analysis): {e}")

    # infer_speakers_and_generate_notes is inherited and calls the above two methods

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

        # Lead with the stable meeting context as a system message so OpenAI's
        # automatic prefix caching can reuse it across turns; the volatile
        # question is sent last.
        context = build_chat_context(meeting_notes, diarized_transcript)
        messages = [{"role": "system", "content": context}] + build_chat_messages(
            user_question, conversation_history
        )
        if not self.model:
            raise ValueError(
                "No OpenAI model configured. Please select a model in Settings."
            )
        try:
            response = self.client.chat.completions.create(
                model=self.model, messages=messages, temperature=0.2, timeout=timeout
            )
            return response.choices[0].message.content
        except Exception as e:  # noqa: BLE001
            if "not a chat model" in str(e) or "404" in str(e):
                logger.error(
                    f"OpenAI API error (chat): Invalid model {self.model}. {e}"
                )
                raise ValueError(
                    f"The model '{self.model}' appears to be invalid or is not a chat model. Please check the model name in Settings."
                )
            logger.error(f"OpenAI API error (chat): {e}")
            raise RuntimeError(f"OpenAI API error (chat): {e}")

    def ask_question_streaming(
        self,
        user_question: str,
        meeting_notes: str,
        diarized_transcript: str,
        conversation_history: list = None,
        timeout: int = 60,
        recording_id: str = None,
    ) -> Generator[Any, None, None]:
        if recording_id is not None:
            diarized_transcript = self.get_mapped_transcript_for_llm(recording_id)

        # Lead with the stable meeting context as a system message so OpenAI's
        # automatic prefix caching can reuse it across turns; the volatile
        # question is sent last.
        context = build_chat_context(meeting_notes, diarized_transcript)
        messages = [{"role": "system", "content": context}] + build_chat_messages(
            user_question, conversation_history
        )

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "update_meeting_notes",
                    "description": "Overwrites the current meeting notes with new content. Use this whenever the user asks to modify, edit, add to, or delete parts of the meeting notes. The input should be the fully updated Markdown content of the notes.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "content": {
                                "type": "string",
                                "description": "The full, updated Markdown text of the meeting notes.",
                            }
                        },
                        "required": ["content"],
                    },
                },
            }
        ]

        # Prepare request arguments
        request_kwargs = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "timeout": timeout,
            "tools": tools,
        }

        # Skip temperature for reasoning models (OpenAI) that enforce default temperature
        if self.model.startswith("gpt") or "gpt" in self.model:
            request_kwargs["temperature"] = 0.2

        try:
            stream = self.client.chat.completions.create(**request_kwargs)

            tool_calls_accumulator = {}

            for chunk in stream:
                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta

                if delta.content is not None:
                    yield delta.content

                if delta.tool_calls:
                    for tool_part in delta.tool_calls:
                        idx = tool_part.index
                        if idx not in tool_calls_accumulator:
                            tool_calls_accumulator[idx] = {"name": "", "arguments": ""}

                        if tool_part.function and tool_part.function.name:
                            tool_calls_accumulator[idx]["name"] += (
                                tool_part.function.name
                            )

                        if tool_part.function and tool_part.function.arguments:
                            tool_calls_accumulator[idx]["arguments"] += (
                                tool_part.function.arguments
                            )

            # Process accumulated tool calls
            for idx, tool_data in tool_calls_accumulator.items():
                if tool_data["name"] == "update_meeting_notes":
                    try:
                        args = json.loads(tool_data["arguments"])
                        new_notes = args.get("content")
                        if new_notes and recording_id:
                            self._update_notes_in_db(recording_id, new_notes)
                            yield {"type": "notes_update"}
                            yield "I have updated the meeting notes."
                    except json.JSONDecodeError:
                        logger.error(
                            "Failed to parse tool arguments for update_meeting_notes"
                        )

        except Exception as e:  # noqa: BLE001
            if "not a chat model" in str(e) or "404" in str(e):
                logger.error(
                    f"OpenAI API error (streaming chat): Invalid model {self.model}. {e}"
                )
                raise ValueError(
                    f"The model '{self.model}' appears to be invalid or is not a chat model. Please check the model name in Settings."
                )
            logger.error(f"OpenAI API error (streaming chat): {e}")
            raise RuntimeError(f"OpenAI API error (streaming chat): {e}")

    def infer_meeting_title(
        self,
        transcript: str,
        prompt_template: str = None,
        timeout: int = 60,
        output_language_instruction: Optional[str] = None,
    ) -> str:
        """
        Infer a concise, descriptive meeting title from the provided transcript.
        Sub-classes must implement.
        """
        prompt = self.build_title_prompt(
            prompt_template,
            transcript,
            output_language_instruction,
        )
        if not self.model:
            raise ValueError(
                "No OpenAI model configured. Please select a model in Settings."
            )
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                timeout=timeout,
            )
            title = self.parse_title(response.choices[0].message.content)
            return title
        except Exception as e:  # noqa: BLE001
            if "not a chat model" in str(e) or "404" in str(e):
                logger.error(
                    f"OpenAI API error (meeting title): Invalid model {self.model}. {e}"
                )
                raise ValueError(
                    f"The model '{self.model}' appears to be invalid or is not a chat model. Please check the model name in Settings."
                )
            logger.error(f"OpenAI API error (meeting title): {e}")
            raise RuntimeError(f"OpenAI API error (meeting title): {e}")

    def validate_api_key(self) -> bool:
        """
        Validate the API key by making a lightweight API call.
        Returns True if valid, raises an exception or returns False if invalid.
        """
        try:
            # Simple call to list models to verify key
            self.client.models.list()
            return True
        except Exception as e:  # noqa: BLE001
            logger.error(f"OpenAI API validation failed: {e}")
            raise ValueError(f"OpenAI API validation failed: {e}")
