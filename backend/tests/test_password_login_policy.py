"""NOJOIN_PASSWORD_LOGIN and the password rules for SSO accounts."""

from __future__ import annotations

import pytest

from backend.core.oidc_config import (
    OIDCConfigurationError,
    reset_oidc_settings_cache,
    validate_sign_in_configuration,
)
from backend.tests.sso_sharing_support import build_harness

PASSWORD = "correct horse battery"


@pytest.fixture
async def harness():
    async for built in build_harness():
        yield built


@pytest.fixture
def policy(monkeypatch: pytest.MonkeyPatch):
    def _set(value: str | None) -> None:
        if value is None:
            monkeypatch.delenv("NOJOIN_PASSWORD_LOGIN", raising=False)
        else:
            monkeypatch.setenv("NOJOIN_PASSWORD_LOGIN", value)
        reset_oidc_settings_cache()

    yield _set
    reset_oidc_settings_cache()


async def _password_login(harness, username: str):
    async with harness.client() as client:
        return await client.post(
            "/api/v1/login/access-token",
            data={"username": username, "password": PASSWORD},
        )


@pytest.mark.anyio
async def test_enabled_by_default(harness, policy) -> None:
    policy(None)
    await harness.add_user("carol")
    assert (await _password_login(harness, "carol")).status_code == 200


@pytest.mark.anyio
async def test_owner_only_keeps_a_break_glass_login(harness, policy) -> None:
    policy("owner_only")
    await harness.add_user("job", role="owner")
    await harness.add_user("carol", role="admin")
    assert (await _password_login(harness, "job")).status_code == 200
    refused = await _password_login(harness, "carol")
    assert refused.status_code == 403
    assert refused.json()["detail"] == "password_login_disabled"


@pytest.mark.anyio
async def test_owner_only_does_not_reveal_accounts_without_the_password(
    harness, policy
) -> None:
    policy("owner_only")
    await harness.add_user("carol")
    async with harness.client() as client:
        response = await client.post(
            "/api/v1/login/access-token",
            data={"username": "carol", "password": "wrong password!!"},
        )
    assert response.status_code == 400
    assert response.json()["detail"] == "Incorrect username or password"


@pytest.mark.anyio
async def test_disabled_refuses_everyone(harness, policy) -> None:
    policy("disabled")
    await harness.add_user("job", role="owner")
    assert (await _password_login(harness, "job")).status_code == 403


@pytest.mark.anyio
async def test_registration_closes_unless_password_login_is_enabled(
    harness, policy
) -> None:
    policy("owner_only")
    async with harness.client() as client:
        response = await client.post(
            "/api/v1/users/register",
            json={"username": "mallory", "password": PASSWORD, "invite_code": "x"},
        )
    assert response.status_code == 403
    assert response.json()["detail"] == "registration_disabled"


@pytest.mark.anyio
async def test_sso_accounts_cannot_get_a_password(harness, policy) -> None:
    policy(None)
    sso_user = await harness.add_user("alice", sso_created=True)
    async with harness.client(sso_user) as client:
        response = await client.put(
            "/api/v1/users/me/password",
            json={
                "current_password": PASSWORD,
                "new_password": "another long password",
            },
        )
    assert response.status_code == 403
    assert response.json()["detail"] == "sso_account_has_no_password"

    admin = await harness.add_user("root", role="owner", is_superuser=True)
    async with harness.client(admin) as client:
        response = await client.patch(
            f"/api/v1/users/{sso_user.id}", json={"password": "another long password"}
        )
    assert response.status_code == 400
    assert response.json()["detail"] == "sso_account_has_no_password"


def test_disabled_without_sso_fails_startup(policy, monkeypatch) -> None:
    for name in (
        "NOJOIN_OIDC_ISSUER",
        "NOJOIN_OIDC_CLIENT_ID",
        "NOJOIN_OIDC_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    policy("disabled")
    with pytest.raises(OIDCConfigurationError):
        validate_sign_in_configuration()


def test_partial_sso_configuration_fails_startup(policy, monkeypatch) -> None:
    monkeypatch.setenv("NOJOIN_OIDC_ISSUER", "https://idp.test/")
    monkeypatch.delenv("NOJOIN_OIDC_CLIENT_ID", raising=False)
    monkeypatch.delenv("NOJOIN_OIDC_CLIENT_SECRET", raising=False)
    policy(None)
    with pytest.raises(OIDCConfigurationError, match="NOJOIN_OIDC_CLIENT_ID"):
        validate_sign_in_configuration()


def test_unknown_policy_fails_startup(policy) -> None:
    policy("sometimes")
    with pytest.raises(OIDCConfigurationError):
        validate_sign_in_configuration()
