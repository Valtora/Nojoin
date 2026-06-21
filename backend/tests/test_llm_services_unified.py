import json
from types import SimpleNamespace

import pytest

from backend.processing.llm_services import (
    AnthropicLLMBackend,
    GeminiLLMBackend,
    OllamaLLMBackend,
    OpenAILLMBackend,
    SecondaryLLMBackend,
)
from backend.utils.meeting_edge import MeetingEdgeRequest
from backend.utils.meeting_intelligence import (
    AutomaticMeetingIntelligenceRequest,
    AutomaticMeetingIntelligenceResult,
)


def _sample_request() -> AutomaticMeetingIntelligenceRequest:
    return AutomaticMeetingIntelligenceRequest(
        resolved_transcript="[00:00 - 00:05] Speaker 1: Status update.",
        unresolved_speakers=("SPEAKER_00",),
        user_notes="Confirm the rollout date",
        prefer_short_titles=True,
        output_language_instruction=(
            "Write the meeting title and notes in English (British). Use British spelling."
        ),
    )


def _sample_payload() -> str:
    return json.dumps(
        {
            "speaker_mapping": {"SPEAKER_00": "Alex"},
            "title": "Launch Readiness Review",
            "notes_markdown": "# Meeting Notes\n\n## Summary\nAll teams are ready.",
        }
    )


def _sample_meeting_edge_payload() -> str:
    return json.dumps(
        {
            "summary": "Launch timing is the active thread.",
            "rolling_summary": "The team is reviewing launch readiness and open risks.",
            "questions": ["Who owns final launch approval?"],
            "points": [],
            "concepts": [],
        }
    )


class _FakeOllamaResponse:
    def __init__(self, payload: str):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"message": {"content": self._payload}}


class _FakeOllamaStreamResponse:
    def __init__(self, chunks: list[dict[str, object]]):
        self._chunks = chunks

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self):
        for chunk in self._chunks:
            yield json.dumps(chunk).encode("utf-8")


def test_gemini_title_prompt_includes_output_language_instruction() -> None:
    capture: dict[str, object] = {}

    class FakeModels:
        def generate_content(self, *, model: str, contents: str):
            capture["contents"] = contents
            return SimpleNamespace(text="Préparation du lancement")

    backend = object.__new__(GeminiLLMBackend)
    backend.model = "gemini-test"
    backend.client = SimpleNamespace(models=FakeModels())

    title = backend.infer_meeting_title(
        "SPEAKER_00: Bonjour.",
        output_language_instruction="Write the meeting title in French.",
    )

    assert title == "Préparation du lancement"
    assert "# Output Language" in str(capture["contents"])
    assert "in French" in str(capture["contents"])


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
    assert "English (British)" in str(capture["contents"])


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
        def post(
            self,
            url: str,
            json: dict,
            timeout: int,
            allow_redirects: bool,
        ):
            capture["url"] = url
            capture["json"] = json
            capture["timeout"] = timeout
            capture["allow_redirects"] = allow_redirects
            return _FakeOllamaResponse(_sample_payload())

    backend = object.__new__(OllamaLLMBackend)
    backend.model = "llama3"
    backend.api_url = "http://ollama.local"
    backend.context_window = 131072
    backend.requests = FakeRequests()

    result = backend.generate_meeting_intelligence(_sample_request(), timeout=30)

    assert result.speaker_mapping == {"SPEAKER_00": "Alex"}
    assert "## User Notes" in result.notes_markdown
    assert capture["timeout"] == 30
    assert capture["allow_redirects"] is False
    assert capture["url"] == "http://ollama.local/api/chat"
    assert capture["json"]["stream"] is False
    assert capture["json"]["format"] == "json"
    assert capture["json"]["options"]["temperature"] == 0.3
    assert capture["json"]["options"]["num_ctx"] == 131072


def test_ollama_generate_meeting_intelligence_repairs_contract_failure() -> None:
    calls: list[dict[str, object]] = []

    class FakeRequests:
        def post(
            self,
            url: str,
            json: dict,
            timeout: int,
            allow_redirects: bool,
        ):
            calls.append(
                {
                    "url": url,
                    "json": json,
                    "timeout": timeout,
                    "allow_redirects": allow_redirects,
                }
            )
            if len(calls) == 1:
                return _FakeOllamaResponse('{"speaker_mapping": {"SPEAKER_00": "Alex"}')
            return _FakeOllamaResponse(_sample_payload())

    backend = object.__new__(OllamaLLMBackend)
    backend.model = "gemma4:latest"
    backend.api_url = "http://ollama.local"
    backend.requests = FakeRequests()

    result = backend.generate_meeting_intelligence(_sample_request(), timeout=30)

    assert result.title == "Launch Readiness Review"
    assert len(calls) == 2
    assert calls[0]["json"]["format"] == "json"
    assert calls[1]["json"]["format"] == "json"
    assert calls[1]["json"]["options"]["temperature"] == 0.0
    repair_prompt = calls[1]["json"]["messages"][0]["content"]
    assert "Validation error:" in repair_prompt
    assert "Previous Invalid Response" in repair_prompt
    assert "Return a corrected response" in repair_prompt


