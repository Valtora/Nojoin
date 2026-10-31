"""The upload's deep_parse form field is true unless the client sends false.

FastAPI 0.147 to 0.161 read an absent ``bool`` form field as an unchecked HTML
checkbox, which is False whatever the declared default, and 0.162 restored the
default. The endpoint types the field so that its default applies either way;
these tests fail if a FastAPI release changes that again.
"""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api.deps import get_current_user, get_db
from backend.api.v1.endpoints import documents as documents_ep
from backend.models.document import DocumentParseMode


class _Stored(Exception):
    """Ends the upload once its row is built, carrying the parse mode."""

    def __init__(self, parse_mode: DocumentParseMode) -> None:
        super().__init__(parse_mode)
        self.parse_mode = parse_mode


@pytest.fixture
def app(monkeypatch) -> FastAPI:
    async def owned_recording(*_args, **_kwargs):
        return SimpleNamespace(id=1, public_id="rec-1")

    @contextlib.asynccontextmanager
    async def no_limit(*_args, **_kwargs):
        yield

    async def stream(*, file, dest_path, max_size):
        return 3

    async def insert(_db, document, **_kwargs):
        raise _Stored(document.parse_mode)

    monkeypatch.setattr(documents_ep, "_get_owned_recording", owned_recording)
    monkeypatch.setattr(documents_ep, "enforce_upload_concurrency", no_limit)
    monkeypatch.setattr(documents_ep, "ensure_disk_headroom", lambda *_a, **_k: None)
    monkeypatch.setattr(documents_ep, "stream_and_validate_upload", stream)
    monkeypatch.setattr(documents_ep, "insert_document_or_remove_file", insert)

    app = FastAPI()
    app.include_router(documents_ep.router)
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    return app


async def _upload(app: FastAPI, data: dict[str, str]):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as client:
        return await client.post(
            "/recordings/rec-1/documents",
            files={"file": ("notes.txt", b"abc", "text/plain")},
            data=data,
        )


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({}, DocumentParseMode.VISUAL),
        ({"deep_parse": ""}, DocumentParseMode.VISUAL),
        ({"deep_parse": "true"}, DocumentParseMode.VISUAL),
        ({"deep_parse": "false"}, DocumentParseMode.STRUCTURAL),
        ({"deep_parse": "0"}, DocumentParseMode.STRUCTURAL),
    ],
)
def test_deep_parse_is_true_unless_the_client_sends_false(
    app: FastAPI, data: dict[str, str], expected: DocumentParseMode
) -> None:
    with pytest.raises(_Stored) as stored:
        asyncio.run(_upload(app, data))

    assert stored.value.parse_mode == expected


def test_deep_parse_rejects_a_value_that_is_not_a_boolean(app: FastAPI) -> None:
    response = asyncio.run(_upload(app, {"deep_parse": "maybe"}))

    assert response.status_code == 422


def test_openapi_shows_deep_parse_as_a_boolean_that_defaults_to_true(
    app: FastAPI,
) -> None:
    schemas = app.openapi()["components"]["schemas"]
    (body,) = [
        schema for name, schema in schemas.items() if name.startswith("Body_upload")
    ]

    assert body["properties"]["deep_parse"]["type"] == "boolean"
    assert body["properties"]["deep_parse"]["default"] is True
