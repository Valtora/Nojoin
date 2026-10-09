"""Ollama must never answer from a prompt it silently cut short.

Left at its defaults, Ollama answers a prompt longer than ``num_ctx`` from
whatever part of it fits (current releases keep about half the window), and
shifts the prompt out when the answer fills the window, each with an ordinary
"stop". Every request is therefore sent with ``truncate: false`` and
``shift: false``, so Ollama refuses instead, and the refusal is reported.

The response bodies below were recorded from qwen2.5:0.5b on Ollama 0.40.2,
0.34.2, 0.12.6 and 0.12.5 (which predates both fields and ignores them).
"""

from __future__ import annotations

import json

import pytest
from requests import HTTPError

from backend.processing.llm_backends import ollama as ollama_module
from backend.processing.llm_backends.factory import SecondaryLLMBackend
from backend.processing.llm_backends.ollama import OllamaLLMBackend


def _llama_server_overflow(prompt_tokens: int, n_ctx: int) -> dict:
    """The refusal of Ollama 0.34 and 0.40: llama-server's error, as a string."""
    detail = {
        "error": {
            "code": 400,
            "message": (
                f"request ({prompt_tokens} tokens) exceeds the available context "
                f"size ({n_ctx} tokens), try increasing it"
            ),
            "type": "exceed_context_size_error",
            "n_prompt_tokens": prompt_tokens,
            "n_ctx": n_ctx,
        }
    }
    return {"error": json.dumps(detail, separators=(",", ":"))}


OVERFLOW_0_40 = _llama_server_overflow(2622, 512)
OVERFLOW_0_12_6 = {"error": "the input length exceeds the context length"}
FITS = {"done": True, "done_reason": "stop", "prompt_eval_count": 318, "eval_count": 7}
WINDOW_FILLED_0_40 = {
    "done": True,
    "done_reason": "length",
    "prompt_eval_count": 61,
    "eval_count": 451,
}
WINDOW_FILLED_0_12_6 = {
    "done": True,
    "done_reason": "length",
    "prompt_eval_count": 61,
    "eval_count": 452,
}
# 0.12.5 ignores truncate: an overflow comes back cut to exactly num_ctx (512).
TRUNCATED_0_12_5 = {
    "done": True,
    "done_reason": "stop",
    "prompt_eval_count": 512,
    "eval_count": 11,
}
# Without shift:false the answer pushed a 496-token prompt past 512 (0.12.6).
SHIFTED_0_12_6 = {
    "done": True,
    "done_reason": "stop",
    "prompt_eval_count": 496,
    "eval_count": 30,
}
# 400s that are not about the window (0.34.2): an image for a text-only model
# arrives in the same llama-server envelope as the overflow, and bad base64.
MULTIMODAL_0_34 = {
    "error": json.dumps(
        {
            "error": {
                "code": 400,
                "message": (
                    "Multimodal data provided, but model does not support "
                    "multimodal requests."
                ),
                "type": "invalid_request_error",
            }
        },
        separators=(",", ":"),
    )
}
BAD_BASE64_0_34 = {"error": "illegal base64 data at input byte 0"}

ANSWER = "# Meeting Notes\n\n## Summary\nThe launch moves to Friday."


class _Response:
    def __init__(self, body: dict, status_code: int = 200, chunks=None):
        self._body = body
        self.status_code = status_code
        self._chunks = chunks or []

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise HTTPError(f"{self.status_code} Client Error", response=self)

    def json(self) -> dict:
        return self._body

    def iter_lines(self):
        for chunk in self._chunks:
            yield json.dumps(chunk).encode()


def _answer(metadata: dict, content: str = ANSWER) -> _Response:
    return _Response({"message": {"content": content}, **metadata})


def _refusal(body: dict, status_code: int = 400) -> _Response:
    return _Response(body, status_code=status_code)


