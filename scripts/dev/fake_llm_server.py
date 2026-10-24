"""A fake Ollama server with canned, deterministic answers, for test stacks.

Answers the Ollama API calls Nojoin makes (``GET /api/tags``, ``POST
/api/show`` and ``POST /api/chat``, streamed or not, with images or without),
so an end-to-end stack can run notes, Meeting Chat, Meeting Edge, AI analytics
and document image parsing with no model and no outside network. Standard
library only. Run it on the stack's internal network and point the stack at it
with ``OLLAMA_API_URL``; docs/DEVELOPMENT.md has the commands.

Each request is classified by the instruction its prompt opens with, and gets
the same answer every time. Two models are listed: ``fake-llm:latest`` accepts
images and ``fake-text:latest`` does not, so the downgrade a text-only model
causes can be exercised too. The context window behaves as Ollama's does with
the ``truncate`` and ``shift`` options Nojoin sends, with tokens estimated at
four bytes each, so a small ``OLLAMA_CONTEXT_WINDOW`` drives the long-meeting
paths. The log line for a request names its task and sizes, never its text.

    python scripts/dev/fake_llm_server.py --host 0.0.0.0 --port 11434
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

# A fixed timestamp keeps every response byte-identical between runs.
CREATED_AT = "2026-01-01T00:00:00Z"
VERSION = "0.34.0"
MODEL_CONTEXT_LENGTH = 131072
# Ollama's num_ctx when a request sends none.
DEFAULT_NUM_CTX = 4096
BYTES_PER_TOKEN = 4
TOKENS_PER_IMAGE = 256

MODELS = {
    "fake-llm:latest": ("completion", "vision"),
    "fake-text:latest": ("completion",),
}

NOTES = (
    "## Summary\n\n"
    "These notes were written by the fake LLM server, which does not read the "
    "transcript.\n\n"
    "## Action Items\n\n"
    "- Check the notes appear in the meeting view."
)
TITLE = "Fake LLM Meeting"
CHAT_ANSWER = (
    "This answer comes from the fake LLM server. It has not read the meeting, "
    "so it cannot say what was discussed."
)

# The task each prompt asks for, by the instruction it opens with. The earliest
# marker in a prompt wins, so one quoted further down, in a transcript or in
# notes, cannot outrank the prompt's own opening. A JSON repair prompt has no
# marker of its own and is answered for the original task it quotes.
TASK_MARKERS = (
    (
        "meeting_intelligence",
        "Your task is to produce one valid JSON object that combines",
    ),
    ("meeting_edge", "You are Meeting Edge, a live meeting assistant."),
    ("meeting_analysis", "You are analysing a meeting transcript to describe how"),
    ("speaker_suggestions", "return only evidence-backed speaker name suggestions"),
    (
        "notes_structure",
        "You are helping a Nojoin user design a meeting-notes structure.",
    ),
    ("notes_part", "so its transcript has been split into consecutive parts."),
    ("notes_merge_title", "write a title for the meeting and one set of notes"),
    ("notes_merge", "Combine the notes of the parts below into one set of notes."),
    ("notes", "Generate meeting notes from the transcript below."),
    ("title", "provide a title that summarises the main topic"),
    ("chat", "You are a helpful AI assistant. You have access to the following"),
)

ANSWERS: dict[str, str] = {
    "meeting_intelligence": json.dumps(
        {"speaker_mapping": {}, "title": TITLE, "notes_markdown": NOTES}
    ),
    "meeting_edge": json.dumps(
        {
            "summary": "The fake LLM server is standing in for a model.",
            "rolling_summary": (
                "The fake LLM server answers every Meeting Edge refresh with "
                "this summary. It does not read the transcript."
            ),
            "questions": ["Is the fake LLM server answering?"],
            "points": ["These suggestions are canned test output."],
            "concepts": [
                {
                    "term": "Fake LLM server",
                    "explanation": "A test stand-in that returns canned answers.",
                }
            ],
        }
    ),
    "meeting_analysis": json.dumps(
        {"topics": [], "sentiment": [], "questions": [], "decisions": []}
    ),
    "speaker_suggestions": json.dumps({"suggestions": []}),
    "notes_structure": json.dumps(
        {
            "name": "Fake structure",
            "description": "Written by the fake LLM server.",
            "sections": (
                "## Summary\n\nWhat the meeting covered.\n\n"
                "## Action Items\n\nEach action, with its owner."
            ),
        }
    ),
    "notes_part": NOTES,
    "notes_merge_title": f"<title>{TITLE}</title>\n\n{NOTES}",
    "notes_merge": NOTES,
    "notes": NOTES,
    "title": TITLE,
    "chat": CHAT_ANSWER,
}


class RequestError(Exception):
    """An error Ollama would answer with, as its status and message."""

    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class Answer:
    model: str
    task: str
    content: str
    prompt_tokens: int
    answer_tokens: int
    done_reason: str


def model_name(name: object) -> str:
    """The listed model a request names: ``fake-llm`` is ``fake-llm:latest``."""
    if not isinstance(name, str) or not name.strip():
        raise RequestError(HTTPStatus.BAD_REQUEST, "model is required")
    name = name.strip()
    if ":" not in name:
        name = f"{name}:latest"
    if name not in MODELS:
        raise RequestError(
            HTTPStatus.NOT_FOUND, f'model "{name}" not found, try pulling it first'
        )
    return name


def classify(prompt: str, wants_json: bool) -> str:
    found = [(prompt.find(marker), task) for task, marker in TASK_MARKERS]
    found = [(index, task) for index, task in found if index >= 0]
    if found:
        return min(found)[1]
    return "chat_json" if wants_json else "chat"


def answer_text(task: str, images: int) -> str:
    if task == "vision":
        noun = "image" if images == 1 else "images"
        return (
            f"## Page\n\nThe fake LLM server read {images} {noun}. "
            "It does not look at their content."
        )
    if task == "chat_json":
        return json.dumps({"answer": CHAT_ANSWER})
    return ANSWERS[task]


def estimate_tokens(text: str) -> int:
    return -(-len(text.encode("utf-8")) // BYTES_PER_TOKEN)


def overflow_error(prompt_tokens: int, window: int) -> RequestError:
    """The refusal Ollama relays from llama-server for an oversized prompt."""
    detail = {
        "error": {
            "code": 400,
            "message": (
                f"request ({prompt_tokens} tokens) exceeds the available context "
                f"size ({window} tokens)"
            ),
            "type": "exceed_context_size_error",
            "n_prompt_tokens": prompt_tokens,
            "n_ctx": window,
        }
    }
    return RequestError(HTTPStatus.BAD_REQUEST, json.dumps(detail))


def context_window(options: object) -> int:
    """The num_ctx a request asks for, clamped to the model's own length."""
    num_ctx = options.get("num_ctx") if isinstance(options, dict) else None
    try:
        window = int(num_ctx or DEFAULT_NUM_CTX)
    except (TypeError, ValueError) as error:
        raise RequestError(
            HTTPStatus.BAD_REQUEST, "num_ctx must be a number"
        ) from error
    return min(max(window, 1), MODEL_CONTEXT_LENGTH)


