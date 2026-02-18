from backend.utils.config_manager import config_manager
import logging
import json
import re
from typing import Dict, Tuple, List, Generator, Any, Optional
# Lazy imports for LLM providers to avoid heavy dependencies in API
# import openai
# import anthropic
from backend.utils.transcript_utils import render_transcript
from backend.utils.config_manager import from_project_relative_path
import os

logger = logging.getLogger(__name__)

class LLMBackend:
    def infer_speakers(self, transcript: str, prompt_template: str = None, timeout: int = 60) -> Dict[str, str]:
        """
        Infer the most likely real names or roles for each speaker label in the transcript.
        Returns a mapping from diarization label to inferred name/role.
        """
        raise NotImplementedError

    def list_models(self) -> List[str]:
        """
        List available models for the provider.
        """
        raise NotImplementedError

    def generate_meeting_notes(self, transcript: str, speaker_mapping: Dict[str, str], prompt_template: str = None, timeout: int = 60) -> str:
        """
        Generate meeting notes using the provided speaker mapping to replace generic labels.
        Returns the meeting notes as a string.
        """
        raise NotImplementedError

    def infer_speakers_and_generate_notes(self, transcript: str, prompt_template: str = None, timeout: int = 60) -> Tuple[Dict[str, str], str]:
        """
        Backward-compatible method: infers speakers and generates notes in sequence using the new methods.
        """
        mapping = self.infer_speakers(transcript, prompt_template, timeout)
        notes = self.generate_meeting_notes(transcript, mapping, prompt_template, timeout)
        return mapping, notes

    def ask_question_about_meeting(self, user_question: str, meeting_notes: str, diarized_transcript: str, conversation_history: list = None, timeout: int = 60, recording_id: str = None):
        """
        Ask a question about the meeting.
        """
        # If recording_id is provided, use mapped transcript
        if recording_id is not None:
            diarized_transcript = self.get_mapped_transcript_for_llm(recording_id)
        raise NotImplementedError

    def ask_question_streaming(self, user_question: str, meeting_notes: str, diarized_transcript: str, conversation_history: list = None, timeout: int = 60, recording_id: str = None) -> Generator[str, None, None]:
        """
        Ask a question about the meeting and yield response chunks.
        """
        if recording_id is not None:
            diarized_transcript = self.get_mapped_transcript_for_llm(recording_id)
        raise NotImplementedError

    def infer_meeting_title(self, transcript: str, prompt_template: str = None, timeout: int = 60) -> str:
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

    def _build_chat_prompt(self, user_question: str, meeting_notes: str, diarized_transcript: str) -> str:
        base_prompt = f"""
You are a helpful AI assistant. You have access to the following meeting notes and full diarized transcript. Use this information to answer the user's question as accurately as possible. If the answer is not present, say so.

# CRITICAL INSTRUCTION
When referencing transcript content, always include the timestamp in [MM:SS] format (e.g., "At [12:30], Speaker A mentioned...").

# Meeting Notes:
{meeting_notes}

# Full Diarized Transcript:
{diarized_transcript}
"""


        base_prompt += f"\nUser Question: {user_question}\n"
        return base_prompt

    @staticmethod
    def get_default_speaker_prompt_template():
        return """
You are an expert meeting assistant. Analyze the diarized meeting transcript below, where speakers are labeled generically (e.g., 'Speaker 1', 'SPEAKER_00').\n\nFirst, infer the most likely real names or roles for each speaker, based on context, introductions, or references in the transcript. If a real name is not clear, suggest a likely role (e.g., 'Project Manager', 'Client', 'Engineer') or keep the generic label. Be conservative: only use a real name or role if it is clearly stated or strongly implied.\n\nOutput a Markdown table mapping each diarization label to the inferred name or role. Only output the table and nothing else.\n\nBelow is the diarized transcript:\n\n{transcript}\n"""

    @staticmethod
    def get_default_notes_prompt_template():
        return """You are an expert meeting intelligence assistant. Your task is to generate comprehensive, high-quality meeting notes from the provided transcript. Use the speaker mapping to refer to participants by their inferred names/roles instead of generic labels.

# CRITICAL FORMATTING REQUIREMENTS
You MUST follow these formatting rules EXACTLY. Do not deviate:
1. Use ONLY the section headers specified below, in the exact order given
2. Use Markdown formatting throughout
3. Be thorough and detailed - notes may be as lengthy as required to capture all important content
4. Do NOT add any introductory text, concluding remarks, or sections not specified below
5. Start your response with "# Meeting Notes" - nothing before it

# Speaker Mapping
{mapping_table}

# OUTPUT FORMAT - Follow this EXACT structure:

# Meeting Notes

## Topics Discussed
List each major topic or theme discussed in the meeting as a bullet point. Be specific and descriptive.
- Topic 1: Brief description
- Topic 2: Brief description
(continue for all topics)

## Summary
Provide a comprehensive summary of the meeting covering:
- The main purpose and context of the meeting
- Key points raised by participants
- Important information shared
- Overall conclusions or outcomes reached

## Detailed Notes

### [Topic Name 1]
Provide detailed notes on this topic including:
- **Key Points**: Main arguments, information, or ideas presented
- **Discussion**: What was debated or discussed, including different perspectives
- **Decisions**: Any decisions made regarding this topic (if applicable)
- **Rationale**: The reasoning behind decisions or recommendations (if discussed)
- **Open Questions**: Any unresolved questions or points requiring follow-up

### [Topic Name 2]
(Follow the same structure for each major topic)

(Continue for all topics discussed)

## Action Items / Tasks
List all tasks, action items, or follow-ups mentioned, formatted as:
- [ ] Task description - Assigned to: [Person] - Due: [Date if mentioned, otherwise "TBD"]
(If no tasks were discussed, write: "No specific action items were identified in this meeting.")

## Miscellaneous
Capture any additional important information that doesn't fit the above categories:
- Side discussions or tangential points of interest
- Announcements or FYIs mentioned
- References to external documents, resources, or prior meetings
- Any other noteworthy content
(If nothing applicable, write: "No additional items.")

---

# Transcript to Analyze:

{transcript}

---

Now generate the meeting notes following the exact format specified above. Be comprehensive and capture all important details."""

    @staticmethod
    def get_default_title_prompt_template():
        return (
            "You are an expert meeting assistant. Given the full meeting transcript below, "
            "provide a concise, descriptive title that summarises the main topic or purpose of the meeting. "
            "Limit the title to at most 12 words. Output ONLY the title with no additional commentary, punctuation, or formatting.\n\n"
            "# Transcript\n\n{transcript}\n"
        )

    @staticmethod
    def get_speaker_prompt_template() -> str:
        return LLMBackend.get_default_speaker_prompt_template()

    @staticmethod
    def get_notes_prompt_template() -> str:
        return LLMBackend.get_default_notes_prompt_template()

    @staticmethod
    def get_title_prompt_template() -> str:
        return LLMBackend.get_default_title_prompt_template()

    @staticmethod
    def parse_mapping_table(response_text: str) -> Dict[str, str]:
        lines = [line.strip() for line in response_text.splitlines() if line.strip()]
        mapping = {}
        in_table = False
        for line in lines:
            if line.startswith("|") and "|" in line[1:]:
                in_table = True
                parts = [p.strip() for p in line.strip("|").split("|")]
                if len(parts) == 2 and parts[0] != "Diarization Label" and not parts[0].startswith("-"):
                    mapping[parts[0]] = parts[1]
            elif in_table and (not line.startswith("|")):
                break
        return mapping

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
            cleaned = line.strip().lstrip('#').strip()
            cleaned = re.sub(r'^\W+|\W+$', '', cleaned)  # Remove leading/trailing non-word chars/quotes
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
    def get_mapped_transcript_for_llm(recording_id: int) -> str:
        """
        Fetches the diarized transcript and speaker mapping for a recording, and returns the mapped transcript as plaintext.
        """
        from backend.core.db import get_sync_session
        from backend.models.recording import Recording
        from backend.models.transcript import Transcript
        from backend.models.speaker import RecordingSpeaker
        from sqlmodel import select

        with get_sync_session() as session:
            rec = session.get(Recording, recording_id)
            if not rec:
                return "Recording not found."
            
            # Get Transcript
            transcript_obj = session.exec(select(Transcript).where(Transcript.recording_id == recording_id)).first()
            if not transcript_obj or not transcript_obj.segments:
                 return "Diarized transcript not found."
            
            # Get Speakers
            speakers = session.exec(select(RecordingSpeaker).where(RecordingSpeaker.recording_id == recording_id)).all()
            label_to_name = {s.diarization_label: s.name for s in speakers}
            
            # Render
            lines = []
            for seg in transcript_obj.segments:
                speaker_label = seg.get('speaker', 'Unknown')
                speaker_name = label_to_name.get(speaker_label, speaker_label)
                text = seg.get('text', '')
                start = seg.get('start', 0)
                minutes = int(start // 60)
                seconds = int(start % 60)
                timestamp = f"[{minutes:02d}:{seconds:02d}]"
                lines.append(f"{timestamp} {speaker_name}: {text}")
            
            return "\n".join(lines)

    def _update_notes_in_db(self, recording_id: int, new_notes: str):
        """
        Updates the meeting notes in the database.
        """
        from backend.core.db import get_sync_session
        from backend.models.transcript import Transcript
        from sqlmodel import select

        with get_sync_session() as session:
            transcript_obj = session.exec(select(Transcript).where(Transcript.recording_id == recording_id)).first()
            if transcript_obj:
                transcript_obj.notes = new_notes
                session.add(transcript_obj)
                session.commit()
                logger.info(f"Updated notes for recording {recording_id}")
            else:
                logger.error(f"Could not find transcript for recording {recording_id} to update notes")

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
            raise ValueError("Google Gemini API key is not set. Please provide it in settings.")
        self.api_key = api_key
        self.model = model or _get_default_model_for_provider("gemini")
        self.genai = genai  # Store reference for later use
        self.client = genai.Client(api_key=self.api_key)

    def _extract_text_from_response(self, response):
        """
        Extract text from the response, handling potential non-text parts (like thoughts)
        to avoid warnings and ensure text extraction.
        """
        if hasattr(response, 'candidates') and response.candidates:
            candidate = response.candidates[0]
            if hasattr(candidate, 'content') and candidate.content and hasattr(candidate.content, 'parts'):
                text_parts = []
                for part in candidate.content.parts:
                    if hasattr(part, 'text') and part.text:
                        text_parts.append(part.text)
                if text_parts:
                    return "".join(text_parts)
        
        # Fallback to .text if available (which might log the warning but works)
        if hasattr(response, 'text'):
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
                name = getattr(m, 'name', None)
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
        except Exception as e:
            logger.error(f"Gemini API error (list models): {e}")
            return []

    def infer_speakers(self, transcript: str, prompt_template: str = None, timeout: int = 60) -> Dict[str, str]:
        """
        Run speaker inference on the transcript and return a mapping from diarization label to inferred name/role.
        Can be called independently of meeting notes generation.
        """
        if prompt_template is None:
            prompt_template = self.get_speaker_prompt_template()
        prompt = prompt_template.format(transcript=transcript)
        if not self.model:
            raise ValueError("No Gemini model configured. Please select a model in Settings.")
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
            )
            text = self._extract_text_from_response(response)
            mapping = self.parse_mapping_table(text)
            return mapping
        except Exception as e:
            logger.error(f"Gemini API error (speaker mapping): {e}")
            raise RuntimeError(f"Gemini API error (speaker mapping): {e}")

    def generate_meeting_notes(self, transcript: str, speaker_mapping: Dict[str, str], prompt_template: str = None, timeout: int = 60) -> str:
        """
        Generate meeting notes using the provided speaker mapping. Should be called after user relabeling.
        """
        if prompt_template is None:
            prompt_template = self.get_notes_prompt_template()
        mapping_table = self.mapping_to_markdown_table(speaker_mapping)
        prompt = prompt_template.format(transcript=transcript, mapping_table=mapping_table)
        if not self.model:
            raise ValueError("No Gemini model configured. Please select a model in Settings.")
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
            )
            text = self._extract_text_from_response(response)
            notes = self.parse_notes(text)
            return notes
        except Exception as e:
            logger.error(f"Gemini API error (meeting notes): {e}")
            raise RuntimeError(f"Gemini API error (meeting notes): {e}")

    # infer_speakers_and_generate_notes is inherited and calls the above two methods

    def ask_question_about_meeting(self, user_question: str, meeting_notes: str, diarized_transcript: str, conversation_history: list = None, timeout: int = 60, recording_id: str = None):
        # If recording_id is provided, use mapped transcript
        if recording_id is not None:
            diarized_transcript = self.get_mapped_transcript_for_llm(recording_id)
        
        prompt = self._build_chat_prompt(user_question, meeting_notes, diarized_transcript)
        
        contents = []
        if conversation_history:
            contents.extend(conversation_history)
        contents.append({"role": "user", "parts": [{"text": prompt}]})
        if not self.model:
            raise ValueError("No Gemini model configured. Please select a model in Settings.")
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
            )
            return self._extract_text_from_response(response)
        except Exception as e:
            logger.error(f"Gemini API error (chat): {e}")
            raise RuntimeError(f"Gemini API error (chat): {e}")

    def ask_question_streaming(self, user_question: str, meeting_notes: str, diarized_transcript: str, conversation_history: list = None, timeout: int = 60, recording_id: str = None) -> Generator[Any, None, None]:
        if recording_id is not None:
            diarized_transcript = self.get_mapped_transcript_for_llm(recording_id)
            
        prompt = self._build_chat_prompt(user_question, meeting_notes, diarized_transcript)
        
        contents = []
        if conversation_history:
            contents.extend(conversation_history)
        contents.append({"role": "user", "parts": [{"text": prompt}]})
        
        if not self.model:
            raise ValueError("No Gemini model configured. Please select a model in Settings.")
            
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
                    )
                )
            )
            for chunk in response_stream:
                # Check for function calls
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
                    if hasattr(chunk, 'candidates') and chunk.candidates:
                        candidate = chunk.candidates[0]
                        if hasattr(candidate, 'content') and candidate.content and hasattr(candidate.content, 'parts'):
                            for part in candidate.content.parts:
                                if hasattr(part, 'text') and part.text:
                                    has_text = True
                                    break
                    
                    if has_text and chunk.text:
                        yield chunk.text
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"Gemini API error (streaming chat): {e}")
            # If it's just a property access error because of no text, ignore it
            if "Candidate was blocked" in str(e) or "has no parts" in str(e):
                pass
            else:
                raise RuntimeError(f"Gemini API error (streaming chat): {e}")

    def infer_meeting_title(self, transcript: str, prompt_template: str = None, timeout: int = 60) -> str:
        """
        Infer a concise, descriptive meeting title from the provided transcript.
        Sub-classes must implement.
        """
        if prompt_template is None:
            prompt_template = self.get_title_prompt_template()
        prompt = prompt_template.format(transcript=transcript)
        if not self.model:
            raise ValueError("No Gemini model configured. Please select a model in Settings.")
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
            )
            text = self._extract_text_from_response(response)
            title = self.parse_title(text)
            return title
        except Exception as e:
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
        except Exception as e:
            logger.error(f"Gemini API validation failed: {e}")
            raise ValueError(f"Gemini API validation failed: {e}")