def test_ollama_streaming_chat_raises_when_context_exhausted() -> None:
    capture: dict[str, object] = {}

    class FakeRequests:
        def post(
            self,
            url: str,
            json: dict,
            stream: bool,
            timeout: int,
            allow_redirects: bool,
        ):
            capture["json"] = json
            return _FakeOllamaStreamResponse(
                [
                    {
                        "message": {"role": "assistant", "content": "Based"},
                        "done": False,
                    },
                    {
                        "message": {"role": "assistant", "content": ""},
                        "done": True,
                        "done_reason": "length",
                        "prompt_eval_count": 4095,
                        "eval_count": 1,
                    },
                ]
            )

    backend = object.__new__(OllamaLLMBackend)
    backend.model = "gemma4:latest"
    backend.api_url = "http://ollama.local"
    backend.context_window = 131072
    backend.requests = FakeRequests()

    with pytest.raises(RuntimeError, match="context window was exhausted"):
        list(
            backend.ask_question_streaming(
                user_question="What should I do next?",
                meeting_notes="",
                diarized_transcript="Full transcript",
            )
        )

    assert capture["json"]["options"]["temperature"] == 0.3
    assert capture["json"]["options"]["num_ctx"] == 131072


def test_ollama_generate_meeting_edge_accepts_empty_signal_payload() -> None:
    calls: list[dict[str, object]] = []
    empty_signal_payload = json.dumps(
        {
            "summary": "Budget is being discussed.",
            "rolling_summary": "The meeting is in a short budget discussion.",
            "questions": [],
            "points": [],
            "concepts": [],
        }
    )

    class FakeRequests:
        def post(
            self,
            url: str,
            json: dict,
            timeout: int,
            allow_redirects: bool,
        ):
            calls.append(
                {
                    "url": url,
                    "json": json,
                    "timeout": timeout,
                    "allow_redirects": allow_redirects,
                }
            )
            return _FakeOllamaResponse(empty_signal_payload)

    backend = object.__new__(OllamaLLMBackend)
    backend.model = "gemma4:latest"
    backend.api_url = "http://ollama.local"
    backend.requests = FakeRequests()

    result = backend.generate_meeting_edge(
        MeetingEdgeRequest(recent_transcript="Speaker A: We need final approval before launch."),
        timeout=20,
    )

    assert result.summary == "Budget is being discussed."
    assert result.questions == ()
    assert result.points == ()
    assert result.concepts == ()
    assert len(calls) == 1
    assert calls[0]["json"]["format"] == "json"


def test_ollama_generate_meeting_edge_repairs_malformed_payload() -> None:
    calls: list[dict[str, object]] = []

    class FakeRequests:
        def post(
            self,
            url: str,
            json: dict,
            timeout: int,
            allow_redirects: bool,
        ):
            calls.append(
                {
                    "url": url,
                    "json": json,
                    "timeout": timeout,
                    "allow_redirects": allow_redirects,
                }
            )
            if len(calls) == 1:
                return _FakeOllamaResponse('{"summary": "Budget is being discussed."')
            return _FakeOllamaResponse(_sample_meeting_edge_payload())

    backend = object.__new__(OllamaLLMBackend)
    backend.model = "gemma4:latest"
    backend.api_url = "http://ollama.local"
    backend.requests = FakeRequests()

    result = backend.generate_meeting_edge(
        MeetingEdgeRequest(recent_transcript="Speaker A: We need final approval before launch."),
        timeout=20,
    )

    assert result.questions == ("Who owns final launch approval?",)
    assert len(calls) == 2
    assert calls[0]["json"]["format"] == "json"
    assert calls[1]["json"]["format"] == "json"
    assert "Could not parse a Meeting Edge JSON object" in calls[1]["json"]["messages"][0]["content"]


def test_secondary_fallback_runs_after_primary_repair_failure() -> None:
    calls: list[dict[str, object]] = []

    class FakeRequests:
        def post(
            self,
            url: str,
            json: dict,
            timeout: int,
            allow_redirects: bool,
        ):
            calls.append({"url": url, "json": json})
            return _FakeOllamaResponse('{"speaker_mapping": {"SPEAKER_00": "Alex"}')

    primary = object.__new__(OllamaLLMBackend)
    primary.model = "gemma4:latest"
    primary.api_url = "http://ollama.local"
    primary.requests = FakeRequests()

    class FakeSecondary:
        model = "gemini-flash-lite-latest"

        def generate_meeting_intelligence(self, request, prompt_template=None, timeout=60):
            calls.append({"secondary_request": request})
            return AutomaticMeetingIntelligenceResult(
                speaker_mapping={"SPEAKER_00": "Jordan"},
                title="Fallback Notes",
                notes_markdown="# Meeting Notes\n\n## Summary\nFallback succeeded.",
            )

    backend = SecondaryLLMBackend(primary=primary, secondary=FakeSecondary())

    result = backend.generate_meeting_intelligence(_sample_request(), timeout=30)

    assert result.title == "Fallback Notes"
    assert result.speaker_mapping == {"SPEAKER_00": "Jordan"}
    assert len(calls) == 3
    assert calls[-1]["secondary_request"].output_language_instruction is not None
    assert "English (British)" in calls[-1]["secondary_request"].output_language_instruction
