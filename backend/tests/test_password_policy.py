from __future__ import annotations

from collections.abc import AsyncGenerator
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from backend.api.deps import get_current_active_superuser, get_current_user, get_db
from backend.api.v1.endpoints.system import SetupRequest
from backend.core.security import (
    MIN_PASSWORD_LENGTH,
    get_password_hash,
    hash_user_password,
    validate_password_policy,
    verify_password,
)
from backend.main import create_app
from backend.models.user import UserCreate, UserPasswordUpdate, UserUpdate


SECURE_TEST_BASE_URL = "https://test"


class _WritableSession:
    def add(self, value):
        return None

    async def commit(self):
        return None


async def _override_get_db() -> AsyncGenerator[_WritableSession, None]:
    yield _WritableSession()


def _iter_validation_errors(exc: ValidationError) -> list[dict[str, object]]:
    return exc.errors(include_url=False)


def _has_validation_error(
    errors: list[dict[str, object]],
    *,
    field_name: str,
    message_fragment: str,
) -> bool:
    for error in errors:
        loc = error.get("loc")
        msg = error.get("msg")
        if (
            isinstance(loc, tuple)
            and loc
            and loc[-1] == field_name
            and isinstance(msg, str)
            and message_fragment in msg
        ):
            return True
    return False


def _has_response_validation_error(response, *, field_name: str, message_fragment: str) -> bool:
    detail = response.json().get("detail", [])
    return any(
        isinstance(item, dict)
        and isinstance(item.get("loc"), list)
        and item["loc"]
        and item["loc"][-1] == field_name
        and isinstance(item.get("msg"), str)
        and message_fragment in item["msg"]
        for item in detail
    )


def test_validate_password_policy_rejects_short_passwords() -> None:
    with pytest.raises(ValueError, match=f"at least {MIN_PASSWORD_LENGTH} characters"):
        validate_password_policy("short")


def test_validate_password_policy_rejects_all_whitespace_passwords() -> None:
    with pytest.raises(ValueError, match="all whitespace"):
        validate_password_policy(" " * MIN_PASSWORD_LENGTH)


def test_hash_user_password_hashes_valid_passwords() -> None:
    hashed_password = hash_user_password("validpass")

    assert verify_password("validpass", hashed_password)


def test_verify_password_accepts_existing_argon2id_hashes() -> None:
    existing_hash = (
        "$argon2id$v=19$m=65536,t=3,p=4$JGSMsTaGkPIeQ0jpnfOecw$"
        "cfvQa00hyJkmz8cPb4xgl6pi+jHPxUeqG0YijjkFfrY"
    )

    assert verify_password("validpass", existing_hash)
    assert not verify_password("wrongpass", existing_hash)


def test_user_create_rejects_all_whitespace_passwords() -> None:
    with pytest.raises(ValidationError) as exc_info:
        UserCreate(
            username="new-user",
            password=" " * MIN_PASSWORD_LENGTH,
        )

    assert _has_validation_error(
        _iter_validation_errors(exc_info.value),
        field_name="password",
        message_fragment="all whitespace",
    )


def test_user_update_rejects_short_passwords() -> None:
    with pytest.raises(ValidationError) as exc_info:
        UserUpdate(password="short")

    assert _has_validation_error(
        _iter_validation_errors(exc_info.value),
        field_name="password",
        message_fragment=f"at least {MIN_PASSWORD_LENGTH} characters",
    )


def test_user_password_update_allows_short_current_password_for_grandfathered_users() -> None:
    password_update = UserPasswordUpdate(
        current_password="short",
        new_password="validpass",
    )

    assert password_update.current_password == "short"
    assert password_update.new_password == "validpass"


def test_setup_request_rejects_all_whitespace_passwords() -> None:
    with pytest.raises(ValidationError) as exc_info:
        SetupRequest(
            username="owner",
            password=" " * MIN_PASSWORD_LENGTH,
        )

    assert _has_validation_error(
        _iter_validation_errors(exc_info.value),
        field_name="password",
        message_fragment="all whitespace",
    )