def read_messages(body: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise RequestError(HTTPStatus.BAD_REQUEST, "messages are required")
    if not all(isinstance(m, dict) for m in messages):
        raise RequestError(HTTPStatus.BAD_REQUEST, "each message must be an object")
    images = sum(len(m.get("images") or []) for m in messages)
    return messages, images


def chat_answer(body: dict[str, Any]) -> Answer:
    """What ``/api/chat`` answers, or the RequestError Ollama would raise."""
    model = model_name(body.get("model"))
    messages, images = read_messages(body)
    if images and "vision" not in MODELS[model]:
        raise RequestError(
            HTTPStatus.BAD_REQUEST, f"{model} does not support image input"
        )
    window = context_window(body.get("options"))
    text = "".join(str(m.get("content") or "") for m in messages)
    prompt_tokens = estimate_tokens(text) + images * TOKENS_PER_IMAGE
    if prompt_tokens > window and body.get("truncate") is False:
        raise overflow_error(prompt_tokens, window)
    prompt_tokens = min(prompt_tokens, window)

    last = str(messages[-1].get("content") or "")
    task = "vision" if images else classify(last, body.get("format") is not None)
    content = answer_text(task, images)
    answer_tokens = estimate_tokens(content)
    done_reason = "stop"
    if prompt_tokens + answer_tokens > window and body.get("shift") is False:
        room = max(window - prompt_tokens, 0)
        content = content.encode("utf-8")[: room * BYTES_PER_TOKEN].decode(
            "utf-8", errors="ignore"
        )
        answer_tokens, done_reason = room, "length"
    return Answer(model, task, content, prompt_tokens, answer_tokens, done_reason)


def chat_chunks(answer: Answer, stream: bool) -> list[dict[str, Any]]:
    """The response objects: one, or one per word and a closing one."""
    final = {
        "model": answer.model,
        "created_at": CREATED_AT,
        "message": {"role": "assistant", "content": answer.content},
        "done": True,
        "done_reason": answer.done_reason,
        "total_duration": 0,
        "load_duration": 0,
        "prompt_eval_count": answer.prompt_tokens,
        "prompt_eval_duration": 0,
        "eval_count": answer.answer_tokens,
        "eval_duration": 0,
    }
    if not stream:
        return [final]
    words = answer.content.split(" ")
    pieces = [w if i == len(words) - 1 else f"{w} " for i, w in enumerate(words)]
    chunks = [
        {
            "model": answer.model,
            "created_at": CREATED_AT,
            "message": {"role": "assistant", "content": piece},
            "done": False,
        }
        for piece in pieces
        if piece
    ]
    return [*chunks, {**final, "message": {"role": "assistant", "content": ""}}]


def tags() -> dict[str, Any]:
    return {
        "models": [
            {
                "name": name,
                "model": name,
                "modified_at": CREATED_AT,
                "size": 0,
                "digest": "0" * 64,
                "details": {
                    "format": "gguf",
                    "family": "fake",
                    "parameter_size": "0B",
                    "quantization_level": "none",
                },
            }
            for name in MODELS
        ]
    }


def show(body: dict[str, Any]) -> dict[str, Any]:
    name = model_name(body.get("model") or body.get("name"))
    return {
        "modelfile": "",
        "parameters": "",
        "template": "{{ .Prompt }}",
        "details": {"format": "gguf", "family": "fake"},
        "model_info": {
            "general.architecture": "fake",
            "fake.context_length": MODEL_CONTEXT_LENGTH,
        },
        "capabilities": list(MODELS[name]),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "FakeOllama/" + VERSION

    def do_GET(self) -> None:
        if self.path == "/":
            self._send_text(HTTPStatus.OK, "Ollama is running")
        elif self.path == "/api/tags":
            self._send_json(HTTPStatus.OK, tags())
        elif self.path == "/api/version":
            self._send_json(HTTPStatus.OK, {"version": VERSION})
        else:
            self._send_text(HTTPStatus.NOT_FOUND, "404 page not found")

    def do_HEAD(self) -> None:
        self.send_response(HTTPStatus.OK if self.path == "/" else HTTPStatus.NOT_FOUND)
        self.end_headers()

    def do_POST(self) -> None:
        try:
            body = self._read_body()
            if self.path == "/api/chat":
                self._chat(body)
            elif self.path == "/api/show":
                self._send_json(HTTPStatus.OK, show(body))
            else:
                self._send_text(HTTPStatus.NOT_FOUND, "404 page not found")
        except RequestError as error:
            self._note(f"{self.path} refused: {error.status.value}")
            self._send_json(error.status, {"error": error.message})

    def _chat(self, body: dict[str, Any]) -> None:
        answer = chat_answer(body)
        # Ollama streams unless a request says otherwise.
        stream = body.get("stream", True) is not False
        self._note(
            f"/api/chat {answer.model} task={answer.task} prompt_tokens="
            f"{answer.prompt_tokens} stream={stream} done={answer.done_reason}"
        )
        chunks = chat_chunks(answer, stream)
        if not stream:
            self._send_json(HTTPStatus.OK, chunks[0])
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(json.dumps(chunk).encode("utf-8") + b"\n")
            self.wfile.flush()

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError as error:
            raise RequestError(HTTPStatus.BAD_REQUEST, "invalid JSON") from error
        if not isinstance(body, dict):
            raise RequestError(HTTPStatus.BAD_REQUEST, "the body must be an object")
        return body

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_text(self, status: HTTPStatus, text: str) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _note(self, line: str) -> None:
        sys.stderr.write(f"fake-llm {line}\n")

    def log_message(self, format: str, *args: Any) -> None:
        self._note(format % args)


def build_server(host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11434)
    args = parser.parse_args(argv)

    server = build_server(args.host, args.port)
    # As a container's PID 1, Python ignores SIGTERM unless it handles it, and
    # `docker stop` would wait out its timeout.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    sys.stderr.write(f"fake-llm listening on {args.host}:{server.server_port}\n")
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