class OpenAILLMBackend(LLMBackend):
    def __init__(self, api_key=None, model=None):
        import openai
        if api_key is None:
            api_key = config_manager.get("openai_api_key")
        if not api_key:
            raise ValueError("OpenAI API key is not set. Please provide it in settings.")
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
                elif "o1" in m.id: # Add support for reasoning models
                    model_list.append(m.id)
            return sorted(model_list)
        except Exception as e:
            logger.error(f"OpenAI API error (list models): {e}")
            return []

    def infer_speakers(self, transcript: str, prompt_template: str = None, timeout: int = 60) -> Dict[str, str]:
        """
        Run speaker inference on the transcript and return a mapping from diarization label to inferred name/role.
        Can be called independently of meeting notes generation.
        """
        if prompt_template is None:
            prompt_template = self.get_speaker_prompt_template()
        prompt = prompt_template.format(transcript=transcript)
        if not self.model:
            raise ValueError("No OpenAI model configured. Please select a model in Settings.")
        try:
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                timeout=timeout,
                stream=True
            )
            text_chunks = []
            for chunk in stream:
                if chunk.choices[0].delta.content:
                    text_chunks.append(chunk.choices[0].delta.content)
            text = "".join(text_chunks)
            mapping = self.parse_mapping_table(text)
            return mapping
        except Exception as e:
            if "not a chat model" in str(e) or "404" in str(e):
                logger.error(f"OpenAI API error (speaker mapping): Invalid model {self.model}. {e}")
                raise ValueError(f"The model '{self.model}' appears to be invalid or is not a chat model. Please check the model name in Settings.")
            logger.error(f"OpenAI API error (speaker mapping): {e}")
            raise RuntimeError(f"OpenAI API error (speaker mapping): {e}")

    def generate_meeting_notes(self, transcript: str, speaker_mapping: Dict[str, str], prompt_template: str = None, timeout: int = 60) -> str:
        """
        Generate meeting notes using the provided speaker mapping. Should be called after user relabeling.
        """
        if prompt_template is None:
            prompt_template = self.get_notes_prompt_template()
        mapping_table = self.mapping_to_markdown_table(speaker_mapping)
        prompt = prompt_template.format(transcript=transcript, mapping_table=mapping_table)
        if not self.model:
            raise ValueError("No OpenAI model configured. Please select a model in Settings.")
        try:
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                timeout=timeout,
                stream=True
            )
            text_chunks = []
            for chunk in stream:
                if chunk.choices[0].delta.content:
                    text_chunks.append(chunk.choices[0].delta.content)
            text = "".join(text_chunks)
            notes = self.parse_notes(text)
            return notes
        except Exception as e:
            if "not a chat model" in str(e) or "404" in str(e):
                logger.error(f"OpenAI API error (meeting notes): Invalid model {self.model}. {e}")
                raise ValueError(f"The model '{self.model}' appears to be invalid or is not a chat model. Please check the model name in Settings.")
            logger.error(f"OpenAI API error (meeting notes): {e}")
            raise RuntimeError(f"OpenAI API error (meeting notes): {e}")

    # infer_speakers_and_generate_notes is inherited and calls the above two methods

    def ask_question_about_meeting(self, user_question: str, meeting_notes: str, diarized_transcript: str, conversation_history: list = None, timeout: int = 60, recording_id: str = None):
        if recording_id is not None:
            diarized_transcript = self.get_mapped_transcript_for_llm(recording_id)
        
        prompt = self._build_chat_prompt(user_question, meeting_notes, diarized_transcript)
        
        messages = []
        if conversation_history:
            for msg in conversation_history:
                if msg.get("role") and msg.get("parts"):
                    role = msg["role"]
                    # Map 'model' to 'assistant' for OpenAI compatibility
                    if role == "model":
                        role = "assistant"
                    for part in msg["parts"]:
                        messages.append({"role": role, "content": part["text"]})
        messages.append({"role": "user", "content": prompt})
        if not self.model:
            raise ValueError("No OpenAI model configured. Please select a model in Settings.")
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.2,
                timeout=timeout
            )
            return response.choices[0].message.content
        except Exception as e:
            if "not a chat model" in str(e) or "404" in str(e):
                logger.error(f"OpenAI API error (chat): Invalid model {self.model}. {e}")
                raise ValueError(f"The model '{self.model}' appears to be invalid or is not a chat model. Please check the model name in Settings.")
            logger.error(f"OpenAI API error (chat): {e}")
            raise RuntimeError(f"OpenAI API error (chat): {e}")

    def ask_question_streaming(self, user_question: str, meeting_notes: str, diarized_transcript: str, conversation_history: list = None, timeout: int = 60, recording_id: str = None) -> Generator[Any, None, None]:
        if recording_id is not None:
            diarized_transcript = self.get_mapped_transcript_for_llm(recording_id)
            
        prompt = self._build_chat_prompt(user_question, meeting_notes, diarized_transcript)
        
        messages = []
        if conversation_history:
            for msg in conversation_history:
                if msg.get("role") and msg.get("parts"):
                    role = msg["role"]
                    # Map 'model' to 'assistant' for OpenAI compatibility
                    if role == "model":
                        role = "assistant"
                    for part in msg["parts"]:
                        messages.append({"role": role, "content": part["text"]})
        messages.append({"role": "user", "content": prompt})
        
        tools = [{
            "type": "function",
            "function": {
                "name": "update_meeting_notes",
                "description": "Overwrites the current meeting notes with new content. Use this whenever the user asks to modify, edit, add to, or delete parts of the meeting notes. The input should be the fully updated Markdown content of the notes.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "The full, updated Markdown text of the meeting notes."
                        }
                    },
                    "required": ["content"]
                }
            }
        }]
        
        # Prepare request arguments
        request_kwargs = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "timeout": timeout,
            "tools": tools
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
                            tool_calls_accumulator[idx] = {
                                "name": "",
                                "arguments": ""
                            }
                        
                        if tool_part.function and tool_part.function.name:
                            tool_calls_accumulator[idx]["name"] += tool_part.function.name
                        
                        if tool_part.function and tool_part.function.arguments:
                            tool_calls_accumulator[idx]["arguments"] += tool_part.function.arguments
            
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
                        logger.error("Failed to parse tool arguments for update_meeting_notes")

        except Exception as e:
            if "not a chat model" in str(e) or "404" in str(e):
                logger.error(f"OpenAI API error (streaming chat): Invalid model {self.model}. {e}")
                raise ValueError(f"The model '{self.model}' appears to be invalid or is not a chat model. Please check the model name in Settings.")
            logger.error(f"OpenAI API error (streaming chat): {e}")
            raise RuntimeError(f"OpenAI API error (streaming chat): {e}")

    def infer_meeting_title(self, transcript: str, prompt_template: str = None, timeout: int = 60) -> str:
        """
        Infer a concise, descriptive meeting title from the provided transcript.
        Sub-classes must implement.
        """
        if prompt_template is None:
            prompt_template = self.get_title_prompt_template()
        prompt = prompt_template.format(transcript=transcript)
        if not self.model:
            raise ValueError("No OpenAI model configured. Please select a model in Settings.")
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                timeout=timeout
            )
            title = self.parse_title(response.choices[0].message.content)
            return title
        except Exception as e:
            if "not a chat model" in str(e) or "404" in str(e):
                logger.error(f"OpenAI API error (meeting title): Invalid model {self.model}. {e}")
                raise ValueError(f"The model '{self.model}' appears to be invalid or is not a chat model. Please check the model name in Settings.")
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
        except Exception as e:
            logger.error(f"OpenAI API validation failed: {e}")
            raise ValueError(f"OpenAI API validation failed: {e}")

