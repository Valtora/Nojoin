"""A meeting too long for the Ollama window: the secondary first, then parts.

Ollama refuses a notes prompt longer than its window (see
test_ollama_context_window). The refusal keeps its type through the backend,
so a configured secondary provider gets the whole meeting first, and only
when it fails too, or none is configured, are the notes written in parts.
"""

from __future__ import annotations

import pytest

from backend.processing.llm_backends.factory import SecondaryLLMBackend
from backend.processing.llm_backends.ollama import PARTS_ESTIMATED_BYTES_PER_TOKEN
from backend.processing.llm_backends.ollama_context import OllamaContextOverflowError
from backend.tests.test_ollama_context_window import (
    FITS,
    _answer,
    _backend,
    _llama_server_overflow,
    _refusal,
    _Response,
)
from backend.utils.context_window import ContextWindowExceededError
from backend.utils.meeting_intelligence import AutomaticMeetingIntelligenceRequest

TRANSCRIPT = "\n".join(
    f"[{minute:02d}:00 - {minute:02d}:59] Ana: "
    + " ".join(f"m{minute}w{word}" for word in range(60))
    + "."
    for minute in range(90)
)
# What a byte of prompt costs on this fake server.
TOKENS_PER_BYTE = 0.3


class _Ollama:
    """Refuses a prompt past its window as Ollama 0.40 does, with its count.

    Answers a draft and a combining call with fixed notes. Given ``mapping``,
    each draft also names speakers and the final call gives a title, in the
    tagged lines the automatic pipeline reads.
    """

    def __init__(self, window: int, *, mapping: dict | None = None):
        self.window = window
        self.mapping = mapping
        self.chat_payloads: list[dict] = []

    def post(self, url, **kwargs):
        if url.endswith("/api/show"):
            return _Response({"error": "not found"}, status_code=404)
        payload = kwargs["json"]
        self.chat_payloads.append(payload)
        prompt = payload["messages"][-1]["content"]
        tokens = int(len(prompt.encode()) * TOKENS_PER_BYTE)
        if tokens > self.window:
            return _refusal(_llama_server_overflow(tokens, self.window))
        if "# Transcript Part" in prompt:
            notes = "## Summary\nPart."
            if self.mapping is None:
                return _answer(FITS, notes)
            names = "".join(
                f"{label}: {name}\n" for label, name in self.mapping.items()
            )
            return _answer(FITS, f"{notes}\n\n<speaker_names>\n{names}</speaker_names>")
        if self.mapping is None or "# Title Rules" not in prompt:
            return _answer(FITS, "## Summary\nAll of it.")
        return _answer(
            FITS,
            "<title>Budget Review</title>\n<gist>The budget was reviewed.</gist>\n"
            "## Summary\nSPEAKER_01 spoke.",
        )

    def drafts(self) -> list[dict]:
        return [
            p
            for p in self.chat_payloads
            if "# Transcript Part" in p["messages"][-1]["content"]
        ]


def _refusal_of_whole(fake: _Ollama, call) -> ContextWindowExceededError:
    with pytest.raises(OllamaContextOverflowError) as error:
        call()
    return error.value


def test_a_refused_meeting_keeps_its_type_and_ollamas_count():
    fake = _Ollama(window=8192)
    backend = _backend(fake, context_window=8192)

    overflow = _refusal_of_whole(
        fake, lambda: backend.generate_meeting_notes(TRANSCRIPT, {}, timeout=5)
    )

    assert overflow.window == 8192
    assert overflow.prompt_tokens > 8192


def test_the_parts_budget_is_measured_from_ollamas_count_when_it_gives_one():
    backend = _backend(_Ollama(window=8192), context_window=8192)
    prompt = "x" * 4000

    measured = backend._parts_budget(
        ContextWindowExceededError("refused", prompt_tokens=1000, window=4096), prompt
    )
    estimated = backend._parts_budget(ContextWindowExceededError("refused"), prompt)

    assert measured.window == 4096
    assert measured.tokens_per_byte == pytest.approx(0.25 * 1.1)
    assert estimated.window == 8192
    assert estimated.tokens_per_byte == 1 / PARTS_ESTIMATED_BYTES_PER_TOKEN


