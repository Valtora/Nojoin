from nojoin.utils.config_manager import config_manager
import logging
import json
import re
from typing import Dict, Tuple, List
import openai
import anthropic
from nojoin.utils.transcript_utils import render_transcript
from nojoin.db import database as db_ops
from nojoin.utils.config_manager import from_project_relative_path
import os

logger = logging.getLogger(__name__)

class LLMBackend:
    def infer_speakers(self, transcript: str, prompt_template: str = None, timeout: int = 60) -> Dict[str, str]:
        """
        Infer the most likely real names or roles for each speaker label in the transcript.
        Returns a mapping from diarization label to inferred name/role.
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
        # If recording_id is provided, use mapped transcript
        if recording_id is not None:
            diarized_transcript = self.get_mapped_transcript_for_llm(recording_id)
        raise NotImplementedError

    @staticmethod
    def get_default_speaker_prompt_template():
        return """
You are an expert meeting assistant. Analyze the diarized meeting transcript below, where speakers are labeled generically (e.g., 'Speaker 1', 'SPEAKER_00').\n\nFirst, infer the most likely real names or roles for each speaker, based on context, introductions, or references in the transcript. If a real name is not clear, suggest a likely role (e.g., 'Project Manager', 'Client', 'Engineer') or keep the generic label. Be conservative: only use a real name or role if it is clearly stated or strongly implied.\n\nOutput a Markdown table mapping each diarization label to the inferred name or role. Only output the table and nothing else.\n\nBelow is the diarized transcript:\n\n{transcript}\n"""

    @staticmethod
    def get_default_notes_prompt_template():
        return """
You are an expert meeting assistant. Using the mapping of speaker labels to real names/roles provided below, generate the meeting notes. Use the inferred names/roles in place of the generic labels. Output ONLY the meeting notes in the following strict format, with no extra commentary, introductory text, concluding remarks, or additional sections.\n\n# Speaker Mapping\n{mapping_table}\n\n# Meeting Notes\n\n## Meeting Summary\nA concise summary of the key topics discussed, main points raised, and significant conclusions or outcomes. Use bullet points if appropriate.\n\n## Decisions\n*Only include this section if clear decisions were made during the meeting. List each decision as a bullet point. Omit this section if no decisions were made.*\n\n## Tasks\n*Only include this section if clear tasks or follow-up actions were discussed. List each task as a bullet point. Omit this section if no tasks were discussed.*\n\nBelow is the diarized transcript:\n\n{transcript}\n"""

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
        return "\n".join(notes_lines).strip()

    @staticmethod
    def mapping_to_markdown_table(mapping: Dict[str, str]) -> str:
        if not mapping:
            return ""
        header = "| Diarization Label | Inferred Name/Role |\n|---|---|"
        rows = [f"| {k} | {v} |" for k, v in mapping.items()]
        return "\n".join([header] + rows)

    @staticmethod
    def get_mapped_transcript_for_llm(recording_id: str) -> str:
        """
        Fetches the diarized transcript and speaker mapping for a recording, and returns the mapped transcript as plaintext.
        """
        rec = db_ops.get_recording_by_id(recording_id)
        if not rec:
            return "Recording not found."
        diarized_transcript_path = rec.get("diarized_transcript_path")
        abs_diarized_transcript_path = from_project_relative_path(diarized_transcript_path) if diarized_transcript_path else None
        if not abs_diarized_transcript_path or not os.path.exists(abs_diarized_transcript_path):
            return "Diarized transcript not found."
        speakers = db_ops.get_speakers_for_recording(recording_id)
        label_to_name = {s['diarization_label']: s['name'] for s in speakers if s.get('diarization_label')}
        label_to_name['Unknown'] = 'Unknown'
        return render_transcript(abs_diarized_transcript_path, label_to_name, output_format="plain")

from google import genai

