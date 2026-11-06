"""Saving Settings > AI providers with an Ollama address that no longer resolves.

The page sends every setting with each save, the Ollama addresses included. An
address the install already holds is not resolved again, so a host that has
gone away does not block unrelated changes (#945). A new address still goes
through the whole outbound policy and is refused with a message the page shows.
"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path

import pytest

from backend.api.v1.endpoints import settings as settings_ep
from backend.tests.test_install_wide_transcription_settings import _call_settings
from backend.utils.config_manager import ConfigManager

GONE = "http://ollama-gone.example.test:11434"
LAN = "http://ollama-lan.example.test:11434"
LOOPBACK = "http://ollama-loop.example.test:11434"

# What each made-up host resolves to. Any other name fails as an unknown host
# does, so no test reaches a real resolver.
_ADDRESSES = {
    "ollama-lan.example.test": "192.168.1.20",
    "ollama-loop.example.test": "127.0.0.1",
}

URL_KEYS = ("ollama_api_url", "secondary_ollama_api_url")


def _fake_getaddrinfo(host, *args, **kwargs):
    address = _ADDRESSES.get(host)
    if address is None:
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 0))]


@pytest.fixture(autouse=True)
def _offline_resolver(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)


def _config(tmp_path: Path, monkeypatch, stored: dict) -> ConfigManager:
    for env_key in ("OLLAMA_API_URL", "SECONDARY_OLLAMA_API_URL"):
        monkeypatch.delenv(env_key, raising=False)
    monkeypatch.setattr(ConfigManager, "_ensure_dirs_exist", lambda self, cfg: None)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(stored), encoding="utf-8")
    manager = ConfigManager(config_path=str(path))
    monkeypatch.setattr(settings_ep, "config_manager", manager)
    return manager


def _saved(manager: ConfigManager) -> dict:
    return json.loads(Path(manager.config_path).read_text(encoding="utf-8"))


def _save(payload: dict, role: str = "admin") -> tuple[int, dict]:
    status, body, _ = asyncio.run(_call_settings(role, {}, "POST", payload))
    return status, body


@pytest.mark.parametrize("key", URL_KEYS)
@pytest.mark.parametrize("sent", [GONE, GONE + "/"], ids=["same", "trailing-slash"])
def test_an_unchanged_unresolvable_address_does_not_block_another_setting(
    tmp_path, monkeypatch, key, sent
):
    config = _config(tmp_path, monkeypatch, {key: GONE})

    status, body = _save({key: sent, "ollama_think": True})

    assert status == 200, body
    saved = _saved(config)
    assert saved["ollama_think"] is True
    assert saved[key] == GONE


def test_an_address_from_the_environment_counts_as_unchanged(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch, {})
    monkeypatch.setenv("OLLAMA_API_URL", GONE)
    config.reload(force=True)

    status, body = _save({"ollama_api_url": GONE, "ollama_context_window": 8192})

    assert status == 200, body
    assert _saved(config)["ollama_context_window"] == 8192


@pytest.mark.parametrize(
    ("key", "label"),
    [
        ("ollama_api_url", "The Ollama address"),
        ("secondary_ollama_api_url", "The fallback Ollama address"),
    ],
)
def test_a_new_unresolvable_address_is_refused_with_a_clear_message(
    tmp_path, monkeypatch, key, label
):
    config = _config(tmp_path, monkeypatch, {key: LAN})

    status, body = _save({key: GONE, "ollama_think": True})

    assert status == 400
    assert body == {
        "detail": f"{label} was not saved. Could not resolve Ollama hostname."
    }
    saved = _saved(config)
    assert saved[key] == LAN
    assert "ollama_think" not in saved


@pytest.mark.parametrize("key", URL_KEYS)
def test_a_new_private_network_address_is_still_accepted(tmp_path, monkeypatch, key):
    config = _config(tmp_path, monkeypatch, {key: GONE})

    status, body = _save({key: LAN + "/"})

    assert status == 200, body
    assert _saved(config)[key] == LAN


@pytest.mark.parametrize("key", URL_KEYS)
@pytest.mark.parametrize(
    ("url", "reason"),
    [
        (LOOPBACK, "Loopback, link-local"),
        ("http://localhost:11434", "Internal Nojoin service hostnames are not allowed"),
        ("http://[::1]:11434", "Loopback Ollama hostnames are not allowed."),
        ("http://127.0.0.2:11434", "Loopback, link-local"),
        ("http://db:5432", "Internal Nojoin service hostnames are not allowed"),
        ("ftp://ollama-lan.example.test", "Invalid Ollama API URL format."),
    ],
    ids=[
        "resolves-to-loopback",
        "localhost",
        "ipv6-loopback",
        "loopback-ip",
        "service",
        "scheme",
    ],
)
def test_a_new_loopback_or_internal_address_is_still_refused(
    tmp_path, monkeypatch, key, url, reason
):
    config = _config(tmp_path, monkeypatch, {key: GONE})

    status, body = _save({key: url})

    assert status == 400
    assert reason in body["detail"]
    assert _saved(config)[key] == GONE


def test_a_member_sending_an_unresolvable_address_saves_their_own_setting(
    tmp_path, monkeypatch
):
    """A member's copy of the install-wide address is dropped before any check."""
    config = _config(tmp_path, monkeypatch, {"ollama_api_url": LAN})

    status, body = _save({"ollama_api_url": GONE, "theme": "light"}, role="user")

    assert status == 200, body
    assert body["theme"] == "light"
    assert _saved(config)["ollama_api_url"] == LAN
