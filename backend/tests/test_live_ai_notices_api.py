"""The recording read carries live AI notices during a capture (#994).

Also the recovery that clears them: an AI settings save and a subscription
connected or disconnected start every provider afresh.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import requests
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from backend.processing.live_ai_backoff import EPOCH_KEY, LiveAIChain
from backend.processing.llm_backends.base import LLMBackend
from backend.tests.sqlite_schemas import (
    DOCUMENTS_SCHEMA,
    RECORDING_TAGS_SCHEMA,
    TAGS_SCHEMA,
)
from backend.tests.test_cli_oauth_api import _build_app, _FakeOAuth, _patch, _restore
from backend.tests.test_live_speaker_naming_api import REC, USER, _seed
from backend.tests.test_live_speaker_naming_api import client as client
from backend.tests.test_live_speaker_naming_api import (
    session_maker as session_maker,
)
from backend.tests.test_settings_processing_tuning import _run
from backend.tests.test_settings_processing_tuning import fake_config as fake_config

PROVIDER_TEXT = "request_id=req_abc123 the upstream said something private"


class Refused(LLMBackend):
    provider_id = "anthropic"
    model = "claude-x"

    def generate_meeting_edge(
        self, request: Any, prompt_template: Any = None, timeout: Any = None
    ):
        response = requests.Response()
        response.status_code = 401
        raise requests.HTTPError(PROVIDER_TEXT, response=response)


def _stop_the_provider() -> None:
    chain = LiveAIChain(Refused(), recording_id=1, purpose="meeting_edge")
    try:
        chain.generate_meeting_edge("request", timeout=90)
    except RuntimeError:
        pass


async def _detail_tables(session_maker: Any, status: str = "UPLOADING") -> None:
    await _seed(session_maker)
    async with session_maker() as db:
        for schema in (DOCUMENTS_SCHEMA, TAGS_SCHEMA, RECORDING_TAGS_SCHEMA):
            await db.execute(text(schema))
        await db.execute(text("UPDATE recordings SET status = :s"), {"s": status})
        await db.commit()


@pytest.mark.anyio
async def test_a_user_reads_a_stopped_provider_as_the_administrators_to_fix(
    client: AsyncClient, session_maker: Any
) -> None:
    await _detail_tables(session_maker)
    _stop_the_provider()

    response = await client.get(f"/api/v1/recordings/{REC}")

    assert response.status_code == 200, response.text
    (notice,) = response.json()["live_ai_notices"]
    assert notice["provider"] == "Anthropic" and notice["model"] == "claude-x"
    assert notice["action"] == "stopped" and notice["kind"] == "auth"
    assert notice["fixable_by"] == "admin" and notice["settings"] is None
    assert notice["message"].endswith(
        "Ask your administrator to check the AI provider settings."
    )
    assert "req_abc123" not in response.text and "private" not in response.text


@pytest.mark.anyio
async def test_an_administrator_is_sent_to_the_ai_providers_page(
    client: AsyncClient, session_maker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(USER, "role", "admin", raising=False)
    await _detail_tables(session_maker)
    _stop_the_provider()

    response = await client.get(f"/api/v1/recordings/{REC}")

    (notice,) = response.json()["live_ai_notices"]
    assert notice["fixable_by"] == "you" and notice["settings"] == "ai-providers"
    assert notice["message"].endswith("until the AI settings change.")


@pytest.mark.anyio
async def test_a_capture_with_every_provider_answering_has_no_notices(
    client: AsyncClient, session_maker: Any
) -> None:
    await _detail_tables(session_maker)

    response = await client.get(f"/api/v1/recordings/{REC}")

    assert response.json()["live_ai_notices"] == []


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["PROCESSING", "PROCESSED"])
async def test_a_recording_no_longer_captured_carries_no_notices(
    client: AsyncClient, session_maker: Any, status: str
) -> None:
    await _detail_tables(session_maker, status)
    _stop_the_provider()

    response = await client.get(f"/api/v1/recordings/{REC}")

    assert response.status_code == 200, response.text
    assert "live_ai_notices" not in response.json()


@pytest.mark.anyio
async def test_a_notice_clears_once_the_settings_change(
    client: AsyncClient, session_maker: Any, fake_live_ai_redis: Any
) -> None:
    await _detail_tables(session_maker)
    _stop_the_provider()
    fake_live_ai_redis.incr(EPOCH_KEY)

    response = await client.get(f"/api/v1/recordings/{REC}")

    assert response.json()["live_ai_notices"] == []


# -- what starts every provider afresh -----------------------------------------


def test_saving_an_ai_setting_starts_every_provider_afresh(fake_live_ai_redis) -> None:
    (response,), _ = _run([("POST", {"cli_model": "sonnet"})])

    assert response.status_code == 200
    assert fake_live_ai_redis.get(EPOCH_KEY) == "1"


def test_saving_another_setting_leaves_them_as_they_are(fake_live_ai_redis) -> None:
    (response,), _ = _run([("POST", {"vad_threshold": 0.3})])

    assert response.status_code == 200
    assert fake_live_ai_redis.get(EPOCH_KEY) is None


def test_connecting_and_disconnecting_a_subscription_starts_afresh(
    fake_live_ai_redis,
) -> None:
    async def _go() -> list[str | None]:
        originals = _patch(_FakeOAuth())
        engine, _maker, app = await _build_app()
        epochs = []
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as ac:
                await ac.post("/cli-oauth/start")
                await ac.post("/cli-oauth/complete", json={"code": "c"})
                epochs.append(fake_live_ai_redis.get(EPOCH_KEY))
                await ac.delete("/cli-oauth/token")
                epochs.append(fake_live_ai_redis.get(EPOCH_KEY))
        finally:
            await engine.dispose()
            _restore(originals)
        return epochs

    assert asyncio.run(_go()) == ["1", "2"]
