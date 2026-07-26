import logging
import re
from typing import Any, Dict, Generator, List, Optional, Sequence, Tuple

from backend.utils.chat_prompt import (
    build_chat_prompt,
)
from backend.utils.languages import build_output_language_prompt_section
from backend.utils.meeting_edge import (
    MeetingEdgeContractError,
    MeetingEdgeRequest,
    MeetingEdgeResult,
)
from backend.utils.meeting_edge import (
    build_meeting_edge_prompt as build_meeting_edge_prompt_text,
)
from backend.utils.meeting_edge import (
    build_meeting_edge_prompt_parts as build_meeting_edge_prompt_parts_text,
)
from backend.utils.meeting_edge import (
    parse_meeting_edge_response as parse_meeting_edge_payload,
)
from backend.utils.meeting_intelligence import (
    AutomaticMeetingIntelligenceRequest,
    AutomaticMeetingIntelligenceResult,
    MeetingIntelligenceContractError,
    build_title_preference_instruction,
)
from backend.utils.meeting_intelligence import (
    build_automatic_meeting_intelligence_prompt as build_automatic_meeting_intelligence_prompt_text,
)
from backend.utils.meeting_intelligence import (
    finalise_automatic_meeting_intelligence_result as finalise_automatic_meeting_intelligence_payload,
)
from backend.utils.meeting_intelligence import (
    parse_automatic_meeting_intelligence_response as parse_automatic_meeting_intelligence_payload,
)
from backend.utils.meeting_notes import (
    MeetingEventContext,
    NotesPromptContext,
    append_user_notes_section,
    build_glossary_prompt_section,
    build_meeting_context_prompt_section,
    build_meeting_metadata_prompt_section,
    build_notes_body_spec,
    build_user_notes_prompt_section,
    strip_leading_title_heading,
)
from backend.utils.prompt_blocks import render_prompt_blocks
from backend.utils.speaker_name_suggestions import (
    SpeakerInferenceResult,
    parse_speaker_inference_response,
)

logger = logging.getLogger(__name__)

JSON_CONTRACT_ERRORS = (MeetingIntelligenceContractError, MeetingEdgeContractError)

# Output ceiling for the two note-producing calls, tried in order.
#
# Only Anthropic needs this: max_tokens is a required parameter of the Messages
# API. OpenAI, Gemini and Ollama are sent no output cap at all, so each model's
# own maximum applies -- setting a number there would *lower* them, and on
# OpenAI's reasoning models max_tokens is rejected outright in favour of
# max_completion_tokens. The asymmetry is deliberate, not an oversight.
#
# Anthropic rejects a value above the model's own maximum rather than clamping
# it, and the maximum varies by model, so the ladder asks for the largest first
# and steps down on that specific error. 128k is the real ceiling on current
# Claude models (Opus 5, Fable 5, Opus 4.8/4.7/4.6, Sonnet 5, Sonnet 4.6); 64k
# covers Haiku 4.5 and the 4.x generation, then 8192 and 4096 for older models.
# A rejection costs one failed round trip, not a generation.
#
# Reaching these values requires streaming: the SDK refuses a *non-streaming*
# request whose max_tokens implies a run over ten minutes, which is anything
# above roughly 21,000 tokens. See AnthropicLLMBackend._create_with_ceiling.
NOTES_MAX_OUTPUT_TOKEN_LADDER = (128_000, 64_000, 8_192, 4_096)
NOTES_MAX_OUTPUT_TOKENS = NOTES_MAX_OUTPUT_TOKEN_LADDER[0]

# Meeting Chat uses the same ladder. Most chat answers are short, but the chat
# tool `update_meeting_notes` rewrites the *entire* notes document, so a low cap
# silently truncated a rewrite of any real length -- the old 1024 was roughly 750
# words for a whole set of notes.
CHAT_MAX_OUTPUT_TOKEN_LADDER = NOTES_MAX_OUTPUT_TOKEN_LADDER


def is_output_ceiling_error(exc: Exception) -> bool:
    """Whether a provider error is "max_tokens is larger than this model allows"."""
    message = str(exc).lower()
    if "max_tokens" not in message:
        return False
    return any(
        marker in message
        for marker in ("greater than", "less than or equal", "maximum", "at most")
    )


