"""Tests for the demo API. Run from demo/server with: pytest test_app.py"""

from __future__ import annotations

import importlib
import json
import sys
from datetime import date

import pytest
from starlette.testclient import TestClient

CAPTURED_ON = "2026-10-19"


@pytest.fixture()
def demo(tmp_path, monkeypatch):
    bodies = tmp_path / "bodies"
    bodies.mkdir()
    (bodies / "recordings.json").write_text(
        json.dumps(
            [
                {
                    "id": "r1",
                    "created_at": "2026-10-18T19:00:00Z",
                    "name": "On 2026-10-18 we met",
                }
            ]
        )
    )
    (bodies / "recordings-q.json").write_text(json.dumps([]))
    (bodies / "calendar.json").write_text(
        json.dumps({"2026-10-20": [{"start": "2026-10-20"}]})
    )
    (bodies / "version.json").write_text(json.dumps({"version": "2.9.0"}))
    (bodies / "audio.m4a").write_bytes(b"0123456789")
    entries = [
        {
            "path": "/api/v1/recordings",
            "query": "limit=50",
            "body": "bodies/recordings.json",
        },
        {
            "path": "/api/v1/recordings",
            "query": "limit=50&q=moon",
            "body": "bodies/recordings-q.json",
        },
        {
            "path": "/api/v1/calendar",
            "query": "start=2026-10-01&end=2026-10-31",
            "body": "bodies/calendar.json",
        },
        {"path": "/api/v1/version", "query": "", "body": "bodies/version.json"},
        {
            "path": "/api/v1/recordings/r1/stream",
            "query": "",
            "body": "bodies/audio.m4a",
            "content_type": "audio/mp4",
        },
    ]
    (tmp_path / "index.json").write_text(
        json.dumps({"captured_on": CAPTURED_ON, "entries": entries})
    )
    monkeypatch.setenv("DEMO_FIXTURES", str(tmp_path))
    sys.modules.pop("app", None)
    module = importlib.import_module("app")
    return module


def signed_in(module) -> TestClient:
    client = TestClient(module.app, base_url="https://testserver")
    assert (
        client.post(
            "/api/v1/login/session", data={"username": "x", "password": "y"}
        ).status_code
        == 200
    )
    return client


def test_writes_are_refused(demo):
    client = signed_in(demo)
    for method in ("post", "put", "patch", "delete"):
        response = getattr(client, method)("/api/v1/recordings/r1")
        assert response.status_code == 403
        assert response.json()["detail"] == demo.READ_ONLY


def test_reads_need_the_session_cookie(demo):
    client = TestClient(demo.app, base_url="https://testserver")
    assert client.get("/api/v1/recordings?limit=50").status_code == 401
    assert client.get("/api/v1/version").status_code == 200


def test_logout_clears_the_session(demo):
    client = signed_in(demo)
    client.post("/api/v1/login/logout")
    assert client.get("/api/v1/recordings?limit=50").status_code == 401


def test_exact_query_then_path_fallback(demo, monkeypatch):
    monkeypatch.setattr(demo.FIXTURE_SET, "offset_days", lambda today=None: 0)
    client = signed_in(demo)
    assert client.get("/api/v1/recordings?q=moon&limit=50").json() == []
    assert client.get("/api/v1/recordings?limit=50&q=unseen").json() == []
    assert client.get("/api/v1/recordings?limit=50&q=WE MET").json()[0]["id"] == "r1"
    assert client.get("/api/v1/recordings?limit=20").json()[0]["id"] == "r1"
    assert client.get("/api/v1/nothing").status_code == 404


def test_offset_is_whole_weeks(demo):
    fixtures = demo.FIXTURE_SET
    assert fixtures.offset_days(date(2026, 10, 25)) == 0
    assert fixtures.offset_days(date(2026, 10, 26)) == 7
    assert fixtures.offset_days(date(2026, 12, 1)) == 42
    assert fixtures.offset_days(date(2026, 1, 1)) == 0


def test_dates_move_forward_and_text_does_not(demo, monkeypatch):
    monkeypatch.setattr(demo.FIXTURE_SET, "offset_days", lambda today=None: 14)
    demo.shifted_body.cache_clear()
    client = signed_in(demo)
    row = client.get("/api/v1/recordings?limit=50").json()[0]
    assert row["created_at"] == "2026-11-01T19:00:00Z"
    assert row["name"] == "On 2026-10-18 we met"
    calendar = client.get("/api/v1/calendar?start=2026-10-15&end=2026-11-14").json()
    assert calendar == {"2026-11-03": [{"start": "2026-11-03"}]}


def test_query_dates_move_back_before_matching(demo, monkeypatch):
    monkeypatch.setattr(demo.FIXTURE_SET, "offset_days", lambda today=None: 7)
    assert demo.FIXTURE_SET.find(
        "/api/v1/calendar", "start=2026-10-08&end=2026-11-07", 7
    )[0]["query"].startswith("start=2026-10-01")


def test_audio_is_served_with_ranges(demo):
    client = signed_in(demo)
    response = client.get(
        "/api/v1/recordings/r1/stream", headers={"Range": "bytes=2-5"}
    )
    assert response.status_code == 206
    assert response.content == b"2345"