class GeminiLLMBackend(LLMBackend):
    def __init__(self, api_key=None, model=None):
        if api_key is None:
            api_key = config_manager.get("gemini_api_key")
        if not api_key:
            raise ValueError("Google Gemini API key is not set. Please provide it in settings.")
        self.api_key = api_key
        self.model = model or "gemini-2.5-flash-preview-04-17"
        self.client = genai.Client(api_key=self.api_key)

    def infer_speakers(self, transcript: str, prompt_template: str = None, timeout: int = 60) -> Dict[str, str]:
        """
        Run speaker inference on the transcript and return a mapping from diarization label to inferred name/role.
        Can be called independently of meeting notes generation.
        """
        if prompt_template is None:
            prompt_template = self.get_default_speaker_prompt_template()
        prompt = prompt_template.format(transcript=transcript)
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
            )
            mapping = self.parse_mapping_table(response.text)
            return mapping
        except Exception as e:
            logger.error(f"Gemini API error (speaker mapping): {e}")
            raise RuntimeError(f"Gemini API error (speaker mapping): {e}")

    def generate_meeting_notes(self, transcript: str, speaker_mapping: Dict[str, str], prompt_template: str = None, timeout: int = 60) -> str:
        """
        Generate meeting notes using the provided speaker mapping. Should be called after user relabeling.
        """
        if prompt_template is None:
            prompt_template = self.get_default_notes_prompt_template()
        mapping_table = self.mapping_to_markdown_table(speaker_mapping)
        prompt = prompt_template.format(transcript=transcript, mapping_table=mapping_table)
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
            )
            notes = self.parse_notes(response.text)
            return notes
        except Exception as e:
            logger.error(f"Gemini API error (meeting notes): {e}")
            raise RuntimeError(f"Gemini API error (meeting notes): {e}")

    # infer_speakers_and_generate_notes is inherited and calls the above two methods

    def ask_question_about_meeting(self, user_question: str, meeting_notes: str, diarized_transcript: str, conversation_history: list = None, timeout: int = 60, recording_id: str = None):
        # If recording_id is provided, use mapped transcript
        if recording_id is not None:
            diarized_transcript = self.get_mapped_transcript_for_llm(recording_id)
        prompt = f"""
You are a helpful AI assistant. You have access to the following meeting notes and full diarized transcript. Use this information to answer the user's question as accurately as possible. If the answer is not present, say so.

# Meeting Notes:
{meeting_notes}

# Full Diarized Transcript:
{diarized_transcript}

User Question: {user_question}
"""
        contents = []
        if conversation_history:
            contents.extend(conversation_history)
        contents.append({"role": "user", "parts": [{"text": prompt}]})
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
            )
            return response.text
        except Exception as e:
            logger.error(f"Gemini API error (chat): {e}")
            raise RuntimeError(f"Gemini API error (chat): {e}")

