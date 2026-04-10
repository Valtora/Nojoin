from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api import deps
from backend.api.v1.endpoints import users
from backend.models.user import UserRole


def make_user(*, role: str, is_superuser: bool = False, force_password_change: bool = False):
    return SimpleNamespace(
        role=role,
        is_superuser=is_superuser,
        force_password_change=force_password_change,
    )


def make_user_input(*, role: str, is_superuser: bool = False):
    return SimpleNamespace(role=role, is_superuser=is_superuser)


def test_resolve_created_user_privileges_blocks_admin_creating_owner_account():
    with pytest.raises(HTTPException) as exc_info:
        users.resolve_created_user_privileges(
            make_user(role=UserRole.ADMIN),
            make_user_input(role=UserRole.OWNER),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Cannot create owner accounts"


def test_resolve_created_user_privileges_blocks_non_superuser_creating_superuser_account():
    with pytest.raises(HTTPException) as exc_info:
        users.resolve_created_user_privileges(
            make_user(role=UserRole.OWNER, is_superuser=False),
            make_user_input(role=UserRole.ADMIN, is_superuser=True),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Cannot create superuser accounts"


def test_resolve_created_user_privileges_allows_owner_to_create_owner_account():
    role, is_superuser = users.resolve_created_user_privileges(
        make_user(role=UserRole.OWNER, is_superuser=False),
        make_user_input(role=UserRole.OWNER, is_superuser=False),
    )

    assert role == UserRole.OWNER
    assert is_superuser is False


def test_resolve_created_user_privileges_allows_superuser_to_create_superuser_account():
    role, is_superuser = users.resolve_created_user_privileges(
        make_user(role=UserRole.ADMIN, is_superuser=True),
        make_user_input(role=UserRole.ADMIN, is_superuser=True),
    )

    assert role == UserRole.ADMIN
    assert is_superuser is True


def test_enforce_password_change_policy_blocks_non_exempt_route():
    with pytest.raises(HTTPException) as exc_info:
        deps.enforce_password_change_policy(
            make_user(role=UserRole.USER, force_password_change=True),
            path="/api/v1/recordings/123",
            method="GET",
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Password change required"


def test_enforce_password_change_policy_allows_profile_and_password_routes():
    user = make_user(role=UserRole.USER, force_password_change=True)

    deps.enforce_password_change_policy(
        user,
        path="/api/v1/users/me",
        method="GET",
    )
    deps.enforce_password_change_policy(
        user,
        path="/api/v1/users/me/password",
        method="PUT",
    )


def test_validate_companion_recording_claim_ignores_non_companion_tokens():
    deps._validate_companion_recording_claim(
        {"token_type": "session"},
        42,
    )


def test_validate_companion_recording_claim_rejects_mismatched_recording_id():
    with pytest.raises(HTTPException) as exc_info:
        deps._validate_companion_recording_claim(
            {"token_type": "companion", "recording_id": 7},
            42,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Token does not grant access to this recording"


def test_validate_companion_recording_claim_accepts_matching_recording_id():
    deps._validate_companion_recording_claim(
        {"token_type": "companion", "recording_id": "42"},
        42,
    )