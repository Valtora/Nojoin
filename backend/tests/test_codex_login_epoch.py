"""A ChatGPT subscription connected by device login starts live calls afresh (#994).

The login task stores the credential itself, so it is where the live calls'
backoff epoch moves: the browser's poll may never see the connection if its
dialog was closed before the user approved on the other device.
"""

from __future__ import annotations

import pytest

from backend.processing.cli.codex_login import CodexLoginError
from backend.processing.live_ai_backoff import EPOCH_KEY
from backend.worker.tasks import cli_login


@pytest.fixture
def login(monkeypatch, tmp_path):
    published: list[dict] = []
    stored: list[str] = []
    monkeypatch.setattr(cli_login, "codex_home_for", lambda user_id: tmp_path)
    monkeypatch.setattr(
        cli_login.codex_oauth,
        "publish_login_state",
        lambda user_id, state: published.append(state),
    )
    monkeypatch.setattr(
        cli_login,
        "store_codex_auth_blob_sync",
        lambda user_id, blob: stored.append(blob),
    )

    def run(outcome):
        def device_login(user_id, publish_code):
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr(cli_login, "run_device_login", device_login)
        cli_login.codex_device_login_task.run(7)
        return published, stored

    return run


def test_a_stored_credential_moves_the_epoch(login, fake_live_ai_redis):
    published, stored = login('{"tokens": "made-up"}')

    assert stored == ['{"tokens": "made-up"}']
    assert published[-1]["status"] == "connected"
    assert fake_live_ai_redis.get(EPOCH_KEY) == "1"


@pytest.mark.parametrize(
    "error",
    [CodexLoginError("the code lapsed"), RuntimeError("the binary crashed")],
    ids=["login-error", "unexpected"],
)
def test_a_failed_login_leaves_the_epoch(login, fake_live_ai_redis, error):
    published, stored = login(error)

    assert stored == []
    assert published[-1]["status"] == "failed"
    assert fake_live_ai_redis.get(EPOCH_KEY) is None