class _FakeOllama:
    """Answers /api/show with a trained context length, /api/chat from a queue."""

    def __init__(self, *chat_responses: _Response, trained_context: int | None = None):
        self.trained_context = trained_context
        self.chat_responses = list(chat_responses)
        self.chat_payloads: list[dict] = []
        self.show_calls = 0

    def post(
        self,
        url: str,
        json: dict,
        timeout: int,
        allow_redirects: bool,
        stream: bool = False,
    ):
        if url.endswith("/api/show"):
            self.show_calls += 1
            if self.trained_context is None:
                return _Response({"error": "not found"}, status_code=404)
            return _Response(
                {
                    "model_info": {
                        "general.architecture": "qwen2",
                        "qwen2.context_length": self.trained_context,
                    },
                    "capabilities": ["completion"],
                }
            )
        self.chat_payloads.append(json)
        return self.chat_responses.pop(0)


@pytest.fixture(autouse=True)
def _fresh_model_facts_cache():
    ollama_module._model_facts_cache.clear()
    yield
    ollama_module._model_facts_cache.clear()


def _backend(fake: _FakeOllama, context_window: int) -> OllamaLLMBackend:
    backend = object.__new__(OllamaLLMBackend)
    backend.model = "qwen2.5:0.5b"
    backend.api_url = "http://ollama.local"
    backend.context_window = context_window
    backend.requests = fake
    return backend


def _notes(backend: OllamaLLMBackend) -> str:
    return backend.generate_meeting_notes("[00:00 - 00:04] Ana: hi", {}, timeout=5)


def _chat(backend: OllamaLLMBackend, history: list[dict] | None = None) -> str:
    return backend.ask_question_about_meeting(
        user_question="What was decided?",
        meeting_notes="notes",
        diarized_transcript="[00:00 - 00:04] Ana: we ship Friday",
        conversation_history=history,
        timeout=5,
    )


def _turns(count: int, size: int) -> list[dict]:
    roles = ("user", "model")
    return [
        {"role": roles[i % 2], "parts": [{"text": f"{i}" * size}]} for i in range(count)
    ]


def test_every_request_has_ollama_refuse_rather_than_cut_the_prompt():
    fake = _FakeOllama(_answer(FITS))
    backend = _backend(fake, context_window=32768)

    assert "The launch moves to Friday." in _notes(backend)

    payload = fake.chat_payloads[0]
    assert payload["truncate"] is False
    assert payload["shift"] is False
    assert payload["options"]["num_ctx"] == 32768
    # The window bounds the answer; a num_predict would override the Modelfile's.
    assert "num_predict" not in payload["options"]


@pytest.mark.parametrize(
    ("body", "window", "detail"),
    [
        (OVERFLOW_0_40, 512, "the window is 512 tokens and the prompt is 2,622 tokens"),
        (OVERFLOW_0_12_6, 1024, "the window is 1,024 tokens"),
    ],
    ids=["ollama-0.34-and-0.40", "ollama-0.12.6"],
)
def test_ollamas_overflow_refusal_is_reported_as_the_context_window(
    body, window, detail
):
    fake = _FakeOllama(_refusal(body))
    backend = _backend(fake, context_window=window)

    with pytest.raises(RuntimeError, match="context window was exhausted") as error:
        _notes(backend)

    assert detail in str(error.value)
    assert len(fake.chat_payloads) == 1


@pytest.mark.parametrize(
    ("body", "status_code"),
    [
        (MULTIMODAL_0_34, 400),
        (BAD_BASE64_0_34, 400),
        ({"error": "model 'qwen2.5:0.5b' not found"}, 404),
    ],
    ids=["multimodal-400", "base64-400", "unknown-model-404"],
)
def test_an_unrelated_error_is_not_called_a_context_overflow(body, status_code):
    fake = _FakeOllama(_refusal(body, status_code=status_code))
    backend = _backend(fake, context_window=32768)

    with pytest.raises(RuntimeError) as error:
        _notes(backend)

    assert str(status_code) in str(error.value)
    assert "context window" not in str(error.value)


def test_the_window_ollama_reports_wins_over_the_one_sent():
    # Ollama may run a smaller window than requested (it clamps to the model);
    # its n_ctx is what the prompt did not fit.
    fake = _FakeOllama(_refusal(_llama_server_overflow(9000, 8192)))
    backend = _backend(fake, context_window=32768)

    with pytest.raises(RuntimeError) as error:
        _notes(backend)

    assert "is 8,192 tokens and the prompt is 9,000 tokens" in str(error.value)


