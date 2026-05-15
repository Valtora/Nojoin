import json
from types import SimpleNamespace

from backend.processing.llm_services import (
    AnthropicLLMBackend,
    GeminiLLMBackend,
    OllamaLLMBackend,
    OpenAILLMBackend,
)
from backend.utils.meeting_intelligence import AutomaticMeetingIntelligenceRequest


def _sample_request() -> AutomaticMeetingIntelligenceRequest:
    return AutomaticMeetingIntelligenceRequest(
        resolved_transcript="[00:00 - 00:05] Speaker 1: Status update.",
        unresolved_speakers=("SPEAKER_00",),
        user_notes="Confirm the rollout date",
        prefer_short_titles=True,
    )


def _sample_payload() -> str:
    return json.dumps(
        {
            "speaker_mapping": {"SPEAKER_00": "Alex"},
            "title": "Launch Readiness Review",
            "notes_markdown": "# Meeting Notes\n\n## Summary\nAll teams are ready.",
        }
    )


class _FakeOllamaResponse:
    def __init__(self, payload: str):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"message": {"content": self._payload}}


def test_gemini_generate_meeting_intelligence_uses_shared_contract() -> None:
    capture: dict[str, object] = {}

    class FakeModels:
        def generate_content(self, *, model: str, contents: str):
            capture["model"] = model
            capture["contents"] = contents
            return SimpleNamespace(text=_sample_payload())

    backend = object.__new__(GeminiLLMBackend)
    backend.model = "gemini-test"
    backend.client = SimpleNamespace(models=FakeModels())

    result = backend.generate_meeting_intelligence(_sample_request(), timeout=123)

    assert result.speaker_mapping == {"SPEAKER_00": "Alex"}
    assert result.title == "Launch Readiness Review"
    assert "## User Notes" in result.notes_markdown
    assert "- SPEAKER_00" in str(capture["contents"])


def test_openai_generate_meeting_intelligence_uses_shared_contract() -> None:
    capture: dict[str, object] = {}

    class FakeCompletions:
        def create(self, **kwargs):
            capture.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=_sample_payload())
                    )
                ]
            )

    backend = object.__new__(OpenAILLMBackend)
    backend.model = "gpt-5.1"
    backend.client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )

    result = backend.generate_meeting_intelligence(_sample_request(), timeout=45)

    assert result.speaker_mapping == {"SPEAKER_00": "Alex"}
    assert "## User Notes" in result.notes_markdown
    assert capture["timeout"] == 45
    assert capture["temperature"] == 0.2
    assert "Return valid JSON only" in str(capture["messages"][0]["content"])


def test_anthropic_generate_meeting_intelligence_uses_shared_contract() -> None:
    capture: dict[str, object] = {}

    class FakeMessages:
        def create(self, **kwargs):
            capture.update(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(text=_sample_payload())])

    backend = object.__new__(AnthropicLLMBackend)
    backend.model = "claude-test"
    backend.client = SimpleNamespace(messages=FakeMessages())

    result = backend.generate_meeting_intelligence(_sample_request(), timeout=90)

    assert result.speaker_mapping == {"SPEAKER_00": "Alex"}
    assert "## User Notes" in result.notes_markdown
    assert capture["max_tokens"] == 4096
    assert capture["messages"][0]["content"].startswith("You are an expert meeting intelligence assistant.")


def test_ollama_generate_meeting_intelligence_uses_shared_contract() -> None:
    capture: dict[str, object] = {}

    class FakeRequests:
        def post(self, url: str, json: dict, timeout: int):
            capture["url"] = url
            capture["json"] = json
            capture["timeout"] = timeout
            return _FakeOllamaResponse(_sample_payload())

    backend = object.__new__(OllamaLLMBackend)
    backend.model = "llama3"
    backend.api_url = "http://ollama.local"
    backend.requests = FakeRequests()

    result = backend.generate_meeting_intelligence(_sample_request(), timeout=30)

    assert result.speaker_mapping == {"SPEAKER_00": "Alex"}
    assert "## User Notes" in result.notes_markdown
    assert capture["timeout"] == 30
    assert capture["url"] == "http://ollama.local/api/chat"
    assert capture["json"]["stream"] is False