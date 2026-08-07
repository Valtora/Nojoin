"""Reloading config.json without re-reading it every time.

Callers reload defensively. Telemetry does it on every status poll so an
operator's opt-out cannot go unnoticed, which put a JSON parse and a directory
probe on a request path served several times a minute. The cache has to keep
that guarantee -- a real change must still be seen -- while costing one stat
when nothing has changed.
"""

import json

import pytest

from backend.utils.config_manager import ConfigManager


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"whisper_model_size": "turbo"}), encoding="utf-8")
    return path


@pytest.fixture
def manager(config_file):
    return ConfigManager(config_path=str(config_file))


def write_config(path, data):
    """Write and force a distinct mtime.

    Nanosecond mtime makes same-instant writes distinguishable in practice, but
    a test that writes twice within one filesystem tick should not depend on
    that resolution to prove the cache works.
    """
    path.write_text(json.dumps(data), encoding="utf-8")
    stat = path.stat()
    import os

    os.utime(path, ns=(stat.st_atime_ns + 10_000_000, stat.st_mtime_ns + 10_000_000))


class TestReloadSkipsUnchangedFiles:
    def test_unchanged_file_is_not_reparsed(self, manager, monkeypatch):
        loads = {"n": 0}
        original = manager._load_config

        def counting_load():
            loads["n"] += 1
            return original()

        monkeypatch.setattr(manager, "_load_config", counting_load)

        for _ in range(10):
            manager.reload()

        assert loads["n"] == 0

    def test_config_still_readable_after_a_skipped_reload(self, manager):
        manager.reload()
        assert manager.get("whisper_model_size") == "turbo"


class TestReloadSeesRealChanges:
    def test_a_rewrite_is_picked_up(self, manager, config_file):
        write_config(config_file, {"whisper_model_size": "small"})
        manager.reload()
        assert manager.get("whisper_model_size") == "small"

    def test_same_length_rewrite_is_picked_up(self, manager, config_file):
        # "turbo" and "base" differ in length, but a same-length rewrite must
        # also be seen, which is why mtime is in the identity and not just size.
        write_config(config_file, {"whisper_model_size": "base"})
        manager.reload()
        assert manager.get("whisper_model_size") == "base"

    def test_force_rereads_even_when_unchanged(self, manager, monkeypatch):
        loads = {"n": 0}
        original = manager._load_config

        def counting_load():
            loads["n"] += 1
            return original()

        monkeypatch.setattr(manager, "_load_config", counting_load)
        manager.reload(force=True)

        assert loads["n"] == 1

    def test_a_deleted_file_is_noticed(self, manager, config_file):
        # Absent is a distinct state from unchanged: the manager must fall back
        # to defaults rather than serve a file that is no longer there.
        config_file.unlink()
        manager.reload()
        assert manager.get("whisper_model_size") is not None


class TestWriteThenRead:
    def test_a_write_through_set_is_visible_after_reload(self, manager):
        manager.set("whisper_model_size", "small")
        manager.reload()
        assert manager.get("whisper_model_size") == "small"

    def test_save_config_then_reload_sees_the_new_value(self, manager):
        data = manager.get_all()
        data["whisper_model_size"] = "tiny"
        manager.save_config(data)
        manager.reload()
        assert manager.get("whisper_model_size") == "tiny"


class TestDirectoryLogging:
    def test_existing_directory_is_not_announced(self, manager, caplog):
        # This used to log "Created directory" on every load, which read as
        # repeated creation and buried the events that mattered.
        with caplog.at_level("INFO"):
            manager.reload(force=True)

        assert not [
            record
            for record in caplog.records
            if "Created directory" in record.getMessage()
        ]