class AnthropicLLMBackend(LLMBackend):
    def __init__(self, api_key=None, model=None):
        import anthropic
        if api_key is None:
            api_key = config_manager.get("anthropic_api_key")
        if not api_key:
            raise ValueError("Anthropic API key is not set. Please provide it in settings.")
        self.api_key = api_key
        self.model = model or _get_default_model_for_provider("anthropic")
        self.client = anthropic.Anthropic(api_key=self.api_key)

    def list_models(self) -> List[str]:
        """
        List available Anthropic models.
        """
        try:
            # Anthropic Python SDK supports models.list()
            if hasattr(self.client, 'models') and hasattr(self.client.models, 'list'):
                models = self.client.models.list()
                # Filter for claude models
                return sorted([m.id for m in models if "claude" in m.id])
            else:
                return []
        except Exception as e:
            logger.error(f"Anthropic API error (list models): {e}")
            return []

    def infer_speakers(self, transcript: str, prompt_template: str = None, timeout: int = 60) -> Dict[str, str]:
        """
        Run speaker inference on the transcript and return a mapping from diarization label to inferred name/role.
        Can be called independently of meeting notes generation.
        """
        if prompt_template is None:
            prompt_template = self.get_speaker_prompt_template()
        prompt = prompt_template.format(transcript=transcript)
        if not self.model:
            raise ValueError("No Anthropic model configured. Please select a model in Settings.")
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            text = response.content[0].text if hasattr(response.content[0], 'text') else response.content[0]
            mapping = self.parse_mapping_table(text)
            return mapping
        except Exception as e:
            logger.error(f"Anthropic API error (speaker mapping): {e}")
            raise RuntimeError(f"Anthropic API error (speaker mapping): {e}")

    def generate_meeting_notes(self, transcript: str, speaker_mapping: Dict[str, str], prompt_template: str = None, timeout: int = 60) -> str:
        """
        Generate meeting notes using the provided speaker mapping. Should be called after user relabeling.
        """
        if prompt_template is None:
            prompt_template = self.get_notes_prompt_template()
        mapping_table = self.mapping_to_markdown_table(speaker_mapping)
        prompt = prompt_template.format(transcript=transcript, mapping_table=mapping_table)
        if not self.model:
            raise ValueError("No Anthropic model configured. Please select a model in Settings.")
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            text = response.content[0].text if hasattr(response.content[0], 'text') else response.content[0]
            notes = self.parse_notes(text)
            return notes
        except Exception as e:
            logger.error(f"Anthropic API error (meeting notes): {e}")
            raise RuntimeError(f"Anthropic API error (meeting notes): {e}")

    # infer_speakers_and_generate_notes is inherited and calls the above two methods

    def ask_question_about_meeting(self, user_question: str, meeting_notes: str, diarized_transcript: str, conversation_history: list = None, timeout: int = 60, recording_id: str = None):
        # If recording_id is provided, use mapped transcript
        if recording_id is not None:
            diarized_transcript = self.get_mapped_transcript_for_llm(recording_id)
        
        prompt = self._build_chat_prompt(user_question, meeting_notes, diarized_transcript)
        
        messages = []
        if conversation_history:
            for msg in conversation_history:
                if msg.get("role") and msg.get("parts"):
                    for part in msg["parts"]:
                        messages.append({"role": msg["role"], "content": part["text"]})
        messages.append({"role": "user", "content": prompt})
        if not self.model:
            raise ValueError("No Anthropic model configured. Please select a model in Settings.")
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                messages=messages,
                temperature=0.2,
            )
            return response.content[0].text if hasattr(response.content[0], 'text') else response.content[0]
        except Exception as e:
            logger.error(f"Anthropic API error (chat): {e}")
            raise RuntimeError(f"Anthropic API error (chat): {e}")

    def ask_question_streaming(self, user_question: str, meeting_notes: str, diarized_transcript: str, conversation_history: list = None, timeout: int = 60, recording_id: str = None) -> Generator[Any, None, None]:
        if recording_id is not None:
            diarized_transcript = self.get_mapped_transcript_for_llm(recording_id)
        
        prompt = self._build_chat_prompt(user_question, meeting_notes, diarized_transcript)
        
        messages = []
        if conversation_history:
            for msg in conversation_history:
                if msg.get("role") and msg.get("parts"):
                    for part in msg["parts"]:
                        messages.append({"role": msg["role"], "content": part["text"]})
        messages.append({"role": "user", "content": prompt})
        
        tool_definition = {
            "name": "update_meeting_notes",
            "description": "Overwrites the current meeting notes with new content. Use this whenever the user asks to modify, edit, add to, or delete parts of the meeting notes. The input should be the fully updated Markdown content of the notes.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The full, updated Markdown text of the meeting notes."
                    }
                },
                "required": ["content"]
            }
        }
        
        try:
            with self.client.messages.stream(
                model=self.model,
                max_tokens=1024,
                messages=messages,
                temperature=0.2,
                tools=[tool_definition]
            ) as stream:
                
                current_tool_name = None
                current_json_accum = ""

                for event in stream:
                    # Text Delta
                    if event.type == "content_block_delta" and event.delta.type == "text_delta":
                        yield event.delta.text
                    
                    # Tool Start
                    elif event.type == "content_block_start" and event.content_block.type == "tool_use":
                        current_tool_name = event.content_block.name
                        current_json_accum = ""
                    
                    # Tool Args Delta
                    elif event.type == "content_block_delta" and event.delta.type == "input_json_delta":
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
                                logger.error("Failed to parse tool arguments for update_meeting_notes")
                            finally:
                                current_tool_name = None

        except Exception as e:
            logger.error(f"Anthropic API error (streaming chat): {e}")
            raise RuntimeError(f"Anthropic API error (streaming chat): {e}")

    def infer_meeting_title(self, transcript: str, prompt_template: str = None, timeout: int = 60) -> str:
        """
        Infer a concise, descriptive meeting title from the provided transcript.
        Sub-classes must implement.
        """
        if prompt_template is None:
            prompt_template = self.get_title_prompt_template()
        prompt = prompt_template.format(transcript=transcript)
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            title = self.parse_title(response.content[0].text if hasattr(response.content[0], 'text') else response.content[0])
            return title
        except Exception as e:
            logger.error(f"Anthropic API error (meeting title): {e}")
            raise RuntimeError(f"Anthropic API error (meeting title): {e}")

    def validate_api_key(self) -> bool:
        """
        Validate the API key by making a lightweight API call.
        Returns True if valid, raises an exception or returns False if invalid.
        """
        try:
            # Use list_models to verify key without consuming tokens
            if hasattr(self.client, 'models') and hasattr(self.client.models, 'list'):
                self.client.models.list()
                return True
            
            # Fallback if list_models not available (should not happen with recent SDK)
            if not self.model:
                raise ValueError("No Anthropic model configured. Please select a model in Settings.")

            # Try a minimal generation with the configured model
            self.client.messages.create(
                model=self.model,
                max_tokens=1,
                messages=[{"role": "user", "content": "Hi"}],
            )
            return True
        except Exception as e:
            logger.error(f"Anthropic API validation failed: {e}")
            raise ValueError(f"Anthropic API validation failed: {e}")