class TruncatedNotesError(RuntimeError):
    """Raised when a provider stopped generating because it hit its output cap.

    Truncated notes look plausible and end mid-sentence, so saving them is worse
    than failing: the user has no way to tell a short meeting from a cut-off one.
    Ollama already refused to save truncated output; this makes the other three
    providers behave the same way.
    """

    def __init__(self, provider: str):
        super().__init__(
            f"{provider} stopped generating because it reached its output limit, "
            "so the notes would have been cut off mid-sentence. Try a notes "
            "structure that asks for less detail, or a model with a larger "
            "output limit."
        )


def raise_if_output_truncated(provider: str, stop_reason: Optional[str]) -> None:
    """Fail loudly when a response was cut short by the output limit.

    Accepts each provider's own vocabulary for the same condition: Anthropic's
    ``max_tokens``, OpenAI's ``length``, and Gemini's ``MAX_TOKENS``.
    """
    if not stop_reason:
        return
    if str(stop_reason).strip().lower() in {"max_tokens", "length", "max_token"}:
        raise TruncatedNotesError(provider)


def summarize_llm_response_shape(response_text: str) -> Dict[str, Any]:
    """Return non-content diagnostics for malformed structured LLM responses."""
    stripped = response_text.strip()
    return {
        "chars": len(response_text),
        "starts_with": stripped[:1],
        "ends_with": stripped[-1:] if stripped else "",
        "first_brace": response_text.find("{"),
        "last_brace": response_text.rfind("}"),
        "has_fence": "```" in response_text,
        "has_think_tag": "<think>" in response_text or "</think>" in response_text,
    }


def build_eligible_speaker_labels_prompt_section(
    eligible_labels: Optional[Sequence[str]],
) -> str:
    labels = [
        str(label).strip() for label in eligible_labels or () if str(label).strip()
    ]
    if not labels:
        return "All unresolved diarization labels in the transcript are eligible."

    lines = ["Only return suggestions for these diarization labels:"]
    lines.extend(f"- {label}" for label in labels)
    return "\n".join(lines)


