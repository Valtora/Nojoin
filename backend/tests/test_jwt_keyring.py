from datetime import timedelta

import pytest

from backend.core import security


@pytest.fixture
def isolated_keyring(monkeypatch, tmp_path):
    """Force the security keyring to live inside ``tmp_path`` and ignore env."""
    monkeypatch.delenv("SECRET_KEY", raising=False)

    class _StubPathManager:
        user_data_directory = tmp_path

    monkeypatch.setattr(security, "path_manager", _StubPathManager())
    yield tmp_path


def test_create_access_token_requires_token_version_for_session(isolated_keyring):
    with pytest.raises(ValueError):
        security.create_access_token(
            "alice",
            token_type=security.SESSION_TOKEN_TYPE,
            scopes=[security.WEB_SESSION_SCOPE],
            expires_delta=timedelta(minutes=5),
        )


def test_session_token_round_trip_carries_jti_iat_and_tv(isolated_keyring):
    token = security.create_access_token(
        "alice",
        token_type=security.SESSION_TOKEN_TYPE,
        scopes=[security.WEB_SESSION_SCOPE],
        expires_delta=timedelta(minutes=5),
        token_version=7,
    )

    decoded = security.decode_access_token(token)

    assert decoded["sub"] == "alice"
    assert decoded["token_type"] == security.SESSION_TOKEN_TYPE
    assert decoded["tv"] == 7
    assert isinstance(decoded.get("jti"), str) and decoded["jti"]
    assert "iat" in decoded


def test_companion_token_does_not_carry_jti_or_tv(isolated_keyring):
    token = security.create_access_token(
        "alice",
        token_type=security.COMPANION_TOKEN_TYPE,
        scopes=[security.COMPANION_BOOTSTRAP_SCOPE],
        expires_delta=timedelta(minutes=5),
    )

    decoded = security.decode_access_token(token)

    assert "jti" not in decoded
    assert "tv" not in decoded


def test_keyring_rotation_keeps_old_tokens_verifying(isolated_keyring):
    token_before = security.create_access_token(
        "alice",
        token_type=security.SESSION_TOKEN_TYPE,
        scopes=[security.WEB_SESSION_SCOPE],
        expires_delta=timedelta(minutes=5),
        token_version=0,
    )

    new_kid = security.rotate_signing_key()

    token_after = security.create_access_token(
        "alice",
        token_type=security.SESSION_TOKEN_TYPE,
        scopes=[security.WEB_SESSION_SCOPE],
        expires_delta=timedelta(minutes=5),
        token_version=0,
    )

    # Old key still in keyring, so the old token still verifies.
    decoded_old = security.decode_access_token(token_before)
    decoded_new = security.decode_access_token(token_after)

    assert decoded_old["sub"] == "alice"
    assert decoded_new["sub"] == "alice"
    assert security.get_active_signing_key()[0] == new_kid


def test_pruning_retired_keys_invalidates_tokens_signed_by_them(isolated_keyring):
    token_before = security.create_access_token(
        "alice",
        token_type=security.SESSION_TOKEN_TYPE,
        scopes=[security.WEB_SESSION_SCOPE],
        expires_delta=timedelta(minutes=5),
        token_version=0,
    )
    original_kid = security.get_active_signing_key()[0]

    security.rotate_signing_key()
    removed = security.prune_signing_keys(keep_kids=set())

    assert original_kid in removed

    from jose import JWTError

    with pytest.raises(JWTError):
        security.decode_access_token(token_before)


def test_secret_key_env_disables_rotation(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "env-key")

    class _StubPathManager:
        user_data_directory = tmp_path

    monkeypatch.setattr(security, "path_manager", _StubPathManager())

    with pytest.raises(RuntimeError):
        security.rotate_signing_key()