def test_a_refusal_at_the_configured_window_suggests_raising_it():
    fake = _FakeOllama(_refusal(_llama_server_overflow(5000, 4096)))
    backend = _backend(fake, context_window=4096)

    with pytest.raises(RuntimeError) as error:
        _notes(backend)

    assert "Increase the Ollama context window" in str(error.value)
    assert "too long for this model" not in str(error.value)


@pytest.mark.parametrize(
    "response",
    [_refusal(_llama_server_overflow(9000, 8192)), _answer(WINDOW_FILLED_0_40)],
    ids=["prompt-refused", "answer-filled-window"],
)
def test_a_window_at_the_models_maximum_does_not_suggest_raising_the_setting(
    response,
):
    # The window was lowered to the model's trained 8192; raising the
    # configured 32768 would change nothing.
    fake = _FakeOllama(response, trained_context=8192)
    backend = _backend(fake, context_window=32768)

    with pytest.raises(RuntimeError, match="context window was exhausted") as error:
        _notes(backend)

    assert "too long for this model's context" in str(error.value)
    assert "Increase the Ollama context window" not in str(error.value)


@pytest.mark.parametrize("window", [1024, 4096, 32768])
def test_a_short_prompt_fits_any_window_settings_accept(window):
    fake = _FakeOllama(_answer(FITS, content="Budget sync"))
    backend = _backend(fake, context_window=window)

    title = backend.infer_meeting_title("[00:00 - 00:05] Ana: quick budget sync")

    assert title == "Budget sync"
    assert fake.chat_payloads[0]["options"]["num_ctx"] == window


@pytest.mark.parametrize(
    "metadata",
    [WINDOW_FILLED_0_40, WINDOW_FILLED_0_12_6],
    ids=["ollama-0.40", "ollama-0.12.6"],
)
def test_an_answer_that_filled_the_window_is_incomplete_and_refused(metadata):
    fake = _FakeOllama(_answer(metadata))
    backend = _backend(fake, context_window=512)

    with pytest.raises(RuntimeError, match="answer is incomplete"):
        _notes(backend)


@pytest.mark.parametrize(
    "metadata",
    [TRUNCATED_0_12_5, SHIFTED_0_12_6],
    ids=["prompt-cut-to-window", "answer-shifted-prompt-out"],
)
def test_a_server_that_ignores_truncate_and_shift_is_caught_by_its_counts(metadata):
    fake = _FakeOllama(_answer(metadata))
    backend = _backend(fake, context_window=512)

    with pytest.raises(RuntimeError, match="context window was exhausted"):
        _notes(backend)


def test_an_answer_that_ends_exactly_at_the_window_is_kept():
    # With shift off nothing was cut: only a length stop or a prompt counted at
    # the full window means a cut.
    exact = {"done": True, "done_reason": "stop", "prompt_eval_count": 500}
    fake = _FakeOllama(_answer({**exact, "eval_count": 12}))
    backend = _backend(fake, context_window=512)

    assert "The launch moves to Friday." in _notes(backend)


def test_the_window_is_clamped_to_what_the_model_was_trained_on():
    fake = _FakeOllama(_answer(FITS), trained_context=8192)
    backend = _backend(fake, context_window=32768)

    _notes(backend)

    assert fake.chat_payloads[0]["options"]["num_ctx"] == 8192


def test_an_unknown_model_length_falls_back_to_the_configured_window():
    fake = _FakeOllama(_answer(FITS), trained_context=None)
    backend = _backend(fake, context_window=32768)

    _notes(backend)

    assert fake.chat_payloads[0]["options"]["num_ctx"] == 32768


def test_the_model_is_looked_up_once_per_process_until_the_entry_expires(
    monkeypatch,
):
    clock = [1000.0]
    monkeypatch.setattr(ollama_module.time, "monotonic", lambda: clock[0])
    fake = _FakeOllama(*[_answer(FITS) for _ in range(3)], trained_context=131072)

    _notes(_backend(fake, context_window=32768))
    backend = _backend(fake, context_window=32768)
    _notes(backend)
    assert backend.supports_vision() is False
    assert fake.show_calls == 1

    clock[0] += ollama_module.MODEL_SHOW_TTL_SECONDS
    _notes(_backend(fake, context_window=32768))
    assert fake.show_calls == 2