class OpenAILLMBackend(LLMBackend):
    def __init__(self, api_key=None, model=None):
        if api_key is None:
            api_key = config_manager.get("openai_api_key")
        if not api_key:
            raise ValueError("OpenAI API key is not set. Please provide it in settings.")
        self.api_key = api_key
        self.model = model or "gpt-3.5-turbo"
        openai.api_key = self.api_key

    def infer_speakers(self, transcript: str, prompt_template: str = None, timeout: int = 60) -> Dict[str, str]:
        """
        Run speaker inference on the transcript and return a mapping from diarization label to inferred name/role.
        Can be called independently of meeting notes generation.
        """
        if prompt_template is None:
            prompt_template = self.get_default_speaker_prompt_template()
        prompt = prompt_template.format(transcript=transcript)
        try:
            response = openai.ChatCompletion.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                timeout=timeout
            )
            text = response["choices"][0]["message"]["content"]
            mapping = self.parse_mapping_table(text)
            return mapping
        except Exception as e:
            logger.error(f"OpenAI API error (speaker mapping): {e}")
            raise RuntimeError(f"OpenAI API error (speaker mapping): {e}")

    def generate_meeting_notes(self, transcript: str, speaker_mapping: Dict[str, str], prompt_template: str = None, timeout: int = 60) -> str:
        """
        Generate meeting notes using the provided speaker mapping. Should be called after user relabeling.
        """
        if prompt_template is None:
            prompt_template = self.get_default_notes_prompt_template()
        mapping_table = self.mapping_to_markdown_table(speaker_mapping)
        prompt = prompt_template.format(transcript=transcript, mapping_table=mapping_table)
        try:
            response = openai.ChatCompletion.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                timeout=timeout
            )
            text = response["choices"][0]["message"]["content"]
            notes = self.parse_notes(text)
            return notes
        except Exception as e:
            logger.error(f"OpenAI API error (meeting notes): {e}")
            raise RuntimeError(f"OpenAI API error (meeting notes): {e}")

    # infer_speakers_and_generate_notes is inherited and calls the above two methods

    def ask_question_about_meeting(self, user_question: str, meeting_notes: str, diarized_transcript: str, conversation_history: list = None, timeout: int = 60, recording_id: str = None):
        # If recording_id is provided, use mapped transcript
        if recording_id is not None:
            diarized_transcript = self.get_mapped_transcript_for_llm(recording_id)
        prompt = f"""
You are a helpful AI assistant. You have access to the following meeting notes and full diarized transcript. Use this information to answer the user's question as accurately as possible. If the answer is not present, say so.

# Meeting Notes:
{meeting_notes}

# Full Diarized Transcript:
{diarized_transcript}

User Question: {user_question}
"""
        messages = []
        if conversation_history:
            for msg in conversation_history:
                if msg.get("role") and msg.get("parts"):
                    for part in msg["parts"]:
                        messages.append({"role": msg["role"], "content": part["text"]})
        messages.append({"role": "user", "content": prompt})
        try:
            response = openai.ChatCompletion.create(
                model=self.model,
                messages=messages,
                temperature=0.2,
                timeout=timeout
            )
            return response["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error(f"OpenAI API error (chat): {e}")
            raise RuntimeError(f"OpenAI API error (chat): {e}")

class AnthropicLLMBackend(LLMBackend):
    def __init__(self, api_key=None, model=None):
        if api_key is None:
            api_key = config_manager.get("anthropic_api_key")
        if not api_key:
            raise ValueError("Anthropic API key is not set. Please provide it in settings.")
        self.api_key = api_key
        self.model = model or "claude-3-opus-20240229"
        self.client = anthropic.Anthropic(api_key=self.api_key)

    def infer_speakers(self, transcript: str, prompt_template: str = None, timeout: int = 60) -> Dict[str, str]:
        """
        Run speaker inference on the transcript and return a mapping from diarization label to inferred name/role.
        Can be called independently of meeting notes generation.
        """
        if prompt_template is None:
            prompt_template = self.get_default_speaker_prompt_template()
        prompt = prompt_template.format(transcript=transcript)
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
            prompt_template = self.get_default_notes_prompt_template()
        mapping_table = self.mapping_to_markdown_table(speaker_mapping)
        prompt = prompt_template.format(transcript=transcript, mapping_table=mapping_table)
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
        prompt = f"""
You are a helpful AI assistant. You have access to the following meeting notes and full diarized transcript. Use this information to answer the user's question as accurately as possible. If the answer is not present, say so.

# Meeting Notes:
{meeting_notes}

# Full Diarized Transcript:
{diarized_transcript}

User Question: {user_question}
"""
        messages = []
        if conversation_history:
            for msg in conversation_history:
                if msg.get("role") and msg.get("parts"):
                    for part in msg["parts"]:
                        messages.append({"role": msg["role"], "content": part["text"]})
        messages.append({"role": "user", "content": prompt})
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

def _get_default_model_for_provider(provider: str) -> str:
    """Return the hardcoded default model for each provider."""
    if provider == "gemini":
        return "gemini-2.5-flash-preview-05-20"
    elif provider == "openai":
        return "gpt-4.1-mini-2025-04-14"
    elif provider == "anthropic":
        return "claude-3-7-sonnet-latest"
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")

# --- LLM Backend Factory ---
def get_llm_backend(provider: str, api_key=None, model=None):
    """
    Factory function to instantiate the appropriate LLM backend.
    Heavy dependencies are only imported when needed.
    Args:
        provider (str): 'gemini', 'openai', or 'anthropic'
        api_key (str): API key for the provider (optional)
        model (str): Model name (optional)
    Returns:
        Instance of the appropriate LLMBackend subclass.
    Raises:
        ValueError: If provider is unknown.
    """
    from nojoin.utils.config_manager import config_manager
    if model is None:
        model = config_manager.get(f"{provider}_model") or _get_default_model_for_provider(provider)
    if provider == "gemini":
        from nojoin.processing.LLM_Services import GeminiLLMBackend
        return GeminiLLMBackend(api_key=api_key, model=model)
    elif provider == "openai":
        from nojoin.processing.LLM_Services import OpenAILLMBackend
        return OpenAILLMBackend(api_key=api_key, model=model)
    elif provider == "anthropic":
        from nojoin.processing.LLM_Services import AnthropicLLMBackend
        return AnthropicLLMBackend(api_key=api_key, model=model)
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")