"""scripts/dev/fake_llm_server.py stands in for Ollama in a test stack (#702).

Each flow runs Nojoin's real Ollama backend against the fake server on
loopback, so a prompt the server misreads, or an answer that breaks a JSON
contract, fails here before it fails an end-to-end run.
"""

import importlib.util
import json
import sys
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pytest

from backend.processing.llm_backends import model_probe
from backend.processing.llm_backends import ollama as ollama_module
from backend.processing.llm_backends import ollama_model_facts as ollama_facts_module
from backend.processing.llm_backends.base import LLMBackend
from backend.processing.llm_backends.failures import Failure, classify_failure
from backend.processing.llm_backends.ollama import OllamaLLMBackend
from backend.utils.context_window import ContextWindowExceededError
from backend.utils.meeting_analysis import (
    MeetingAnalysisRequest,
    build_quote_index,
    build_speaker_allowlist,
)
from backend.utils.meeting_edge import MeetingEdgeRequest, build_meeting_edge_prompt
from backend.utils.meeting_intelligence import AutomaticMeetingIntelligenceRequest
from backend.utils.notes_structure_generator import (
    build_notes_structure_generator_prompt,
    parse_generated_notes_structure,
)
from backend.utils.vision import VisionImage, VisionUnsupportedError

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "dev" / "fake_llm_server.py"
TRANSCRIPT = (
    "[00:00:01 - 00:00:05] SPEAKER_00: Hello, I'm Alice Example.\n"
    "[00:00:06 - 00:00:09] SPEAKER_01: Thanks Alice, let's review the budget."
)


def _load_script():
    spec = importlib.util.spec_from_file_location("nojoin_fake_llm_server", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # dataclasses looks the module up in sys.modules while building Answer.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fake = _load_script()


@contextmanager
def _serving(**options):
    server = fake.build_server("127.0.0.1", 0, **options)
    # A short poll keeps shutdown() from waiting out the default half second.
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def fake_llm_url():
    with _serving() as url:
        yield url


@pytest.fixture
def make_backend(fake_llm_url, monkeypatch):
    """Build Ollama backends on the fake server.

    The URL policy refuses loopback for Ollama, so it is bypassed here; the
    fake server is the only thing these backends can reach.
    """
    monkeypatch.setattr(
        ollama_module, "validate_ollama_api_url", lambda url, **_: url.rstrip("/")
    )
    monkeypatch.setattr(ollama_facts_module, "_model_facts_cache", {})

    def _make(model="fake-llm", context_window=32768, api_url=None):
        return OllamaLLMBackend(
            api_url=api_url or fake_llm_url, model=model, context_window=context_window
        )

    return _make


def _post(url, path, body):
    request = urllib.request.Request(
        f"{url}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, response.read()


def test_the_models_are_listed_with_their_capabilities(make_backend):
    backend = make_backend()

    assert backend.list_models() == [
        "fake-401:latest",
        "fake-429:latest",
        "fake-500:latest",
        "fake-llm:latest",
        "fake-slow:latest",
        "fake-text:latest",
    ]
    assert backend.validate_api_key() is True
    assert backend.supports_vision() is True
    assert make_backend(model="fake-text:latest").supports_vision() is False
    assert backend._context_window() == 32768


def test_automatic_meeting_intelligence_completes(make_backend):
    request = AutomaticMeetingIntelligenceRequest(
        resolved_transcript=TRANSCRIPT, unresolved_speakers=("SPEAKER_00",)
    )

    result = make_backend().generate_meeting_intelligence(request)

    assert result.title == fake.TITLE
    assert result.notes_markdown.startswith("## Summary")
    assert result.speaker_mapping == {}


def test_regenerated_notes_title_and_speaker_suggestions_complete(make_backend):
    backend = make_backend()

    assert backend.generate_meeting_notes(TRANSCRIPT, {}).startswith("## Summary")
    assert backend.infer_meeting_title(TRANSCRIPT) == fake.TITLE
    suggestions = backend.infer_speaker_suggestions(
        TRANSCRIPT, eligible_labels=["SPEAKER_00"]
    )
    assert suggestions.suggestions == ()


def test_meeting_edge_completes(make_backend):
    result = make_backend().generate_meeting_edge(
        MeetingEdgeRequest(recent_transcript=TRANSCRIPT)
    )

    assert result.summary
    assert result.questions == ("Is the fake LLM server answering?",)
    assert [c.term for c in result.concepts] == ["Fake LLM server"]


def test_meeting_analysis_completes(make_backend):
    request = MeetingAnalysisRequest(
        transcript=TRANSCRIPT,
        allowlist=build_speaker_allowlist(
            [{"speaker_key": "rs:1", "name": "Alice Example"}]
        ),
        quotes=build_quote_index([TRANSCRIPT], 9.0),
    )

    result = make_backend().generate_meeting_analysis(request)

    assert result.topics == () and result.decisions == ()


def test_meeting_chat_streams_and_answers(make_backend):
    backend = make_backend()

    streamed = "".join(
        backend.ask_question_streaming("What was decided?", "## Summary", TRANSCRIPT)
    )

    assert streamed == fake.CHAT_ANSWER
    assert (
        backend.ask_question_about_meeting("What was decided?", "", TRANSCRIPT)
        == fake.CHAT_ANSWER
    )


def test_image_parsing_needs_the_vision_model(make_backend):
    image = VisionImage(data=b"\x89PNG fake", media_type="image/png")

    text = make_backend().generate_text_from_images("Transcribe this page.", [image])

    assert text.startswith("## Page") and "1 image" in text
    with pytest.raises(VisionUnsupportedError):
        make_backend(model="fake-text").generate_text_from_images("Page.", [image])


def test_a_notes_structure_can_be_generated(make_backend):
    text = make_backend().generate_text(
        build_notes_structure_generator_prompt("A weekly team meeting.")
    )

    assert parse_generated_notes_structure(text).name == "Fake structure"


def test_a_meeting_longer_than_the_window_is_written_in_parts(make_backend):
    """A small window drives the long-meeting path through the fake's refusal."""
    backend = make_backend(context_window=4096)
    line = (
        "[00:00:01 - 00:00:05] SPEAKER_00: We went through the budget line by line.\n"
    )
    long_transcript = line * 600
    request = AutomaticMeetingIntelligenceRequest(
        resolved_transcript=long_transcript, unresolved_speakers=("SPEAKER_00",)
    )

    with pytest.raises(ContextWindowExceededError) as refused:
        backend.generate_meeting_intelligence(request)
    assert refused.value.prompt_tokens and refused.value.window == 4096

    result = backend.generate_meeting_intelligence_in_parts(
        request, overflow=refused.value
    )
    assert result.title == fake.TITLE
    assert result.notes_markdown.startswith("## Summary")

    with pytest.raises(ContextWindowExceededError) as notes_refused:
        backend.generate_meeting_notes(long_transcript, {})
    notes = backend.generate_meeting_notes_in_parts(
        long_transcript, {}, overflow=notes_refused.value
    )
    assert notes.startswith("## Summary")


def test_test_connection_passes_on_each_listed_model(make_backend):
    results = model_probe.run_probes(
        lambda model: make_backend(model=model),
        "ollama",
        [("fake-llm:latest", "main"), ("fake-text:latest", "meeting_edge")],
        include_detail=True,
    )

    assert [(r.model, r.kind) for r in results] == [
        ("fake-llm:latest", "ok"),
        ("fake-text:latest", "ok"),
    ]


def test_test_connection_reads_a_model_it_does_not_list_as_unknown(make_backend):
    (result,) = model_probe.run_probes(
        lambda model: make_backend(model=model),
        "ollama",
        [("llama3", "main")],
        include_detail=False,
    )

    assert result.kind == "unknown_model"


def test_the_probe_prompt_matches_test_connection():
    """The server keeps a copy of the prompt, so a change to either fails here."""
    assert fake.PROBE_PROMPT == model_probe.PROBE_PROMPT
    assert fake.classify(model_probe.PROBE_PROMPT, wants_json=True) == "probe"


def _live_edge_failure(backend, timeout=5):
    with pytest.raises(RuntimeError) as raised:
        backend.for_live_calls().generate_meeting_edge(
            MeetingEdgeRequest(recent_transcript=TRANSCRIPT), timeout=timeout
        )
    return classify_failure(raised.value)


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("fake-401", Failure("auth")),
        ("fake-429", Failure("rate_limit", 60)),
        ("fake-500", Failure("unavailable")),
    ],
)
def test_a_failing_model_fails_as_its_name_says(make_backend, model, expected):
    assert _live_edge_failure(make_backend(model=model)) == expected