@pytest.mark.anyio
async def test_register_rejects_all_whitespace_passwords() -> None:
    app = create_app(app_lifespan=None)
    app.dependency_overrides[get_db] = _override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL) as client:
        response = await client.post(
            "/api/v1/users/register",
            json={
                "username": "new-user",
                "password": " " * MIN_PASSWORD_LENGTH,
                "invite_code": "invite123",
            },
        )

    assert response.status_code == 422
    assert _has_response_validation_error(
        response,
        field_name="password",
        message_fragment="all whitespace",
    )


@pytest.mark.anyio
async def test_create_user_rejects_short_passwords() -> None:
    app = create_app(app_lifespan=None)
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=1,
        role="admin",
        is_superuser=False,
        force_password_change=False,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL) as client:
        response = await client.post(
            "/api/v1/users/",
            json={
                "username": "new-user",
                "password": "short",
                "role": "user",
            },
        )

    assert response.status_code == 422
    assert _has_response_validation_error(
        response,
        field_name="password",
        message_fragment=f"at least {MIN_PASSWORD_LENGTH} characters",
    )


@pytest.mark.anyio
async def test_update_user_password_rejects_all_whitespace_passwords() -> None:
    app = create_app(app_lifespan=None)
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_active_superuser] = lambda: SimpleNamespace(
        id=1,
        role="owner",
        is_superuser=True,
        force_password_change=False,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL) as client:
        response = await client.patch(
            "/api/v1/users/1",
            json={
                "password": " " * MIN_PASSWORD_LENGTH,
            },
        )

    assert response.status_code == 422
    assert _has_response_validation_error(
        response,
        field_name="password",
        message_fragment="all whitespace",
    )


@pytest.mark.anyio
async def test_update_password_me_rejects_all_whitespace_new_passwords() -> None:
    app = create_app(app_lifespan=None)
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=1,
        username="legacy-user",
        role="user",
        is_superuser=False,
        force_password_change=False,
        hashed_password=get_password_hash("legacy1"),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL) as client:
        response = await client.put(
            "/api/v1/users/me/password",
            json={
                "current_password": "legacy1",
                "new_password": " " * MIN_PASSWORD_LENGTH,
            },
        )

    assert response.status_code == 422
    assert _has_response_validation_error(
        response,
        field_name="new_password",
        message_fragment="all whitespace",
    )


@pytest.mark.anyio
async def test_setup_rejects_short_passwords(monkeypatch) -> None:
    app = create_app(app_lifespan=None)
    app.dependency_overrides[get_db] = _override_get_db
    monkeypatch.setenv("FIRST_RUN_PASSWORD", "bootstrap-secret")

    async with AsyncClient(transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL) as client:
        response = await client.post(
            "/api/v1/system/setup",
            headers={"Authorization": "Bootstrap bootstrap-secret"},
            json={
                "username": "owner",
                "password": "short",
            },
        )

    assert response.status_code == 422
    assert _has_response_validation_error(
        response,
        field_name="password",
        message_fragment=f"at least {MIN_PASSWORD_LENGTH} characters",
    )


@pytest.mark.anyio
async def test_update_password_me_accepts_grandfathered_short_current_passwords() -> None:
    app = create_app(app_lifespan=None)
    session = _WritableSession()

    async def override_get_db() -> AsyncGenerator[_WritableSession, None]:
        yield session

    current_user = SimpleNamespace(
        id=1,
        username="legacy-user",
        role="user",
        is_superuser=False,
        force_password_change=True,
        hashed_password=get_password_hash("short"),
    )

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: current_user

    async with AsyncClient(transport=ASGITransport(app=app), base_url=SECURE_TEST_BASE_URL) as client:
        response = await client.put(
            "/api/v1/users/me/password",
            json={
                "current_password": "short",
                "new_password": "validpass",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"message": "Password updated successfully"}
    assert verify_password("validpass", current_user.hashed_password)
    assert current_user.force_password_change is False