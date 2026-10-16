from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.api import deps
from backend.api.services.release_service import ReleaseCatalog
from backend.api.v1.endpoints import version as version_endpoint
from backend.core import security
from backend.models.user import User

LATEST_URL = "https://example.invalid/releases/v2.10.0"


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    def add(self, _instance) -> None:
        pass

    async def commit(self) -> None:
        self.commits += 1


def _user(role: str = "owner", settings: dict | None = None) -> User:
    return User(
        username="pat",
        hashed_password="x",
        role=role,
        settings=settings or {},
    )


@pytest.fixture(autouse=True)
def release_state(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    state = {"installed": "2.9.0", "latest": "2.10.0"}

    async def fake_release_catalog(force_refresh: bool = False) -> ReleaseCatalog:
        return ReleaseCatalog(
            source="github-releases",
            latest_version=state["latest"],
            latest_release_url=LATEST_URL,
        )

    monkeypatch.setattr(
        version_endpoint, "get_installed_version", lambda: state["installed"]
    )
    monkeypatch.setattr(version_endpoint, "get_release_catalog", fake_release_catalog)
    return state


async def _dismiss(user: User, action: str, version: str, session: str | None):
    await version_endpoint.dismiss_update_notice(
        version_endpoint.UpdateNoticeDismissal(action=action, version=version),
        current_user=user,
        session_id=session,
        db=FakeSession(),
    )


@pytest.mark.anyio
async def test_admin_is_shown_a_newer_release() -> None:
    notice = await version_endpoint.get_update_notice(_user(), "session-a")

    assert notice.show is True
    assert notice.current_version == "2.9.0"
    assert notice.latest_version == "2.10.0"
    assert notice.release_url == LATEST_URL


@pytest.mark.anyio
async def test_ordinary_user_is_never_shown_the_notice() -> None:
    notice = await version_endpoint.get_update_notice(_user(role="user"), "session-a")

    assert notice.show is False
    assert notice.latest_version is None


@pytest.mark.anyio
async def test_current_install_is_not_shown_the_notice(
    release_state: dict[str, str],
) -> None:
    release_state["installed"] = "2.10.0"

    notice = await version_endpoint.get_update_notice(_user(), "session-a")

    assert notice.show is False


@pytest.mark.anyio
async def test_skip_hides_that_release_until_a_newer_one(
    release_state: dict[str, str],
) -> None:
    user = _user()
    await _dismiss(user, "skip", "2.10.0", "session-a")

    assert (await version_endpoint.get_update_notice(user, "session-a")).show is False
    # Skip is not tied to the session that chose it.
    assert (await version_endpoint.get_update_notice(user, "session-b")).show is False

    release_state["latest"] = "2.10.1"
    assert (await version_endpoint.get_update_notice(user, "session-a")).show is True


@pytest.mark.anyio
async def test_skipping_an_older_release_keeps_the_newer_skip() -> None:
    user = _user()
    await _dismiss(user, "skip", "2.10.0", "session-a")
    await _dismiss(user, "skip", "2.9.5", "session-a")

    assert user.settings["update_notice"]["skipped_version"] == "2.10.0"


@pytest.mark.anyio
async def test_remind_hides_the_notice_for_this_login_only(
    release_state: dict[str, str],
) -> None:
    user = _user()
    await _dismiss(user, "remind", "2.10.0", "session-a")

    assert (await version_endpoint.get_update_notice(user, "session-a")).show is False
    # The next login mints a new session, which shows the notice again.
    assert (await version_endpoint.get_update_notice(user, "session-b")).show is True

    # A release published during the same session is shown in it.
    release_state["latest"] = "2.10.1"
    assert (await version_endpoint.get_update_notice(user, "session-a")).show is True


@pytest.mark.anyio
async def test_remind_stores_a_digest_rather_than_the_session_id() -> None:
    user = _user()
    await _dismiss(user, "remind", "2.10.0", "session-a")

    stored = user.settings["update_notice"]["reminded_session"]
    assert "session-a" not in stored
    assert len(stored) == 64


@pytest.mark.anyio
async def test_dismissal_keeps_other_user_settings() -> None:
    user = _user(settings={"personal_dictionary": ["Nojoin"]})
    await _dismiss(user, "skip", "2.10.0", "session-a")

    assert user.settings["personal_dictionary"] == ["Nojoin"]


@pytest.mark.anyio
async def test_remind_without_a_browser_session_is_rejected() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await _dismiss(_user(), "remind", "2.10.0", None)

    assert excinfo.value.status_code == 400


@pytest.mark.anyio
async def test_dismissal_rejects_a_malformed_version() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await _dismiss(_user(), "skip", "latest", "session-a")

    assert excinfo.value.status_code == 400


@pytest.fixture
def isolated_keyring(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.delenv("SECRET_KEY", raising=False)

    class _StubPathManager:
        user_data_directory = tmp_path

    monkeypatch.setattr(security, "path_manager", _StubPathManager())


def _request(cookie_token: str | None = None) -> Request:
    headers = []
    if cookie_token is not None:
        headers.append((b"cookie", f"access_token={cookie_token}".encode()))
    return Request({"type": "http", "headers": headers})


def _token(token_type: str, scope: str) -> str:
    return security.create_access_token(
        "pat",
        token_type=token_type,
        scopes=[scope],
        expires_delta=timedelta(minutes=10),
        token_version=0,
    )


@pytest.mark.anyio
async def test_session_id_is_the_session_cookie_jti(isolated_keyring) -> None:
    token = _token(security.SESSION_TOKEN_TYPE, security.WEB_SESSION_SCOPE)

    session_id = await deps.get_current_session_id(_request(token), None)

    assert session_id == security.decode_access_token(token)["jti"]


@pytest.mark.anyio
async def test_api_bearer_token_has_no_session_id(isolated_keyring) -> None:
    token = _token(security.API_TOKEN_TYPE, security.API_ACCESS_SCOPE)

    assert await deps.get_current_session_id(_request(), token) is None


@pytest.mark.anyio
async def test_unreadable_token_has_no_session_id(isolated_keyring) -> None:
    assert await deps.get_current_session_id(_request("not-a-jwt"), None) is None
    assert await deps.get_current_session_id(_request(), None) is None