def test_the_rate_limits_retry_after_can_be_set(make_backend):
    with _serving(retry_after=7) as url:
        backend = make_backend(model="fake-429", api_url=url)

        assert _live_edge_failure(backend) == Failure("rate_limit", 7)


def test_the_slow_model_answers_only_after_its_delay(make_backend):
    with _serving(slow_seconds=0.5) as url:
        backend = make_backend(model="fake-slow", api_url=url)

        assert _live_edge_failure(backend, timeout=0.2) == Failure("timeout")
        result = backend.generate_meeting_edge(
            MeetingEdgeRequest(recent_transcript=TRANSCRIPT), timeout=5
        )
    assert result.questions == ("Is the fake LLM server answering?",)


def test_a_json_repair_prompt_keeps_its_original_task():
    original = build_meeting_edge_prompt(
        MeetingEdgeRequest(recent_transcript=TRANSCRIPT)
    )
    repair = LLMBackend.build_json_repair_prompt(
        original_prompt=original,
        invalid_response="not json",
        validation_error=ValueError("summary must be a non-empty string"),
    )

    assert fake.classify(repair, wants_json=True) == "meeting_edge"


def test_answers_are_byte_identical_between_runs(fake_llm_url):
    body = {
        "model": "fake-llm",
        "messages": [{"role": "user", "content": "Hello"}],
        "stream": True,
    }

    first = _post(fake_llm_url, "/api/chat", body)
    second = _post(fake_llm_url, "/api/chat", body)

    assert first == second
    lines = [json.loads(line) for line in first[1].splitlines()]
    assert lines[-1]["done"] is True and lines[-1]["done_reason"] == "stop"
    assert "".join(c["message"]["content"] for c in lines) == fake.CHAT_ANSWER


def test_an_unknown_model_is_not_found(fake_llm_url):
    with pytest.raises(urllib.error.HTTPError) as error:
        _post(
            fake_llm_url,
            "/api/chat",
            {"model": "llama3", "messages": [{"role": "user", "content": "Hi"}]},
        )

    assert error.value.code == 404
    assert "not found" in json.loads(error.value.read())["error"]


def test_the_root_answers_as_ollama_does(fake_llm_url):
    with urllib.request.urlopen(f"{fake_llm_url}/", timeout=5) as response:
        assert response.read() == b"Ollama is running"
