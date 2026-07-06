from __future__ import annotations

import base64
import hashlib

import pytest

from backend.services.cli_oauth import oauth


def test_generate_pkce_is_valid_s256():
    verifier, challenge = oauth.generate_pkce()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert challenge == expected
    assert "=" not in verifier and "=" not in challenge  # base64url, unpadded
    assert 43 <= len(verifier) <= 128


def test_build_authorize_url_carries_required_params():
    url = oauth.build_authorize_url("CHAL", "STATE")
    assert url.startswith(oauth.AUTHORIZE_URL + "?")
    for fragment in (
        f"client_id={oauth.ANTHROPIC_CLIENT_ID}",
        "response_type=code",
        "code_challenge=CHAL",
        "code_challenge_method=S256",
        "state=STATE",
        "code=true",
        "scope=user%3Ainference",
    ):
        assert fragment in url, fragment


def test_parse_pasted_code_handles_all_shapes():
    assert oauth.parse_pasted_code("  bareCode  ") == ("bareCode", None)
    assert oauth.parse_pasted_code("theCode#theState") == ("theCode", "theState")
    assert oauth.parse_pasted_code(
        "https://platform.claude.com/oauth/code/callback?code=C1&state=S1"
    ) == ("C1", "S1")
    assert oauth.parse_pasted_code("") == ("", None)


def test_tokens_from_response_maps_fields():
    tokens = oauth._tokens_from_response(
        {
            "access_token": "sk-ant-oat01-x",
            "refresh_token": "sk-ant-ort01-y",
            "expires_in": 28800,
            "scope": "user:inference user:profile",
        }
    )
    assert tokens.access_token == "sk-ant-oat01-x"
    assert tokens.refresh_token == "sk-ant-ort01-y"
    assert tokens.expires_in == 28800
    assert tokens.scope == "user:inference user:profile"


def test_tokens_from_response_requires_access_token():
    with pytest.raises(oauth.CliOAuthExchangeError):
        oauth._tokens_from_response({"refresh_token": "r"})