class LLMBackend:
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
        Infer evidence-backed name suggestions for unresolved diarization labels.
        """
        raise NotImplementedError

    def infer_speakers(
        self,
        transcript: str,
        prompt_template: str = None,
        timeout: int = 60,
        user_notes: Optional[str] = None,
        meeting_context: Optional[MeetingEventContext] = None,
        eligible_labels: Optional[Sequence[str]] = None,
    ) -> Dict[str, str]:
        """
        Infer the most likely real names or roles for each speaker label in the transcript.
        Returns a mapping from diarization label to inferred name/role.
        """
        result = self.infer_speaker_suggestions(
            transcript,
            prompt_template,
            timeout,
            user_notes=user_notes,
            meeting_context=meeting_context,
            eligible_labels=eligible_labels,
        )
        return result.mapping

    def list_models(self) -> List[str]:
        """
        List available models for the provider.
        """
        raise NotImplementedError

    def generate_meeting_notes(  # noqa: PLR0913 - matches the prompt-section arguments
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
        Generate meeting notes using the provided speaker mapping to replace generic labels.
        Returns the meeting notes as a string.
        """
        raise NotImplementedError

    def generate_meeting_intelligence(
        self,
        request: AutomaticMeetingIntelligenceRequest,
        prompt_template: str = None,
        timeout: int = 60,
    ) -> AutomaticMeetingIntelligenceResult:
        """
        Generate speaker suggestions for unresolved labels, a meeting title, and
        meeting notes from a single LLM call.
        """
        raise NotImplementedError

    def generate_text(
        self,
        prompt: str,
        timeout: int = 60,
        max_tokens: int = 4096,
    ) -> str:
        """One prompt in, raw text out.

        The generic primitive the task-specific methods above are missing: used
        by features that supply their own prompt and their own parser (the
        notes-structure generator, issue #137).
        """
        raise NotImplementedError

    def generate_meeting_edge(
        self,
        request: MeetingEdgeRequest,
        prompt_template: str = None,
        timeout: int = 60,
    ) -> MeetingEdgeResult:
        """
        Generate live Meeting Edge guidance from the current meeting context.
        """
        raise NotImplementedError

    def infer_speakers_and_generate_notes(
        self,
        transcript: str,
        prompt_template: str = None,
        timeout: int = 60,
        user_notes: Optional[str] = None,
    ) -> Tuple[Dict[str, str], str]:
        """
        Backward-compatible method: infers speakers and generates notes in sequence using the new methods.
        """
        mapping = self.infer_speakers(
            transcript,
            prompt_template,
            timeout,
            user_notes=user_notes,
        )
        notes = self.generate_meeting_notes(
            transcript, mapping, prompt_template, timeout, user_notes=user_notes
        )
        return mapping, notes

    def ask_question_about_meeting(
        self,
        user_question: str,
        meeting_notes: str,
        diarized_transcript: str,
        conversation_history: list = None,
        timeout: int = 60,
        recording_id: str = None,
    ):
        """
        Ask a question about the meeting.
        """
        raise NotImplementedError

    def ask_question_streaming(
        self,
        user_question: str,
        meeting_notes: str,
        diarized_transcript: str,
        conversation_history: list = None,
        timeout: int = 60,
        recording_id: str = None,
    ) -> Generator[str, None, None]:
        """
        Ask a question about the meeting and yield response chunks.
        """
        raise NotImplementedError

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
        raise NotImplementedError

    def validate_api_key(self) -> bool:
        """
        Validate the API key by making a lightweight API call.
        Returns True if valid, raises an exception or returns False if invalid.
        """
        raise NotImplementedError

    def _build_chat_prompt(
        self, user_question: str, meeting_notes: str, diarized_transcript: str
    ) -> str:
        return build_chat_prompt(user_question, meeting_notes, diarized_transcript)

    # Static prompt text. Plain strings, never format templates: JSON schema
    # examples use real braces, and interpolated values need no escaping.
    SPEAKER_SUGGESTION_INTRO = """You are an expert meeting assistant. Analyze the diarized meeting transcript below and return only evidence-backed speaker name suggestions for unresolved diarization labels."""

    SPEAKER_SUGGESTION_CRITICAL_RULES = """- Return valid JSON only. Do not include Markdown, commentary, or code fences unless the client asks for them.
- Only include suggestions for the eligible diarization labels listed below.
- Omit a label entirely if the transcript evidence is weak or ambiguous.
- Each suggestion must include at least one direct evidence span quoted from the transcript.
- Prefer names already present in the transcript or linked meeting attendees. Do not invent names.
- Be conservative. If you are unsure, omit the suggestion instead of guessing.
- `confidence` must be between 0.0 and 1.0."""

    SPEAKER_SUGGESTION_JSON_SCHEMA = """{
    "suggestions": [
        {
            "diarization_label": "SPEAKER_00",
            "suggested_name": "Alex Johnson",
            "confidence": 0.93,
            "rationale": "The speaker says 'I'm Alex' and the attendee list contains Alex Johnson.",
            "signals": ["self_introduction", "meeting_attendee_exact"],
            "evidence_spans": [
                {
                    "quote": "Hi everyone, I'm Alex from product.",
                    "reason": "self_introduction",
                    "start_seconds": 0.0,
                    "end_seconds": 3.2
                }
            ]
        }
    ]
}"""

    NOTES_INTRO = """You are an expert meeting-notes assistant. Generate meeting notes from the transcript below. Use the speaker mapping to refer to participants by their inferred names or roles instead of generic labels."""

    NOTES_CLOSING = """Return only the meeting notes described above, starting directly with the first section and with no preamble or closing commentary."""

    @staticmethod
    def parse_notes(response_text: str) -> str:
        # Assume notes start after the mapping table (after a blank line or after '# Meeting Notes')
        lines = [line for line in response_text.splitlines()]
        notes_lines = []
        in_notes = False
        for line in lines:
            if line.strip().startswith("# Meeting Notes"):
                in_notes = True
            if in_notes:
                notes_lines.append(line)

        result = "\n".join(notes_lines).strip()
        if not result:
            # Fallback: if no header found, return everything.
            # This handles cases where the prompt is modified or the LLM disobeys.
            return response_text.strip()
        return result

    @staticmethod
    def parse_title(response_text: str) -> str:
        # Take first non-empty line, strip spurious characters/quotes/markdown
        for line in response_text.splitlines():
            cleaned = line.strip().lstrip("#").strip()
            cleaned = re.sub(
                r"^\W+|\W+$", "", cleaned
            )  # Remove leading/trailing non-word chars/quotes
            if cleaned:
                cleaned = re.sub(r"\s+", " ", cleaned)
                return cleaned
        return response_text.strip()

    @staticmethod
    def mapping_to_markdown_table(mapping: Dict[str, str]) -> str:
        if not mapping:
            return ""
        header = "| Diarization Label | Inferred Name/Role |\n|---|---|"
        rows = [f"| {k} | {v} |" for k, v in mapping.items()]
        return "\n".join([header] + rows)

    @staticmethod
    def build_notes_prompt(  # noqa: PLR0913 - one argument per prompt section
        prompt_override: Optional[str],
        transcript: str,
        speaker_mapping: Dict[str, str],
        user_notes: Optional[str] = None,
        meeting_context: Optional[MeetingEventContext] = None,
        output_language_instruction: Optional[str] = None,
        notes_context: Optional[NotesPromptContext] = None,
    ) -> str:
        """Compose the standalone (regeneration) notes prompt.

        ``prompt_override`` replaces the whole prompt verbatim; it is a test seam,
        not a template, and nothing in the application passes it.
        """
        if prompt_override:
            return prompt_override

        context = notes_context or NotesPromptContext()
        body = render_prompt_blocks(
            [
                (None, LLMBackend.NOTES_INTRO),
                (
                    "# Speaker Mapping",
                    LLMBackend.mapping_to_markdown_table(speaker_mapping),
                ),
                (
                    "# Recording Metadata",
                    build_meeting_metadata_prompt_section(context.metadata),
                ),
                ("# Glossary", build_glossary_prompt_section(context.glossary)),
                ("# User Notes Context", build_user_notes_prompt_section(user_notes)),
                (
                    "# Meeting Context",
                    build_meeting_context_prompt_section(meeting_context),
                ),
                # Carries its own heading.
                (
                    None,
                    build_output_language_prompt_section(output_language_instruction),
                ),
                # Structure and fidelity rules come from the shared body spec, so
                # this prompt and the unified one cannot drift.
                ("# Notes Format", build_notes_body_spec(context.notes_sections)),
                ("# Transcript to Analyze", transcript),
                (None, LLMBackend.NOTES_CLOSING),
            ]
        )
        return body

    @staticmethod
    def build_speaker_suggestion_prompt(
        prompt_override: Optional[str],
        transcript: str,
        eligible_labels: Optional[Sequence[str]] = None,
        user_notes: Optional[str] = None,
        meeting_context: Optional[MeetingEventContext] = None,
    ) -> str:
        if prompt_override:
            return prompt_override

        body = render_prompt_blocks(
            [
                (None, LLMBackend.SPEAKER_SUGGESTION_INTRO),
                ("# Critical Rules", LLMBackend.SPEAKER_SUGGESTION_CRITICAL_RULES),
                (
                    "# Eligible Diarization Labels",
                    build_eligible_speaker_labels_prompt_section(eligible_labels),
                ),
                ("# User Notes Context", build_user_notes_prompt_section(user_notes)),
                (
                    "# Meeting Context",
                    build_meeting_context_prompt_section(meeting_context),
                ),
                ("# Required JSON Schema", LLMBackend.SPEAKER_SUGGESTION_JSON_SCHEMA),
                ("# Transcript", transcript),
            ]
        )
        # Leading and trailing newlines preserved from the original template.
        return f"\n{body}\n"

    @staticmethod
    def build_title_prompt(
        prompt_override: Optional[str],
        transcript: str,
        output_language_instruction: Optional[str] = None,
    ) -> str:
        """Compose the standalone title prompt.

        Shared by every backend: this used to be the same ``str.format`` call
        copy-pasted into five of them, which is exactly the duplication that lets
        one provider's prompt drift away from the others.
        """
        if prompt_override:
            return prompt_override

        intro = (
            "You are an expert meeting-notes assistant. Given the full meeting "
            "transcript below, provide a title that summarises the main topic or "
            "purpose of the meeting. "
            + build_title_preference_instruction(True)
            + " Output ONLY the title with no additional commentary, punctuation, "
            "or formatting."
        )
        body = render_prompt_blocks(
            [
                (None, intro),
                (
                    None,
                    build_output_language_prompt_section(output_language_instruction),
                ),
                ("# Transcript\n", transcript),
            ]
        )
        return f"{body}\n"

    @staticmethod
    def build_automatic_meeting_intelligence_prompt(
        request: AutomaticMeetingIntelligenceRequest,
        prompt_template: str = None,
    ) -> str:
        return build_automatic_meeting_intelligence_prompt_text(
            request,
            prompt_template,
        )

    @staticmethod
    def build_meeting_edge_prompt(
        request: MeetingEdgeRequest,
        prompt_template: str = None,
    ) -> str:
        return build_meeting_edge_prompt_text(request, prompt_template)

    @staticmethod
    def build_meeting_edge_prompt_parts(
        request: MeetingEdgeRequest,
        prompt_template: str = None,
    ) -> Tuple[str, str]:
        return build_meeting_edge_prompt_parts_text(request, prompt_template)

    @staticmethod
    def finalise_meeting_notes(notes: str, user_notes: Optional[str] = None) -> str:
        return append_user_notes_section(strip_leading_title_heading(notes), user_notes)

    @staticmethod
    def parse_automatic_meeting_intelligence_result(
        response_text: str,
        request: AutomaticMeetingIntelligenceRequest,
    ) -> AutomaticMeetingIntelligenceResult:
        result = parse_automatic_meeting_intelligence_payload(
            response_text,
            request=request,
        )
        return finalise_automatic_meeting_intelligence_payload(
            result,
            request.user_notes,
        )

    @staticmethod
    def parse_speaker_inference_result(
        response_text: str,
        allowed_labels: Optional[Sequence[str]] = None,
    ) -> SpeakerInferenceResult:
        return parse_speaker_inference_response(
            response_text,
            allowed_labels=allowed_labels,
        )

    @staticmethod
    def parse_meeting_edge_result(
        response_text: str,
        request: MeetingEdgeRequest,
    ) -> MeetingEdgeResult:
        return parse_meeting_edge_payload(response_text, request=request)

    @staticmethod
    def build_json_repair_prompt(
        *,
        original_prompt: str,
        invalid_response: str,
        validation_error: Exception,
    ) -> str:
        return f"""Your previous response did not satisfy Nojoin's strict JSON contract.

Validation error:
{validation_error}

Return a corrected response for the original task as one valid JSON object only.
Do not include Markdown code fences, commentary, or prose before or after the JSON object.
Preserve the same schema and do not invent facts not supported by the original task.

# Original Task
{original_prompt}

# Previous Invalid Response
{invalid_response}
"""

    @staticmethod
    def get_mapped_transcript_for_llm(recording_id: int) -> str:
        """
        Fetches the diarized transcript and speaker mapping for a recording, and returns the mapped transcript as plaintext.
        """
        from sqlmodel import select

        from backend.core.db import get_sync_session
        from backend.models.recording import Recording
        from backend.models.speaker import RecordingSpeaker
        from backend.models.transcript import Transcript

        with get_sync_session() as session:
            rec = session.get(Recording, recording_id)
            if not rec:
                return "Recording not found."

            # Get Transcript
            transcript_obj = session.exec(
                select(Transcript).where(Transcript.recording_id == recording_id)
            ).first()
            if not transcript_obj or not transcript_obj.segments:
                return "Diarized transcript not found."

            # Get Speakers
            speakers = session.exec(
                select(RecordingSpeaker).where(
                    RecordingSpeaker.recording_id == recording_id
                )
            ).all()
            label_to_name = {s.diarization_label: s.name for s in speakers}

            # Render
            lines = []
            for seg in transcript_obj.segments:
                speaker_label = seg.get("speaker", "Unknown")
                speaker_name = label_to_name.get(speaker_label, speaker_label)
                text = seg.get("text", "")
                start = seg.get("start", 0)
                minutes = int(start // 60)
                seconds = int(start % 60)
                timestamp = f"[{minutes:02d}:{seconds:02d}]"
                lines.append(f"{timestamp} {speaker_name}: {text}")

            return "\n".join(lines)

    def _update_notes_in_db(self, recording_id: int, new_notes: str):
        """
        Updates the meeting notes in the database.
        """
        from sqlmodel import select

        from backend.core.db import get_sync_session
        from backend.models.transcript import Transcript

        with get_sync_session() as session:
            transcript_obj = session.exec(
                select(Transcript).where(Transcript.recording_id == recording_id)
            ).first()
            if transcript_obj:
                transcript_obj.notes = new_notes
                session.add(transcript_obj)
                session.commit()
                logger.info(f"Updated notes for recording {recording_id}")
            else:
                logger.error(
                    f"Could not find transcript for recording {recording_id} to update notes"
                )


def _get_default_model_for_provider(provider: str) -> Optional[str]:
    """Return the recommended default model for a provider."""
    # Defaults are no longer hardcoded here.
    # Users must select a model or rely on frontend recommendations.
    return None


def get_default_model_for_provider(provider: str) -> Optional[str]:
    """
    Public function to get the default model for a provider.
    This is the single source of truth for default model names.
    """
    return _get_default_model_for_provider(provider)
