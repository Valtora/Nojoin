import logging
from typing import Any, Dict, Generator, List, Optional, Sequence

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

logger = logging.getLogger(__name__)

from backend.processing.llm_backends.base import (
    LLMBackend,
    TruncatedNotesError,
    _get_default_model_for_provider,
    raise_if_output_truncated,
)


class GeminiLLMBackend(LLMBackend):
    def __init__(self, api_key=None, model=None):
        # Lazy import to avoid errors when google-genai isn't installed
        try:
            from google import genai
        except ImportError:
            raise ImportError(
                "The 'google-genai' package is required for Gemini support. "
                "Please install it with: pip install google-genai"
            )

        if api_key is None:
            api_key = config_manager.get("gemini_api_key")
        if not api_key:
            raise ValueError(
                "Google Gemini API key is not set. Please provide it in settings."
            )
        self.api_key = api_key
        self.model = model or _get_default_model_for_provider("gemini")
        self.genai = genai  # Store reference for later use
        self.client = genai.Client(api_key=self.api_key)

    @staticmethod
    def _raise_if_truncated(response) -> None:
        """Fail rather than save notes the output limit cut short."""
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return
        finish_reason = getattr(candidates[0], "finish_reason", None)
        raise_if_output_truncated(
            "Gemini", getattr(finish_reason, "name", finish_reason)
        )

    def _extract_text_from_response(self, response):
        """
        Extract text from the response, handling potential non-text parts (like thoughts)
        to avoid warnings and ensure text extraction.
        """
        if hasattr(response, "candidates") and response.candidates:
            candidate = response.candidates[0]
            if (
                hasattr(candidate, "content")
                and candidate.content
                and hasattr(candidate.content, "parts")
            ):
                text_parts = []
                for part in candidate.content.parts:
                    if hasattr(part, "text") and part.text:
                        text_parts.append(part.text)
                if text_parts:
                    return "".join(text_parts)

        # Fallback to .text if available (which might log the warning but works)
        if hasattr(response, "text"):
            return response.text
        return ""

    def list_models(self) -> List[str]:
        """
        List available Gemini models.
        """
        try:
            models = self.client.models.list()
            # Extract model IDs (e.g. 'models/gemini-pro') from the API response.
            model_list = []
            for m in models:
                # Check attributes
                name = getattr(m, "name", None)
                if name:
                    # Strip 'models/' prefix if present, as the client usually handles it or expects it.
                    # But for display we might want the full name or short name.
                    # The user example showed 'gemini-flash-latest'.
                    if name.startswith("models/"):
                        name = name[7:]

                    # Filter for gemini models
                    if "gemini" in name.lower():
                        model_list.append(name)

            return sorted(model_list)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Gemini API error (list models): {e}")
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
                "No Gemini model configured. Please select a model in Settings."
            )
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
            )
            text = self._extract_text_from_response(response)
            return self.parse_speaker_inference_result(text, eligible_labels)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Gemini API error (speaker suggestions): {e}")
            raise RuntimeError(f"Gemini API error (speaker suggestions): {e}")

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
                "No Gemini model configured. Please select a model in Settings."
            )
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
            )
            self._raise_if_truncated(response)
            text = self._extract_text_from_response(response)
            notes = self.finalise_meeting_notes(self.parse_notes(text), user_notes)
            return notes
        except TruncatedNotesError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.error(f"Gemini API error (meeting notes): {e}")
            raise RuntimeError(f"Gemini API error (meeting notes): {e}")

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
                "No Gemini model configured. Please select a model in Settings."
            )
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
            )
            self._raise_if_truncated(response)
            text = self._extract_text_from_response(response)
            return self.parse_automatic_meeting_intelligence_result(text, request)
        except TruncatedNotesError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.error(f"Gemini API error (meeting intelligence): {e}")
            raise RuntimeError(f"Gemini API error (meeting intelligence): {e}")

    def generate_text(
        self,
        prompt: str,
        timeout: int = 60,
        max_tokens: int = 4096,
    ) -> str:
        if not self.model:
            raise ValueError(
                "No Gemini model configured. Please select a model in Settings."
            )
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
            )
            return self._extract_text_from_response(response)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Gemini API error (text generation): {e}")
            raise RuntimeError(f"Gemini API error (text generation): {e}")

    def generate_meeting_edge(
        self,
        request: MeetingEdgeRequest,
        prompt_template: str = None,
        timeout: int = 60,
    ) -> MeetingEdgeResult:
        prompt = self.build_meeting_edge_prompt(request, prompt_template)
        if not self.model:
            raise ValueError(
                "No Gemini model configured. Please select a model in Settings."
            )
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=self.genai.types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                    http_options=self.genai.types.HttpOptions(
                        timeout=int(timeout * 1000),
                    ),
                ),
            )
            text = self._extract_text_from_response(response)
            return self.parse_meeting_edge_result(text, request)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Gemini API error (Meeting Edge): {e}")
            raise RuntimeError(f"Gemini API error (Meeting Edge): {e}")

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

        prompt = self._build_chat_prompt(
            user_question, meeting_notes, diarized_transcript
        )

        contents = []
        if conversation_history:
            contents.extend(conversation_history)
        contents.append({"role": "user", "parts": [{"text": prompt}]})
        if not self.model:
            raise ValueError(
                "No Gemini model configured. Please select a model in Settings."
            )
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
            )
            return self._extract_text_from_response(response)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Gemini API error (chat): {e}")
            raise RuntimeError(f"Gemini API error (chat): {e}")

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

        prompt = self._build_chat_prompt(
            user_question, meeting_notes, diarized_transcript
        )

        contents = []
        if conversation_history:
            contents.extend(conversation_history)
        contents.append({"role": "user", "parts": [{"text": prompt}]})

        if not self.model:
            raise ValueError(
                "No Gemini model configured. Please select a model in Settings."
            )

        # Define Tool
        def update_meeting_notes(content: str):
            """Overwrites the current meeting notes with new content. Use this whenever the user asks to modify, edit, add to, or delete parts of the meeting notes. The input should be the fully updated Markdown content of the notes."""
            pass

        tools = [update_meeting_notes]

        try:
            # Use streaming API
            # Automatic function calling is disabled to allow manual handling and event streaming.
            response_stream = self.client.models.generate_content_stream(
                model=self.model,
                contents=contents,
                config=self.genai.types.GenerateContentConfig(
                    tools=tools,
                    automatic_function_calling=self.genai.types.AutomaticFunctionCallingConfig(
                        disable=True
                    ),
                ),
            )
            for chunk in response_stream:
                if chunk.function_calls:
                    for fc in chunk.function_calls:
                        if fc.name == "update_meeting_notes":
                            new_notes = fc.args.get("content")
                            if new_notes and recording_id:
                                self._update_notes_in_db(recording_id, new_notes)
                                yield {"type": "notes_update"}
                                # Send a confirmation message back to the user since we are not doing a full round-trip
                                yield "I have updated the meeting notes."

                # Safely extract text to avoid warnings about non-text parts
                try:
                    # Check if there is actual text content before accessing .text
                    has_text = False
                    if hasattr(chunk, "candidates") and chunk.candidates:
                        candidate = chunk.candidates[0]
                        if (
                            hasattr(candidate, "content")
                            and candidate.content
                            and hasattr(candidate.content, "parts")
                        ):
                            for part in candidate.content.parts:
                                if hasattr(part, "text") and part.text:
                                    has_text = True
                                    break

                    if has_text and chunk.text:
                        yield chunk.text
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            logger.error(f"Gemini API error (streaming chat): {e}")
            # If it's just a property access error because of no text, ignore it
            if "Candidate was blocked" in str(e) or "has no parts" in str(e):
                pass
            else:
                raise RuntimeError(f"Gemini API error (streaming chat): {e}")

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
                "No Gemini model configured. Please select a model in Settings."
            )
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
            )
            text = self._extract_text_from_response(response)
            title = self.parse_title(text)
            return title
        except Exception as e:  # noqa: BLE001
            logger.error(f"Gemini API error (meeting title): {e}")
            raise RuntimeError(f"Gemini API error (meeting title): {e}")

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
            logger.error(f"Gemini API validation failed: {e}")
            raise ValueError(f"Gemini API validation failed: {e}")