def test_ollama_writes_the_notes_in_parts_within_its_window():
    fake = _Ollama(window=8192)
    backend = _backend(fake, context_window=8192)
    overflow = _refusal_of_whole(
        fake, lambda: backend.generate_meeting_notes(TRANSCRIPT, {}, timeout=5)
    )

    notes = backend.generate_meeting_notes_in_parts(
        TRANSCRIPT, {}, overflow=overflow, timeout=5, user_notes="Bring the budget"
    )

    assert notes.startswith("## Summary\nAll of it.")
    drafts = fake.drafts()
    assert len(drafts) >= 2
    # The user's notes section reaches every part, not only the first.
    assert all("Bring the budget" in d["messages"][-1]["content"] for d in drafts)
    # Every part fitted first time: the measured cost left room for answers.
    assert len(fake.chat_payloads) == 1 + len(drafts) + 1
    for payload in fake.chat_payloads[1:]:
        assert payload["options"]["num_ctx"] == 8192
        assert payload["truncate"] is False


def test_the_automatic_pipeline_runs_in_parts_with_json_answers():
    fake = _Ollama(window=8192, mapping={"SPEAKER_01": "Ana"})
    backend = _backend(fake, context_window=8192)
    request = AutomaticMeetingIntelligenceRequest(
        resolved_transcript=TRANSCRIPT, unresolved_speakers=("SPEAKER_01",)
    )
    overflow = _refusal_of_whole(
        fake, lambda: backend.generate_meeting_intelligence(request, timeout=5)
    )

    result = backend.generate_meeting_intelligence_in_parts(
        request, overflow=overflow, timeout=5
    )

    assert result.title == "Budget Review"
    assert result.speaker_mapping == {"SPEAKER_01": "Ana"}
    assert result.notes_markdown == "## Summary\nAna spoke."
    # The whole-meeting call asked for JSON; the parts never do.
    assert fake.chat_payloads[0].get("format") == "json"
    assert all("format" not in p for p in fake.chat_payloads[1:])


class _Hosted:
    def __init__(self, fails: bool = False):
        self.fails = fails
        self.calls = 0

    def generate_meeting_notes(self, *args, **kwargs):
        self.calls += 1
        if self.fails:
            raise RuntimeError("hosted provider is down")
        return "## Summary\nHosted, whole."


def test_a_secondary_provider_gets_the_whole_meeting_before_any_parts():
    fake = _Ollama(window=8192)
    hosted = _Hosted()
    chain = SecondaryLLMBackend(_backend(fake, context_window=8192), hosted)

    notes = chain.generate_meeting_notes(TRANSCRIPT, {}, timeout=5)

    assert notes == "## Summary\nHosted, whole."
    assert hosted.calls == 1
    assert len(fake.chat_payloads) == 1


def test_parts_are_written_by_ollama_once_the_secondary_also_failed():
    fake = _Ollama(window=8192)
    hosted = _Hosted(fails=True)
    chain = SecondaryLLMBackend(_backend(fake, context_window=8192), hosted)

    # The secondary's own failure is not what surfaces: the primary's refusal
    # does, so the caller knows to ask for parts.
    overflow = _refusal_of_whole(
        fake, lambda: chain.generate_meeting_notes(TRANSCRIPT, {}, timeout=5)
    )
    notes = chain.generate_meeting_notes_in_parts(
        TRANSCRIPT, {}, overflow=overflow, timeout=5
    )

    assert notes == "## Summary\nAll of it."
    assert hosted.calls == 1
    assert len(fake.drafts()) >= 2


class _Flaky(_Ollama):
    """Answers the first draft with a 500, as Ollama does an aborted answer."""

    def __init__(self, window: int):
        super().__init__(window)
        self.failed = False

    def post(self, url, **kwargs):
        prompt = kwargs.get("json", {}).get("messages", [{}])[-1].get("content", "")
        if "# Transcript Part" in prompt and not self.failed:
            self.failed = True
            self.chat_payloads.append(kwargs["json"])
            return _refusal(
                {"error": "prediction aborted, token repeat limit reached"}, 500
            )
        return super().post(url, **kwargs)


def test_one_server_error_does_not_cost_the_parts_already_written():
    fake = _Flaky(window=8192)
    backend = _backend(fake, context_window=8192)
    overflow = _refusal_of_whole(
        fake, lambda: backend.generate_meeting_notes(TRANSCRIPT, {}, timeout=5)
    )

    notes = backend.generate_meeting_notes_in_parts(
        TRANSCRIPT, {}, overflow=overflow, timeout=5
    )

    assert notes == "## Summary\nAll of it."
    first, second = fake.drafts()[:2]
    assert first == second
