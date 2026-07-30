import json
import logging
from typing import Any, Dict, Generator, List, Optional, Sequence

from backend.utils.chat_prompt import (
    anthropic_cached_system,
    build_chat_context,
    build_chat_messages,
)
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
from backend.utils.speaker_name_suggestions import (
    SpeakerInferenceResult,
)
from backend.utils.vision import VisionImage, VisionUnsupportedError

logger = logging.getLogger(__name__)

from backend.processing.llm_backends.base import (
    CHAT_MAX_OUTPUT_TOKEN_LADDER,
    NOTES_MAX_OUTPUT_TOKEN_LADDER,
    LLMBackend,
    TruncatedNotesError,
    _get_default_model_for_provider,
    is_output_ceiling_error,
    is_vision_unsupported_error,
    raise_if_output_truncated,
)


class AnthropicLLMBackend(LLMBackend):
    def __init__(self, api_key=None, model=None):
        import anthropic

        if api_key is None:
            api_key = config_manager.get("anthropic_api_key")
        if not api_key:
            raise ValueError(
                "Anthropic API key is not set. Please provide it in settings."
            )
        self.api_key = api_key
        self.model = model or _get_default_model_for_provider("anthropic")
        self.client = anthropic.Anthropic(api_key=self.api_key)

    def list_models(self) -> List[str]:
        """
        List available Anthropic models.
        """
        try:
            # Anthropic Python SDK supports models.list()
            if hasattr(self.client, "models") and hasattr(self.client.models, "list"):
                models = self.client.models.list()
                return sorted([m.id for m in models if "claude" in m.id])
            else:
                return []
        except Exception as e:  # noqa: BLE001
            logger.error(f"Anthropic API error (list models): {e}")
            return []

    def _create_with_ceiling(self, ladder: tuple[int, ...], **kwargs):
        """Run a completion at the largest output ceiling the model accepts.

        Streams rather than calling ``messages.create``, and collects the final
        message. That is not a style choice: the SDK refuses any *non-streaming*
        request whose max_tokens implies a run over ten minutes -- above roughly
        21,000 tokens it raises ValueError before sending anything. Streaming
        removes both that guard and the HTTP timeout it exists to prevent, which
        is what makes a ceiling near the model's real maximum usable at all.

        Anthropic rejects a ``max_tokens`` above the model's own maximum instead
        of clamping it, and that maximum varies by model, so the ladder steps down
        only on that specific error. A response that then stops *because* of the
        ceiling is raised rather than returned half-written.
        """
        last_error: Exception | None = None

        for max_tokens in ladder:
            try:
                with self.client.messages.stream(
                    max_tokens=max_tokens, **kwargs
                ) as stream:
                    response = stream.get_final_message()
            except Exception as exc:  # noqa: BLE001
                if not is_output_ceiling_error(exc):
                    raise
                logger.info(
                    "Model %s rejected max_tokens=%s; stepping down",
                    self.model,
                    max_tokens,
                )
                last_error = exc
                continue

            raise_if_output_truncated(
                "Anthropic", getattr(response, "stop_reason", None)
            )
            return response

        raise RuntimeError(
            f"Model {self.model} rejected every supported output limit: {last_error}"
        )

    def _create_notes_message(self, prompt: str):
        return self._create_with_ceiling(
            NOTES_MAX_OUTPUT_TOKEN_LADDER,
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )

    def _open_chat_stream(self, **kwargs):
        """Open a streaming chat request at the largest ceiling the model accepts.

        Returns the entered stream and its context manager so the caller can close
        it. The ceiling error surfaces when the request is made, before anything
        is yielded, so stepping down never replays partial output to the user.
        """
        last_error: Exception | None = None

        for max_tokens in CHAT_MAX_OUTPUT_TOKEN_LADDER:
            manager = self.client.messages.stream(max_tokens=max_tokens, **kwargs)
            try:
                return manager.__enter__(), manager
            except Exception as exc:  # noqa: BLE001
                if not is_output_ceiling_error(exc):
                    raise
                logger.info(
                    "Model %s rejected max_tokens=%s for chat; stepping down",
                    self.model,
                    max_tokens,
                )
                last_error = exc

        raise RuntimeError(
            f"Model {self.model} rejected every supported output limit: {last_error}"
        )

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
                "No Anthropic model configured. Please select a model in Settings."
            )
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            text = (
                response.content[0].text
                if hasattr(response.content[0], "text")
                else response.content[0]
            )
            return self.parse_speaker_inference_result(text, eligible_labels)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Anthropic API error (speaker suggestions): {e}")
            raise RuntimeError(f"Anthropic API error (speaker suggestions): {e}")

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
                "No Anthropic model configured. Please select a model in Settings."
            )
        try:
            # Match the unified meeting-intelligence path's ceiling so the shared
            # "be comprehensive" notes spec is not silently truncated on long meetings.
            response = self._create_notes_message(prompt)
            text = (
                response.content[0].text
                if hasattr(response.content[0], "text")
                else response.content[0]
            )
            notes = self.finalise_meeting_notes(self.parse_notes(text), user_notes)
            return notes
        except TruncatedNotesError:
            # Already a precise, user-facing message; wrapping it as a generic
            # API error would bury the one thing the user can act on.
            raise
        except Exception as e:  # noqa: BLE001
            logger.error(f"Anthropic API error (meeting notes): {e}")
            raise RuntimeError(f"Anthropic API error (meeting notes): {e}")

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
                "No Anthropic model configured. Please select a model in Settings."
            )
        try:
            response = self._create_notes_message(prompt)
            text = (
                response.content[0].text
                if hasattr(response.content[0], "text")
                else response.content[0]
            )
            return self.parse_automatic_meeting_intelligence_result(text, request)
        except TruncatedNotesError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.error(f"Anthropic API error (meeting intelligence): {e}")
            raise RuntimeError(f"Anthropic API error (meeting intelligence): {e}")

    def generate_text(
        self,
        prompt: str,
        timeout: int = 60,
        max_tokens: int = 4096,
    ) -> str:
        if not self.model:
            raise ValueError(
                "No Anthropic model configured. Please select a model in Settings."
            )
        try:
            response = self._create_with_ceiling(
                (max_tokens,),
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            return (
                response.content[0].text
                if hasattr(response.content[0], "text")
                else str(response.content[0])
            )
        except TruncatedNotesError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.error(f"Anthropic API error (text generation): {e}")
            raise RuntimeError(f"Anthropic API error (text generation): {e}")

    def generate_text_from_images(
        self,
        prompt: str,
        images: Sequence[VisionImage],
        timeout: int = 120,
        max_tokens: int = 8192,
    ) -> str:
        if not self.model:
            raise ValueError(
                "No Anthropic model configured. Please select a model in Settings."
            )
        content: list[dict] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image.media_type,
                    "data": image.to_base64(),
                },
            }
            for image in images
        ]
        # Text after the images: the instruction should be the last thing read,
        # and the images are what it refers to.
        content.append({"type": "text", "text": prompt})
        try:
            response = self._create_with_ceiling(
                (max_tokens,),
                model=self.model,
                messages=[{"role": "user", "content": content}],
                temperature=0.0,
            )
            return (
                response.content[0].text
                if hasattr(response.content[0], "text")
                else str(response.content[0])
            )
        except TruncatedNotesError:
            raise
        except Exception as e:  # noqa: BLE001
            if is_vision_unsupported_error(e):
                raise VisionUnsupportedError(
                    f"The selected Anthropic model ({self.model}) does not accept images."
                ) from e
            logger.error(f"Anthropic API error (image generation): {e}")
            raise RuntimeError(f"Anthropic API error (image generation): {e}")

    def generate_meeting_edge(
        self,
        request: MeetingEdgeRequest,
        prompt_template: str = None,
        timeout: int = 60,
    ) -> MeetingEdgeResult:
        # Split into a stable instruction prefix and the volatile per-refresh
        # context so the prefix can be cached across refreshes. The parts
        # concatenate to the same prompt, so the model output is unchanged.
        prefix, suffix = self.build_meeting_edge_prompt_parts(request, prompt_template)
        if prefix:
            user_content = [
                {
                    "type": "text",
                    "text": prefix,
                    "cache_control": {"type": "ephemeral"},
                },
                {"type": "text", "text": suffix},
            ]
        else:
            user_content = suffix
        if not self.model:
            raise ValueError(
                "No Anthropic model configured. Please select a model in Settings."
            )
        try:
            # No assistant prefill: a last-assistant-turn prefill 400s on current
            # Claude models (Opus 4.6+, Sonnet 5, Fable 5). The prompt already
            # mandates a JSON object and the tolerant parser handles any
            # fenced/prose wrapping, so raw JSON is not forced by a prefill.
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                messages=[
                    {"role": "user", "content": user_content},
                ],
                temperature=0.2,
                timeout=timeout,
            )
            text = (
                response.content[0].text
                if hasattr(response.content[0], "text")
                else response.content[0]
            )
            return self.parse_meeting_edge_result(text, request)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Anthropic API error (Meeting Edge): {e}")
            raise RuntimeError(f"Anthropic API error (Meeting Edge): {e}")

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
        # If recording_id is provided, use mapped transcript
        if recording_id is not None:
            diarized_transcript = self.get_mapped_transcript_for_llm(recording_id)

        # Put the stable meeting context in the cached system prompt (render order
        # is tools -> system -> messages), so it is reused across turns while the
        # messages array carries only the volatile history and question.
        context = build_chat_context(meeting_notes, diarized_transcript)
        messages = build_chat_messages(user_question, conversation_history)
        if not self.model:
            raise ValueError(
                "No Anthropic model configured. Please select a model in Settings."
            )
        try:
            response = self._create_with_ceiling(
                CHAT_MAX_OUTPUT_TOKEN_LADDER,
                model=self.model,
                system=anthropic_cached_system(context),
                messages=messages,
                temperature=0.2,
            )
            return (
                response.content[0].text
                if hasattr(response.content[0], "text")
                else response.content[0]
            )
        except TruncatedNotesError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.error(f"Anthropic API error (chat): {e}")
            raise RuntimeError(f"Anthropic API error (chat): {e}")

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

        # Put the stable meeting context in the cached system prompt (render order
        # is tools -> system -> messages), so it is reused across turns while the
        # messages array carries only the volatile history and question.
        context = build_chat_context(meeting_notes, diarized_transcript)
        messages = build_chat_messages(user_question, conversation_history)

        tool_definition = {
            "name": "update_meeting_notes",
            "description": "Overwrites the current meeting notes with new content. Use this whenever the user asks to modify, edit, add to, or delete parts of the meeting notes. The input should be the fully updated Markdown content of the notes.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The full, updated Markdown text of the meeting notes.",
                    }
                },
                "required": ["content"],
            },
        }

        try:
            # The ladder rather than a flat ceiling: update_meeting_notes rewrites
            # the whole notes document, which a small cap truncates mid-document.
            stream, stream_manager = self._open_chat_stream(
                model=self.model,
                system=anthropic_cached_system(context),
                messages=messages,
                temperature=0.2,
                tools=[tool_definition],
            )
            try:
                current_tool_name = None
                current_json_accum = ""

                for event in stream:
                    # Text Delta
                    if (
                        event.type == "content_block_delta"
                        and event.delta.type == "text_delta"
                    ):
                        yield event.delta.text

                    # Tool Start
                    elif (
                        event.type == "content_block_start"
                        and event.content_block.type == "tool_use"
                    ):
                        current_tool_name = event.content_block.name
                        current_json_accum = ""

                    # Tool Args Delta
                    elif (
                        event.type == "content_block_delta"
                        and event.delta.type == "input_json_delta"
                    ):
                        current_json_accum += event.delta.partial_json

                    # Tool Stop (Execute)
                    elif event.type == "content_block_stop":
                        if current_tool_name == "update_meeting_notes":
                            try:
                                args = json.loads(current_json_accum)
                                new_notes = args.get("content")
                                if new_notes and recording_id:
                                    self._update_notes_in_db(recording_id, new_notes)
                                    yield {"type": "notes_update"}
                                    yield "I have updated the meeting notes."
                            except json.JSONDecodeError:
                                logger.error(
                                    "Failed to parse tool arguments for update_meeting_notes"
                                )
                            finally:
                                current_tool_name = None
            finally:
                stream_manager.__exit__(None, None, None)

        except Exception as e:  # noqa: BLE001
            logger.error(f"Anthropic API error (streaming chat): {e}")
            raise RuntimeError(f"Anthropic API error (streaming chat): {e}")

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
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            title = self.parse_title(
                response.content[0].text
                if hasattr(response.content[0], "text")
                else response.content[0]
            )
            return title
        except Exception as e:  # noqa: BLE001
            logger.error(f"Anthropic API error (meeting title): {e}")
            raise RuntimeError(f"Anthropic API error (meeting title): {e}")

    def validate_api_key(self) -> bool:
        """
        Validate the API key by making a lightweight API call.
        Returns True if valid, raises an exception or returns False if invalid.
        """
        try:
            # Use list_models to verify key without consuming tokens
            if hasattr(self.client, "models") and hasattr(self.client.models, "list"):
                self.client.models.list()
                return True

            # Fallback if list_models not available (should not happen with recent SDK)
            if not self.model:
                raise ValueError(
                    "No Anthropic model configured. Please select a model in Settings."
                )

            # Try a minimal generation with the configured model
            self.client.messages.create(
                model=self.model,
                max_tokens=1,
                messages=[{"role": "user", "content": "Hi"}],
            )
            return True
        except Exception as e:  # noqa: BLE001
            logger.error(f"Anthropic API validation failed: {e}")
            raise ValueError(f"Anthropic API validation failed: {e}")
