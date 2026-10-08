import json
import re
from datetime import timedelta

import jwt
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

    with pytest.raises(jwt.InvalidTokenError):
        security.decode_access_token(token_before)


def test_secret_key_env_disables_rotation(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "env-key")

    class _StubPathManager:
        user_data_directory = tmp_path

    monkeypatch.setattr(security, "path_manager", _StubPathManager())

    with pytest.raises(RuntimeError):
        security.rotate_signing_key()


def _empty_active_keyring(directory) -> None:
    (directory / ".secret_keys.json").write_text(
        json.dumps({"active": "legacy", "keys": {"legacy": ""}}), encoding="utf-8"
    )


def _session_token() -> str:
    return security.create_access_token(
        "alice",
        token_type=security.SESSION_TOKEN_TYPE,
        scopes=[security.WEB_SESSION_SCOPE],
        expires_delta=timedelta(minutes=5),
        token_version=0,
    )


def test_empty_legacy_secret_key_is_refused_with_the_file_named(isolated_keyring):
    legacy_file = isolated_keyring / ".secret_key"
    legacy_file.write_text("\n", encoding="utf-8")

    with pytest.raises(
        security.SigningKeyUnavailableError, match=re.escape(str(legacy_file))
    ):
        security.get_signing_keyring()

    # Nothing is persisted, so deleting the empty file and restarting
    # generates a fresh key instead of reloading an empty one.
    assert not (isolated_keyring / ".secret_keys.json").exists()
    assert legacy_file.exists()
    legacy_file.unlink()
    assert security.get_active_signing_key()[1]


def test_empty_active_keyring_key_is_refused_with_the_file_named(isolated_keyring):
    _empty_active_keyring(isolated_keyring)
    keyring_file = isolated_keyring / ".secret_keys.json"

    with pytest.raises(
        security.SigningKeyUnavailableError, match=re.escape(str(keyring_file))
    ):
        _session_token()


@pytest.mark.parametrize("content", ["", " \n"], ids=["zero-byte", "whitespace"])
def test_blank_keyring_file_gets_the_empty_key_remedy(isolated_keyring, content):
    keyring_file = isolated_keyring / ".secret_keys.json"
    keyring_file.write_text(content, encoding="utf-8")

    with pytest.raises(security.SigningKeyUnavailableError) as excinfo:
        security.get_signing_keyring()

    assert str(excinfo.value).startswith(
        f"The JWT signing key in {keyring_file} is empty"
    )
    assert f"Delete {keyring_file} and restart" in str(excinfo.value)


def test_rotation_replaces_an_empty_active_key(isolated_keyring):
    _empty_active_keyring(isolated_keyring)

    new_kid = security.rotate_signing_key()

    active_kid, active_key = security.get_active_signing_key()
    assert active_kid == new_kid
    assert len(active_key) == 64
    assert security.decode_access_token(_session_token())["sub"] == "alice"
