"""The backup API: when a request is answered from the cache, joins a build, or waits.

One export runs at a time, a request for the same backup joins it, and exports and
restores never overlap, because a restore rewrites the rows and files an export is
reading. Redis, Celery and the export itself are replaced with fakes here; the
fingerprint and cache have their own tests in test_backup_cache.py.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.models.task as task_models
from backend.api.deps import get_current_active_superuser, get_db
from backend.api.v1.endpoints import backup as backup_endpoints
from backend.core.backup import cache as backup_cache
from backend.core.backup.format import (
    EXPORT_JOB_KEY,
    EXPORT_STALL_SECONDS,
    RESTORE_LOCK_KEY,
)


class FakeRedis:
    """The handful of commands the endpoints use, over one shared dict."""

    def __init__(self, store: dict[str, str]) -> None:
        self.store = store

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def exists(self, key: str) -> int:
        return int(key in self.store)

    async def delete(self, key: str) -> int:
        return int(self.store.pop(key, None) is not None)

    async def eval(self, script: str, numkeys: int, key: str, task_id: str) -> int:
        # Stands in for the release-if-owned script.
        raw = self.store.get(key)
        if raw and json.loads(raw).get("task_id") == task_id:
            del self.store[key]
            return 1
        return 0

    async def aclose(self) -> None:
        return None


class Harness:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.redis: dict[str, str] = {}
        self.dispatched: list[dict[str, Any]] = []
        self.results: dict[str, SimpleNamespace] = {}
        self.current_export: backup_cache.CachedExport | None = None
        self.export_dir = tmp_path / "backups"
        self.export_dir.mkdir()

        monkeypatch.setenv("BACKUP_EXPORT_DIR", str(self.export_dir))
        monkeypatch.setattr(
            backup_endpoints, "_redis_client", lambda: FakeRedis(self.redis)
        )
        monkeypatch.setattr(backup_endpoints, "AsyncResult", self._result)
        monkeypatch.setattr(backup_endpoints, "dispatch_task", self._dispatch)
        monkeypatch.setattr(
            backup_endpoints.BackupManager,
            "find_current_export",
            staticmethod(lambda include_audio, quality: self.current_export),
        )

        async def _no_ownership(*_args, **_kwargs) -> None:
            return None

        monkeypatch.setattr(task_models, "register_task_ownership", _no_ownership)

        app = FastAPI()
        app.include_router(backup_endpoints.router, prefix="/backup")

        async def _superuser():
            return MagicMock(id=1, is_superuser=True, role="admin")

        async def _db():
            yield MagicMock()

        app.dependency_overrides[get_current_active_superuser] = _superuser
        app.dependency_overrides[get_db] = _db
        self.client = TestClient(app)

    def _result(self, task_id: str, app=None) -> SimpleNamespace:
        return self.results.get(
            task_id, SimpleNamespace(state="PENDING", info=None, result=None)
        )

    async def _dispatch(self, name: str, *, kwargs=None, task_id=None, **_options):
        self.dispatched.append({"name": name, "kwargs": kwargs, "task_id": task_id})
        return SimpleNamespace(id=task_id)

    def publish(self, export_id: str, created_at: str = "2026-10-17T14:02:09+00:00"):
        built = self.export_dir / f"{export_id}.build"
        built.write_bytes(b"PK archive bytes")
        return backup_cache.publish(
            self.export_dir,
            str(built),
            backup_cache.CachedExport(
                export_id=export_id,
                created_at=created_at,
                include_audio=True,
                archive_quality="compressed",
                fingerprint="f",
                warnings={},
                app_version="1.0.0",
                filename="",
                size_bytes=0,
            ),
        )

    def export(self, include_audio: bool = True, quality: str = "compressed"):
        return self.client.post(
            "/backup/export",
            params={"include_audio": include_audio, "archive_quality": quality},
        )


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    return Harness(tmp_path, monkeypatch)


def test_an_unchanged_backup_is_ready_at_once(harness: Harness) -> None:
    harness.current_export = harness.publish("build-1")

    response = harness.export()

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["export"]["export_id"] == "build-1"
    assert harness.dispatched == []


def test_a_stale_backup_is_built_in_the_background(harness: Harness) -> None:
    response = harness.export()

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "building"
    assert harness.dispatched == [
        {
            "name": "backend.worker.tasks.create_backup_task",
            "kwargs": {"include_audio": True, "archive_quality": "compressed"},
            "task_id": body["task_id"],
        }
    ]
    assert json.loads(harness.redis[EXPORT_JOB_KEY])["task_id"] == body["task_id"]


def test_a_second_request_for_the_same_backup_joins_the_first(harness: Harness) -> None:
    first = harness.export().json()

    second = harness.export()

    assert second.status_code == 202
    assert second.json()["task_id"] == first["task_id"]
    assert len(harness.dispatched) == 1


def test_a_request_for_a_different_backup_waits_for_the_running_one(
    harness: Harness,
) -> None:
    harness.export(include_audio=True, quality="compressed")

    response = harness.export(include_audio=True, quality="original")

    assert response.status_code == 409
    assert "Compressed" in response.json()["detail"]
    assert len(harness.dispatched) == 1


def test_a_lost_build_does_not_block_a_new_one(harness: Harness) -> None:
    harness.redis[EXPORT_JOB_KEY] = json.dumps(
        {
            "task_id": "lost",
            "include_audio": True,
            "archive_quality": "original",
            "requested_at": time.time() - EXPORT_STALL_SECONDS - 60,
        }
    )

    response = harness.export(include_audio=True, quality="compressed")

    assert response.status_code == 202
    assert response.json()["task_id"] != "lost"
    assert len(harness.dispatched) == 1


def test_quality_is_ignored_for_a_backup_without_audio(harness: Harness) -> None:
    harness.export(include_audio=False, quality="original")

    assert harness.dispatched[0]["kwargs"] == {
        "include_audio": False,
        "archive_quality": "compressed",
    }


def test_no_backup_starts_during_a_restore(harness: Harness) -> None:
    harness.redis[RESTORE_LOCK_KEY] = "restore-1"

    response = harness.export()

    assert response.status_code == 409
    assert harness.dispatched == []


def test_no_restore_upload_starts_during_a_backup(harness: Harness) -> None:
    harness.export()

    response = harness.client.post(
        "/backup/upload/init",
        params={"filename": "backup.zip", "file_size": 10, "total_chunks": 1},
    )

    assert response.status_code == 409


def test_the_cached_backup_downloads_under_the_time_it_was_made(
    harness: Harness,
) -> None:
    harness.publish("build-1", created_at="2026-10-17T14:02:09+00:00")

    response = harness.client.get("/backup/export/build-1/download")

    assert response.status_code == 200
    assert response.content == b"PK archive bytes"
    assert (
        'filename="nojoin_backup_20261017_140209.zip"'
        in response.headers["content-disposition"]
    )


def test_a_replaced_backup_is_gone(harness: Harness) -> None:
    harness.publish("build-1")
    harness.publish("build-2")

    response = harness.client.get("/backup/export/build-1/download")

    assert response.status_code == 410
    assert not (harness.export_dir / "nojoin-backup-build-1.zip").exists()


def test_status_never_hands_the_browser_a_server_path(harness: Harness) -> None:
    harness.publish("build-1")

    body = harness.client.get("/backup/export/build-1").json()

    assert body["state"] == "SUCCESS"
    assert "zip_path" not in json.dumps(body)
    assert str(harness.export_dir) not in json.dumps(body)


def test_latest_describes_the_cache_and_the_running_build(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness.publish("build-1")
    monkeypatch.setattr(
        backup_endpoints, "current_fingerprint", lambda include_audio, quality: "f"
    )
    task_id = harness.export(include_audio=False).json()["task_id"]

    body = harness.client.get("/backup/export/latest").json()

    assert body["export"]["export_id"] == "build-1"
    assert body["current"] is True
    assert body["job"] == {
        "task_id": task_id,
        "include_audio": False,
        "archive_quality": "compressed",
    }


def test_deleting_the_cached_backup_reclaims_it(harness: Harness) -> None:
    harness.publish("build-1")

    assert harness.client.delete("/backup/export/latest").json() == {"deleted": True}
    assert harness.client.get("/backup/export/build-1/download").status_code == 410


def test_a_restore_the_server_no_longer_tracks_stops_polling(harness: Harness) -> None:
    body = harness.client.get("/backup/import/forgotten").json()

    assert body["status"] == "unknown"


def test_a_queued_restore_is_still_pending(harness: Harness) -> None:
    harness.redis[RESTORE_LOCK_KEY] = "queued"

    body = harness.client.get("/backup/import/queued").json()

    assert body["status"] == "pending"