class OllamaLLMBackend(LLMBackend):
    def __init__(self, api_url=None, model=None):
        import requests
        self.requests = requests
        if api_url is None:
            api_url = config_manager.get("ollama_api_url")
        if not api_url:
             api_url = "http://host.docker.internal:11434"
        
        self.api_url = api_url.rstrip('/')
        self.model = model or config_manager.get("ollama_model")

    def list_models(self) -> List[str]:
        try:
            resp = self.requests.get(f"{self.api_url}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            return sorted([m['name'] for m in data.get('models', [])])
        except Exception as e:
            logger.error(f"Ollama API error (list models): {e}")
            return []

    def infer_speakers(self, transcript: str, prompt_template: str = None, timeout: int = 60) -> Dict[str, str]:
        if prompt_template is None:
            prompt_template = self.get_speaker_prompt_template()
        prompt = prompt_template.format(transcript=transcript)
        if not self.model:
            raise ValueError("No Ollama model configured. Please select a model in Settings.")
        
        try:
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": 0.2}
            }
            resp = self.requests.post(f"{self.api_url}/api/chat", json=payload, timeout=timeout)
            resp.raise_for_status()
            text = resp.json().get('message', {}).get('content', '')
            return self.parse_mapping_table(text)
        except Exception as e:
            logger.error(f"Ollama API error (speaker mapping): {e}")
            raise RuntimeError(f"Ollama API error (speaker mapping): {e}")

    def generate_meeting_notes(self, transcript: str, speaker_mapping: Dict[str, str], prompt_template: str = None, timeout: int = 60) -> str:
        if prompt_template is None:
            prompt_template = self.get_notes_prompt_template()
        mapping_table = self.mapping_to_markdown_table(speaker_mapping)
        prompt = prompt_template.format(transcript=transcript, mapping_table=mapping_table)
        if not self.model:
            raise ValueError("No Ollama model configured. Please select a model in Settings.")
        
        try:
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": 0.2}
            }
            resp = self.requests.post(f"{self.api_url}/api/chat", json=payload, timeout=timeout)
            resp.raise_for_status()
            text = resp.json().get('message', {}).get('content', '')
            return self.parse_notes(text)
        except Exception as e:
            logger.error(f"Ollama API error (meeting notes): {e}")
            raise RuntimeError(f"Ollama API error (meeting notes): {e}")

    def ask_question_about_meeting(self, user_question: str, meeting_notes: str, diarized_transcript: str, conversation_history: list = None, timeout: int = 60, recording_id: str = None):
        if recording_id is not None:
            diarized_transcript = self.get_mapped_transcript_for_llm(recording_id)
        
        prompt = self._build_chat_prompt(user_question, meeting_notes, diarized_transcript)
        
        messages = []
        if conversation_history:
            for msg in conversation_history:
                if msg.get("role") and msg.get("parts"):
                    for part in msg["parts"]:
                        messages.append({"role": msg["role"], "content": part["text"]})
        messages.append({"role": "user", "content": prompt})
        
        if not self.model:
            raise ValueError("No Ollama model configured. Please select a model in Settings.")

        try:
            payload = {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0.2}
            }
            resp = self.requests.post(f"{self.api_url}/api/chat", json=payload, timeout=timeout)
            resp.raise_for_status()
            return resp.json().get('message', {}).get('content', '')
        except Exception as e:
            logger.error(f"Ollama API error (chat): {e}")
            raise RuntimeError(f"Ollama API error (chat): {e}")

    def ask_question_streaming(self, user_question: str, meeting_notes: str, diarized_transcript: str, conversation_history: list = None, timeout: int = 60, recording_id: str = None) -> Generator[str, None, None]:
        if recording_id is not None:
            diarized_transcript = self.get_mapped_transcript_for_llm(recording_id)
            
        prompt = self._build_chat_prompt(user_question, meeting_notes, diarized_transcript)
        
        messages = []
        if conversation_history:
            for msg in conversation_history:
                if msg.get("role") and msg.get("parts"):
                    for part in msg["parts"]:
                        messages.append({"role": msg["role"], "content": part["text"]})
        messages.append({"role": "user", "content": prompt})
        
        if not self.model:
            raise ValueError("No Ollama model configured. Please select a model in Settings.")

        try:
            payload = {
                "model": self.model,
                "messages": messages,
                "stream": True,
                "options": {"temperature": 0.2}
            }
            resp = self.requests.post(f"{self.api_url}/api/chat", json=payload, stream=True, timeout=timeout)
            resp.raise_for_status()
            
            import json
            for line in resp.iter_lines():
                if line:
                    try:
                        chunk = json.loads(line)
                        content = chunk.get('message', {}).get('content', '')
                        if content:
                            yield content
                    except json.JSONDecodeError:
                        pass
        except Exception as e:
            logger.error(f"Ollama API error (streaming chat): {e}")
            raise RuntimeError(f"Ollama API error (streaming chat): {e}")

    def infer_meeting_title(self, transcript: str, prompt_template: str = None, timeout: int = 60) -> str:
        if prompt_template is None:
            prompt_template = self.get_title_prompt_template()
        prompt = prompt_template.format(transcript=transcript)
        if not self.model:
            raise ValueError("No Ollama model configured. Please select a model in Settings.")
        try:
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": 0.2}
            }
            resp = self.requests.post(f"{self.api_url}/api/chat", json=payload, timeout=timeout)
            resp.raise_for_status()
            text = resp.json().get('message', {}).get('content', '')
            return self.parse_title(text)
        except Exception as e:
            logger.error(f"Ollama API error (meeting title): {e}")
            raise RuntimeError(f"Ollama API error (meeting title): {e}")

    def validate_api_key(self) -> bool:
        try:
            self.list_models()
            return True
        except Exception as e:
            logger.error(f"Ollama API validation failed: {e}")
            raise ValueError(f"Ollama API validation failed: {e}")

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

# --- LLM Backend Factory ---
def get_llm_backend(provider: str, api_key=None, model=None, api_url=None):
    """
    Factory function to instantiate the appropriate LLM backend.
    Heavy dependencies are only imported when needed.
    Args:
        provider (str): 'gemini', 'openai', 'anthropic', or 'ollama'
        api_key (str): API key for the provider (optional)
        model (str): Model name (optional)
        api_url (str): API URL for local providers (optional)
    Returns:
        Instance of the appropriate LLMBackend subclass.
    Raises:
        ValueError: If provider is unknown.
    """
    from backend.utils.config_manager import config_manager
    if model is None:
        # For local providers, we might have specific model keys
        if provider == "ollama":
            model = config_manager.get("ollama_model")
        else:
            model = config_manager.get(f"{provider}_model")
    
    if provider == "gemini":
        return GeminiLLMBackend(api_key=api_key, model=model)
    elif provider == "openai":
        return OpenAILLMBackend(api_key=api_key, model=model)
    elif provider == "anthropic":
        return AnthropicLLMBackend(api_key=api_key, model=model)
    elif provider == "ollama":
        return OllamaLLMBackend(api_url=api_url, model=model)
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")
