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
    get_default_meeting_edge_prompt_template,
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
    get_default_automatic_meeting_intelligence_prompt_template,
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
    NOTES_BODY_SPEC,
    MeetingEventContext,
    append_user_notes_section,
    build_meeting_context_prompt_section,
    build_user_notes_prompt_section,
    strip_leading_title_heading,
)
from backend.utils.speaker_name_suggestions import (
    SpeakerInferenceResult,
    parse_speaker_inference_response,
)

logger = logging.getLogger(__name__)

JSON_CONTRACT_ERRORS = (MeetingIntelligenceContractError, MeetingEdgeContractError)


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

    @staticmethod
    def get_default_speaker_suggestion_prompt_template():
        return """
You are an expert meeting assistant. Analyze the diarized meeting transcript below and return only evidence-backed speaker name suggestions for unresolved diarization labels.

# Critical Rules
- Return valid JSON only. Do not include Markdown, commentary, or code fences unless the client asks for them.
- Only include suggestions for the eligible diarization labels listed below.
- Omit a label entirely if the transcript evidence is weak or ambiguous.
- Each suggestion must include at least one direct evidence span quoted from the transcript.
- Prefer names already present in the transcript or linked meeting attendees. Do not invent names.
- Be conservative. If you are unsure, omit the suggestion instead of guessing.
- `confidence` must be between 0.0 and 1.0.

# Eligible Diarization Labels
{eligible_labels_section}

# User Notes Context
{user_notes_section}

# Meeting Context
{meeting_context_section}

# Required JSON Schema
{{
    "suggestions": [
        {{
            "diarization_label": "SPEAKER_00",
            "suggested_name": "Alex Johnson",
            "confidence": 0.93,
            "rationale": "The speaker says 'I'm Alex' and the attendee list contains Alex Johnson.",
            "signals": ["self_introduction", "meeting_attendee_exact"],
            "evidence_spans": [
                {{
                    "quote": "Hi everyone, I'm Alex from product.",
                    "reason": "self_introduction",
                    "start_seconds": 0.0,
                    "end_seconds": 3.2
                }}
            ]
        }}
    ]
}}

# Transcript
{transcript}
"""

    @staticmethod
    def get_default_notes_prompt_template():
        # Body structure and fidelity rules live in the shared NOTES_BODY_SPEC so
        # this standalone (regeneration) prompt and the unified meeting-intelligence
        # prompt cannot drift. Only the delivery wrapper (raw Markdown, speaker
        # mapping, transcript) is specific to this path.
        return (
            """You are an expert meeting-notes assistant. Generate meeting notes from the transcript below. Use the speaker mapping to refer to participants by their inferred names or roles instead of generic labels.

# Speaker Mapping
{mapping_table}

# User Notes Context
{user_notes_section}

# Meeting Context
{meeting_context_section}

{output_language_section}

# Notes Format
"""
            + NOTES_BODY_SPEC
            + """

# Transcript to Analyze
{transcript}

Return only the meeting notes described above, starting directly with the Summary section and with no preamble or closing commentary."""
        )

    @staticmethod
    def get_default_title_prompt_template():
        # Reuse the shared title-style instruction (short titles by default) so the
        # standalone title path matches the unified meeting-intelligence path.
        return (
            "You are an expert meeting-notes assistant. Given the full meeting transcript below, "
            "provide a title that summarises the main topic or purpose of the meeting. "
            + build_title_preference_instruction(True)
            + " Output ONLY the title with no additional commentary, punctuation, or formatting.\n\n"
            "{output_language_section}\n\n"
            "# Transcript\n\n{transcript}\n"
        )

    @staticmethod
    def get_notes_prompt_template() -> str:
        return LLMBackend.get_default_notes_prompt_template()

    @staticmethod
    def get_title_prompt_template() -> str:
        return LLMBackend.get_default_title_prompt_template()

    @staticmethod
    def get_speaker_suggestion_prompt_template() -> str:
        return LLMBackend.get_default_speaker_suggestion_prompt_template()

    @staticmethod
    def get_automatic_meeting_intelligence_prompt_template() -> str:
        return get_default_automatic_meeting_intelligence_prompt_template()

    @staticmethod
    def get_meeting_edge_prompt_template() -> str:
        return get_default_meeting_edge_prompt_template()

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
    def build_notes_prompt(
        prompt_template: str,
        transcript: str,
        speaker_mapping: Dict[str, str],
        user_notes: Optional[str] = None,
        meeting_context: Optional[MeetingEventContext] = None,
        output_language_instruction: Optional[str] = None,
    ) -> str:
        return prompt_template.format(
            transcript=transcript,
            mapping_table=LLMBackend.mapping_to_markdown_table(speaker_mapping),
            user_notes_section=build_user_notes_prompt_section(user_notes),
            meeting_context_section=build_meeting_context_prompt_section(
                meeting_context
            ),
            output_language_section=build_output_language_prompt_section(
                output_language_instruction
            ),
        )

    @staticmethod
    def build_speaker_suggestion_prompt(
        prompt_template: str,
        transcript: str,
        eligible_labels: Optional[Sequence[str]] = None,
        user_notes: Optional[str] = None,
        meeting_context: Optional[MeetingEventContext] = None,
    ) -> str:
        return prompt_template.format(
            transcript=transcript,
            eligible_labels_section=build_eligible_speaker_labels_prompt_section(
                eligible_labels
            ),
            user_notes_section=build_user_notes_prompt_section(user_notes),
            meeting_context_section=build_meeting_context_prompt_section(
                meeting_context
            ),
        )

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
