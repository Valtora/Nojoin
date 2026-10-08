"""Whether a recording has anything playable, and what the player is told.

A recording restored from a backup taken without audio (or whose audio was lost)
has neither its file nor a playback proxy. The stream endpoint used to answer 202
"Audio proxy is being prepared" for it forever, and the page polled for a proxy
nothing would ever make.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api.v1.endpoints.recordings import helpers, routes_query


def _recording(tmp_path: Path, *, audio: bool, proxy: bool) -> SimpleNamespace:
    audio_path = tmp_path / "meeting.webm"
    proxy_path = tmp_path / "meeting.mp3"
    if audio:
        audio_path.write_bytes(b"webm")
    if proxy:
        proxy_path.write_bytes(b"mp3")
    return SimpleNamespace(
        id=1,
        user_id=1,
        audio_path=str(audio_path),
        proxy_path=str(proxy_path) if proxy else None,
    )


async def _stream(monkeypatch, recording: SimpleNamespace):
    async def owned(db, recording_id, user_id):
        return recording

    monkeypatch.setattr(routes_query, "_get_owned_recording", owned)
    request = SimpleNamespace(headers={})
    return await routes_query.stream_recording(
        "rec-1", request, db=None, current_user=SimpleNamespace(id=1)
    )


@pytest.mark.anyio
async def test_stream_says_the_audio_is_gone_when_nothing_can_make_a_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(HTTPException) as raised:
        await _stream(monkeypatch, _recording(tmp_path, audio=False, proxy=False))

    assert raised.value.status_code == 404
    assert "not available" in raised.value.detail


@pytest.mark.anyio
async def test_stream_still_asks_to_wait_while_a_proxy_can_be_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(HTTPException) as raised:
        await _stream(monkeypatch, _recording(tmp_path, audio=True, proxy=False))

    assert raised.value.status_code == 202


@pytest.mark.parametrize(
    ("audio", "proxy", "has_audio"),
    [(False, False, False), (True, False, True), (False, True, True)],
)
def test_has_audio_reports_whether_anything_playable_exists(
    tmp_path: Path, audio: bool, proxy: bool, has_audio: bool
) -> None:
    recording = _recording(tmp_path, audio=audio, proxy=proxy)

    assert helpers._recording_has_audio(recording) is has_audio
