import json
import logging
from typing import Any, Dict, Generator, List, Optional, Sequence

from backend.utils.chat_prompt import (
    anthropic_cached_system,
    build_chat_context,
    build_chat_messages,
)
from backend.utils.config_manager import config_manager
from backend.utils.languages import build_output_language_prompt_section
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
)
from backend.utils.speaker_name_suggestions import (
    SpeakerInferenceResult,
)

logger = logging.getLogger(__name__)

from backend.processing.llm_backends.base import (
    LLMBackend,
    _get_default_model_for_provider,
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
                # Filter for claude models
                return sorted([m.id for m in models if "claude" in m.id])
            else:
                return []
        except Exception as e:  # noqa: BLE001
            logger.error(f"Anthropic API error (list models): {e}")
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
        if prompt_template is None:
            prompt_template = self.get_speaker_suggestion_prompt_template()
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
    ) -> str:
        """
        Generate meeting notes using the provided speaker mapping. Should be called after user relabeling.
        """
        if prompt_template is None:
            prompt_template = self.get_notes_prompt_template()
        prompt = self.build_notes_prompt(
            prompt_template,
            transcript,
            speaker_mapping,
            user_notes,
            meeting_context,
            output_language_instruction,
        )
        if not self.model:
            raise ValueError(
                "No Anthropic model configured. Please select a model in Settings."
            )
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            text = (
                response.content[0].text
                if hasattr(response.content[0], "text")
                else response.content[0]
            )
            notes = self.finalise_meeting_notes(self.parse_notes(text), user_notes)
            return notes
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
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            text = (
                response.content[0].text
                if hasattr(response.content[0], "text")
                else response.content[0]
            )
            return self.parse_automatic_meeting_intelligence_result(text, request)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Anthropic API error (meeting intelligence): {e}")
            raise RuntimeError(f"Anthropic API error (meeting intelligence): {e}")

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
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=anthropic_cached_system(context),
                messages=messages,
                temperature=0.2,
            )
            return (
                response.content[0].text
                if hasattr(response.content[0], "text")
                else response.content[0]
            )
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
            with self.client.messages.stream(
                model=self.model,
                max_tokens=1024,
                system=anthropic_cached_system(context),
                messages=messages,
                temperature=0.2,
                tools=[tool_definition],
            ) as stream:
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
        if prompt_template is None:
            prompt_template = self.get_title_prompt_template()
        prompt = prompt_template.format(
            transcript=transcript,
            output_language_section=build_output_language_prompt_section(
                output_language_instruction
            ),
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