def test_the_model_lookup_is_kept_apart_per_server():
    fake = _FakeOllama(_answer(FITS), _answer(FITS), trained_context=8192)
    other_server = _backend(fake, context_window=32768)
    other_server.api_url = "http://other-ollama.local"

    _notes(_backend(fake, context_window=32768))
    _notes(other_server)

    assert fake.show_calls == 2


def test_chat_keeps_the_newest_turns_that_fit_and_never_cuts_the_question():
    # 4096 window, a quarter of it kept for the answer: two 3,000-byte turns
    # fit beside the question, three do not.
    fake = _FakeOllama(_answer(FITS, content="Friday."))
    backend = _backend(fake, context_window=4096)

    assert _chat(backend, _turns(4, 3000)) == "Friday."

    sent = fake.chat_payloads[0]["messages"]
    assert [m["content"][0] for m in sent[:-1]] == ["2", "3"]
    assert [m["role"] for m in sent[:-1]] == ["user", "assistant"]
    assert "we ship Friday" in sent[-1]["content"]


def test_chat_history_kept_never_opens_on_an_assistant_turn():
    # The two newest turns that fit are an answer and the question after it;
    # an answer without its question goes too.
    fake = _FakeOllama(_answer(FITS, content="Friday."))
    backend = _backend(fake, context_window=4096)

    assert _chat(backend, _turns(5, 3000)) == "Friday."

    sent = fake.chat_payloads[0]["messages"]
    assert [(m["role"], m["content"][0]) for m in sent[:-1]] == [("user", "4")]


def test_chat_trims_again_by_ollamas_own_count_when_it_still_refuses():
    # Dense text (numbers, code) can run past the estimate; Ollama's count then
    # decides how many more turns go.
    fake = _FakeOllama(
        _refusal(_llama_server_overflow(9000, 8192)),
        _answer(FITS, content="Friday."),
    )
    backend = _backend(fake, context_window=8192)

    assert _chat(backend, _turns(4, 2600)) == "Friday."

    first, retry = (p["messages"] for p in fake.chat_payloads)
    assert len(first) == 5
    assert [m["content"][0] for m in retry[:-1]] == ["2", "3"]
    assert retry[-1] == first[-1]


def test_chat_drops_every_turn_when_ollama_gives_no_count():
    fake = _FakeOllama(_refusal(OVERFLOW_0_12_6), _answer(FITS, content="Friday."))
    backend = _backend(fake, context_window=8192)

    assert _chat(backend, _turns(2, 1000)) == "Friday."

    assert len(fake.chat_payloads[1]["messages"]) == 1


def test_chat_refuses_once_trimming_cannot_make_it_fit():
    fake = _FakeOllama(_refusal(OVERFLOW_0_12_6), _refusal(OVERFLOW_0_12_6))
    backend = _backend(fake, context_window=8192)

    with pytest.raises(RuntimeError, match="context window was exhausted"):
        _chat(backend, _turns(2, 1000))

    assert len(fake.chat_payloads) == 2


def test_a_chat_without_history_is_not_retried():
    fake = _FakeOllama(_refusal(OVERFLOW_0_40))
    backend = _backend(fake, context_window=512)

    with pytest.raises(RuntimeError, match="context window was exhausted"):
        _chat(backend)

    assert len(fake.chat_payloads) == 1


def _stream(backend) -> list[str]:
    return list(
        backend.ask_question_streaming(
            user_question="What was decided?",
            meeting_notes="notes",
            diarized_transcript="[00:00 - 00:04] Ana: we ship Friday",
        )
    )


def test_a_streaming_overflow_is_refused_before_any_text_and_falls_back():
    primary = _backend(_FakeOllama(_refusal(OVERFLOW_0_40)), context_window=512)
    stream = _Response(
        {},
        chunks=[
            {"message": {"content": "Friday."}, "done": False},
            {"message": {"content": ""}, **FITS},
        ],
    )
    secondary = _backend(_FakeOllama(stream), context_window=32768)
    secondary.model = "qwen2.5:7b"

    with pytest.raises(RuntimeError, match="context window was exhausted"):
        next(iter(primary.ask_question_streaming("q", "notes", "transcript")))

    primary.requests.chat_responses.append(_refusal(OVERFLOW_0_40))
    assert _stream(SecondaryLLMBackend(primary, secondary)) == ["Friday."]
