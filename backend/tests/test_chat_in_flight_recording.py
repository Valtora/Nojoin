"""In-flight Chat streams and persists through the existing owned endpoint."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from backend.api.v1.endpoints.transcripts import routes_chat
from backend.api.v1.endpoints.transcripts.helpers import ChatRequest
from backend.celery_app import celery_app
from backend.processing.llm_backends.base import LLMBackend
from backend.tests.test_chat_cli_relay_api import (
    _fake_async,
    _make_session_maker,
    _rag_and_dispatch_stubs,
)
from backend.utils.llm_config import ResolvedLLMConfig


@pytest.mark.parametrize(
    ("status", "client_status", "has_transcript"),
    [
        ("UPLOADING", "RECORDING", True),
        ("PAUSED", "PAUSED", True),
        ("UPLOADING", "UPLOADING", True),
        ("QUEUED", "IDLE", True),
        ("PROCESSING", "IDLE", True),
        ("UPLOADING", "RECORDING", False),
    ],
)
def test_chat_streams_while_the_recording_is_in_flight(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    client_status: str,
    has_transcript: bool,
) -> None:
    received: list[dict] = []

    class Backend:
        def ask_question_streaming(self, **kwargs: object) -> Iterator[str]:
            received.append(kwargs)
            yield "Answer."

    async def run() -> tuple[list[str], list[tuple[str, str]]]:
        engine, maker = await _make_session_maker()
        async with engine.begin() as connection:
            if not has_transcript:
                await connection.execute(text("DELETE FROM transcripts"))
            await connection.execute(
                text(
                    "UPDATE recordings SET status=:status, client_status=:client WHERE id=1"
                ),
                {"status": status, "client": client_status},
            )
        config = ResolvedLLMConfig(
            provider="ollama",
            api_key=None,
            model="test",
            api_url="http://x",
            merged_config={},
        )
        monkeypatch.setattr(
            routes_chat, "resolve_llm_config_async", lambda *a, **k: _fake_async(config)
        )
        monkeypatch.setattr(celery_app, "send_task", _rag_and_dispatch_stubs([]))
        monkeypatch.setattr(routes_chat, "async_session_maker", maker)
        monkeypatch.setattr(
            "backend.api.v1.endpoints.transcripts.get_llm_backend_with_secondary",
            lambda _: Backend(),
        )
        monkeypatch.setattr(
            LLMBackend,
            "get_mapped_transcript_for_llm",
            staticmethod(lambda *a, **k: "[00:01] Mira: Current."),
        )
        async with maker() as session:
            response = await routes_chat.chat_with_meeting(
                "p1",
                ChatRequest(message="Hi"),
                db=session,
                current_user=SimpleNamespace(id=1, settings={}),
            )
            frames = [chunk async for chunk in response.body_iterator]
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text("SELECT role, content FROM chat_messages ORDER BY id")
                )
            ).all()
        await engine.dispose()
        return frames, [(row[0], row[1]) for row in rows]

    frames, rows = asyncio.run(run())
    assert json.loads(frames[0].removeprefix("data: ")) == {
        "context": {"provisional": True, "transcript_trimmed": False}
    }
    footer = (
        "\n\n_Based on the transcript so far; wording and speakers may still change._"
    )
    assert frames[1:] == [
        'data: {"token": "Answer."}\n\n',
        f"data: {json.dumps({'token': footer})}\n\n",
        "data: [DONE]\n\n",
    ]
    assert rows == [("user", "Hi"), ("assistant", "Answer." + footer)]
    assert "Current." in received[0]["diarized_transcript"].text
