"""The recording API exposes gist and persists explicit title ownership."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import sessionmaker

from backend.models.recording import Recording
from backend.tests import test_recording_conditional_read as harness

URL = harness.URL
client = harness.client
session_maker = harness.session_maker


@pytest.mark.anyio
async def test_gist_and_its_live_marker_are_visible_in_detail_and_list(
    client: AsyncClient, session_maker: sessionmaker
) -> None:
    async with session_maker() as session:
        recording = await session.get(Recording, 1)
        recording.gist = "The team reviewed the release plan."
        recording.gist_live = True
        await session.commit()

    detail = await client.get(URL)
    listing = await client.get("/api/v1/recordings/")

    assert detail.status_code == 200
    assert detail.json()["gist"] == "The team reviewed the release plan."
    assert detail.json()["gist_live"] is True
    assert "title_user_edited" not in detail.json()
    assert listing.status_code == 200
    rows = listing.json()
    if isinstance(rows, dict):
        rows = rows["items"]
    assert rows[0]["gist"] == "The team reviewed the release plan."
    assert rows[0]["gist_live"] is True
    assert "title_user_edited" not in rows[0]


@pytest.mark.anyio
async def test_patch_title_marks_it_as_user_owned(
    client: AsyncClient, session_maker: sessionmaker
) -> None:
    response = await client.patch(URL, json={"name": "My release notes"})

    assert response.status_code == 200
    assert response.json()["name"] == "My release notes"
    async with session_maker() as session:
        recording = await session.get(Recording, 1)
        assert recording.title_user_edited is True


@pytest.mark.anyio
async def test_unrelated_patch_does_not_claim_the_title(
    client: AsyncClient, session_maker: sessionmaker
) -> None:
    response = await client.patch(URL, json={"transcription_language": "en"})
    assert response.status_code == 200
    async with session_maker() as session:
        recording = await session.get(Recording, 1)
        assert recording.title_user_edited is False
