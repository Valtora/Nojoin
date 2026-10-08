"""JWT verification contract: tokens issued before the PyJWT migration keep
working, every token type round-trips, and forged tokens are refused."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import warnings
from datetime import timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api import deps
from backend.core import security
from backend.utils.time import utc_now

# Minted once by python-jose 3.5.0 through upstream's create_access_token (the
# implementation this migration replaced), for user "alice" with
# token_version 3 and a 100-year expiry, signed by the keyring entry below.
# python-jose is no longer installed, so the tokens are kept as static strings:
# they prove that sessions, API tokens and MCP tokens issued before an upgrade
# still verify after it.
JOSE_KID = "k_f1x7ure"
JOSE_KEY = "6a4f0c2e9b8d71a3c5e0f4b2d6a8c1e3f5b7d9a0c2e4f6a8b0d2c4e6f8a0b2c4"
JOSE_TOKENS = {
    security.SESSION_TOKEN_TYPE: (
        "eyJhbGciOiJIUzI1NiIsImtpZCI6ImtfZjF4N3VyZSIsInR5cCI6IkpXVCJ9."
        "eyJleHAiOjQ5NDUwOTIxMzcsImlhdCI6MTc5MTQ5MjEzNywic3ViIjoiYWxpY2UiLCJ0b2tl"
        "bl90eXBlIjoic2Vzc2lvbiIsInNjb3BlcyI6WyJzZXNzaW9uOndlYiJdLCJqdGkiOiJhZGEy"
        "ZTVjODdmZDg0OTM1ODU5ZTljOGZlN2IwMzRjNyIsInR2IjozfQ."
        "fCA26sLNcXh8yo_HuKZSdp2I-4F_vEb58QD5BNfu7R4"
    ),
    security.API_TOKEN_TYPE: (
        "eyJhbGciOiJIUzI1NiIsImtpZCI6ImtfZjF4N3VyZSIsInR5cCI6IkpXVCJ9."
        "eyJleHAiOjQ5NDUwOTIxMzcsImlhdCI6MTc5MTQ5MjEzNywic3ViIjoiYWxpY2UiLCJ0b2tl"
        "bl90eXBlIjoiYXBpIiwic2NvcGVzIjpbImFwaTpmdWxsIl0sImp0aSI6Ijk5ZGZiMzU1YjY1"
        "MzQ3Nzk4MTE0YzE1OTFiNmQzOWQxIiwidHYiOjN9."
        "mZo7zyy0yeinsLASqjxlvVA6fULkdg3QXhm9y1fiSQM"
    ),
    security.MCP_TOKEN_TYPE: (
        "eyJhbGciOiJIUzI1NiIsImtpZCI6ImtfZjF4N3VyZSIsInR5cCI6IkpXVCJ9."
        "eyJleHAiOjQ5NDUwOTIxMzcsImlhdCI6MTc5MTQ5MjEzNywic3ViIjoiYWxpY2UiLCJ0b2tl"
        "bl90eXBlIjoibWNwIiwic2NvcGVzIjpbIm1jcDpyZWFkIiwibWNwOndyaXRlIl0sImp0aSI6"
        "ImI0YjE2ZjI3Yjc5NTQwOGZiNTNiZGNlZTQ2MzU4NTJhIiwidHYiOjMsImNsaWVudF9pZCI6"
        "ImNsaWVudC1maXh0dXJlIiwicmVzIjoiaHR0cHM6Ly9ub2pvaW4uZXhhbXBsZS9tY3AiLCJn"
        "cmFudF9pZCI6ImdyYW50LWZpeHR1cmUifQ."
        "Dt3OykV2yZUIddX_PgGOetAyNUHYOHNS1LdDS73bmjo"
    ),
}
JOSE_SCOPES = {
    security.SESSION_TOKEN_TYPE: [security.WEB_SESSION_SCOPE],
    security.API_TOKEN_TYPE: [security.API_ACCESS_SCOPE],
    security.MCP_TOKEN_TYPE: [security.MCP_READ_SCOPE, security.MCP_WRITE_SCOPE],
}
MCP_EXTRA_CLAIMS = {
    "client_id": "client-fixture",
    "res": "https://nojoin.example/mcp",
    "grant_id": "grant-fixture",
}
TOKEN_TYPES = list(JOSE_TOKENS)

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        username VARCHAR(255) NOT NULL,
        hashed_password VARCHAR(255) NOT NULL DEFAULT '',
        is_active BOOLEAN NOT NULL DEFAULT 1,
        is_superuser BOOLEAN NOT NULL DEFAULT 0,
        force_password_change BOOLEAN NOT NULL DEFAULT 0,
        role VARCHAR(32) NOT NULL DEFAULT 'user',
        token_version INTEGER NOT NULL DEFAULT 0,
        settings JSON,
        has_seen_demo_recording BOOLEAN NOT NULL DEFAULT 0,
        invitation_id INTEGER
    )
    """,
    """
    CREATE TABLE revoked_jwts (
        jti VARCHAR(64) PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        token_type VARCHAR(32) NOT NULL,
        expires_at DATETIME NOT NULL,
        revoked_at DATETIME NOT NULL,
        reason VARCHAR(64)
    )
    """,
]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def jose_keyring(monkeypatch, tmp_path):
    """A keyring holding the key the python-jose fixture tokens were signed with."""
    monkeypatch.delenv("SECRET_KEY", raising=False)

    class _StubPathManager:
        user_data_directory = tmp_path

    monkeypatch.setattr(security, "path_manager", _StubPathManager())
    (tmp_path / ".secret_keys.json").write_text(
        json.dumps({"active": JOSE_KID, "keys": {JOSE_KID: JOSE_KEY}}),
        encoding="utf-8",
    )
    yield tmp_path


@pytest.fixture
async def db_with_alice():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    async with engine.begin() as conn:
        for stmt in SCHEMA_STATEMENTS:
            await conn.execute(text(stmt))
        now = utc_now()
        await conn.execute(
            text(
                "INSERT INTO users (id, created_at, updated_at, username, token_version)"
                " VALUES (1, :ts, :ts, 'alice', 3)"
            ),
            {"ts": now},
        )
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_json(segment: str) -> dict[str, Any]:
    return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))


def _claims(token_type: str) -> dict[str, Any]:
    now = int(time.time())
    return {
        "exp": now + 300,
        "iat": now,
        "sub": "alice",
        "token_type": token_type,
        "scopes": JOSE_SCOPES[token_type],
        "jti": "f" * 32,
        "tv": 3,
    }


def _hs256_token(header: dict[str, Any], claims: dict[str, Any], secret: bytes) -> str:
    """Sign by hand, so no library refuses the key or the header we forge."""
    signing_input = (
        f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(claims).encode())}"
    )
    signature = hmac.new(secret, signing_input.encode("ascii"), hashlib.sha256)
    return f"{signing_input}.{_b64url(signature.digest())}"


def _rsa_private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _forge_wrong_key() -> str:
    # A known kid and HS256, signed with a secret that is not the keyring's:
    # the one forged token here that fails at the HMAC signature check.
    header = {"alg": "HS256", "kid": JOSE_KID, "typ": "JWT"}
    return _hs256_token(
        header, _claims(security.SESSION_TOKEN_TYPE), secrets.token_bytes(32)
    )


def _forge_alg_none() -> str:
    header = {"alg": "none", "kid": JOSE_KID, "typ": "JWT"}
    body = json.dumps(_claims(security.SESSION_TOKEN_TYPE)).encode()
    return f"{_b64url(json.dumps(header).encode())}.{_b64url(body)}."


def _forge_rs256() -> str:
    return jwt.encode(
        _claims(security.SESSION_TOKEN_TYPE),
        _rsa_private_key(),
        algorithm="RS256",
        headers={"kid": JOSE_KID},
    )


def _forge_list_kid() -> str:
    # A non-string kid used to reach the keyring lookup and raise TypeError
    # (an unhandled 500); it is now refused as a malformed header.
    header = {"alg": "HS256", "kid": [JOSE_KID], "typ": "JWT"}
    return _hs256_token(header, _claims(security.SESSION_TOKEN_TYPE), JOSE_KEY.encode())


def _forge_unknown_kid() -> str:
    header = {"alg": "HS256", "kid": "k_unknown", "typ": "JWT"}
    return _hs256_token(header, _claims(security.SESSION_TOKEN_TYPE), JOSE_KEY.encode())


def _forge_future_iat() -> str:
    # Correctly signed, but issued in the future: PyJWT validates iat, with
    # no leeway since the api process both issues and verifies every token.
    claims = _claims(security.SESSION_TOKEN_TYPE)
    claims["iat"] += 600
    header = {"alg": "HS256", "kid": JOSE_KID, "typ": "JWT"}
    return _hs256_token(header, claims, JOSE_KEY.encode())


FORGED_TOKENS = {
    "wrong-key": _forge_wrong_key,
    "alg-none": _forge_alg_none,
    "rs256": _forge_rs256,
    "list-kid": _forge_list_kid,
    "unknown-kid": _forge_unknown_kid,
    "future-iat": _forge_future_iat,
    "malformed": lambda: "not-a-jwt",
}


@pytest.mark.parametrize("token_type", TOKEN_TYPES)
def test_python_jose_tokens_still_verify(jose_keyring, token_type):
    decoded = security.decode_access_token(JOSE_TOKENS[token_type])

    assert decoded["sub"] == "alice"
    assert decoded["token_type"] == token_type
    assert decoded["scopes"] == JOSE_SCOPES[token_type]
    assert decoded["tv"] == 3
    assert isinstance(decoded["jti"], str) and len(decoded["jti"]) == 32
    if token_type == security.MCP_TOKEN_TYPE:
        assert {k: decoded[k] for k in MCP_EXTRA_CLAIMS} == MCP_EXTRA_CLAIMS


@pytest.mark.anyio
@pytest.mark.parametrize("token_type", TOKEN_TYPES)
async def test_python_jose_tokens_still_authenticate(
    jose_keyring, db_with_alice, token_type
):
    user, payload = await deps.get_authenticated_token_details(
        db_with_alice,
        JOSE_TOKENS[token_type],
        allowed_token_types={token_type},
        required_scopes_by_type={token_type: set(JOSE_SCOPES[token_type])},
    )

    assert user.username == "alice"
    assert payload["token_type"] == token_type


@pytest.mark.parametrize("token_type", TOKEN_TYPES)
def test_pyjwt_mints_the_python_jose_fixture_byte_for_byte(token_type):
    # Same claims, key and kid as the python-jose fixture, encoded the way
    # create_access_token encodes them: the wire format is unchanged, so
    # compatibility does not rest on PyJWT tolerating jose's output.
    header_segment, payload_segment, _ = JOSE_TOKENS[token_type].split(".")
    assert _b64url_json(header_segment)["kid"] == JOSE_KID

    reminted = jwt.encode(
        _b64url_json(payload_segment),
        JOSE_KEY,
        algorithm=security.ALGORITHM,
        headers={"kid": JOSE_KID},
    )

    assert reminted == JOSE_TOKENS[token_type]


@pytest.mark.parametrize("token_type", TOKEN_TYPES)
def test_new_tokens_round_trip_with_string_sub_and_jti(jose_keyring, token_type):
    extra = MCP_EXTRA_CLAIMS if token_type == security.MCP_TOKEN_TYPE else None
    token = security.create_access_token(
        "alice",
        token_type=token_type,
        scopes=JOSE_SCOPES[token_type],
        token_version=3,
        extra_claims=extra,
    )

    header = jwt.get_unverified_header(token)
    decoded = security.decode_access_token(token)

    assert header["alg"] == security.ALGORITHM
    assert header["kid"] == JOSE_KID
    assert isinstance(decoded["sub"], str) and decoded["sub"] == "alice"
    assert isinstance(decoded["jti"], str) and decoded["jti"]
    # utc_now() is naive UTC; the encoded iat must be the real epoch time,
    # not the naive value read in the host's local timezone.
    assert abs(decoded["iat"] - time.time()) < 5
    assert decoded["exp"] > decoded["iat"]
    if extra:
        assert {k: decoded[k] for k in extra} == extra


@pytest.mark.parametrize("forge", FORGED_TOKENS.values(), ids=FORGED_TOKENS.keys())
def test_forged_tokens_are_rejected_by_decode(jose_keyring, forge):
    with pytest.raises(jwt.InvalidTokenError):
        security.decode_access_token(forge())


@pytest.mark.anyio
@pytest.mark.parametrize("forge", FORGED_TOKENS.values(), ids=FORGED_TOKENS.keys())
async def test_forged_tokens_are_401_before_any_database_lookup(jose_keyring, forge):
    with pytest.raises(HTTPException) as excinfo:
        await deps.get_authenticated_token_details(
            None,  # no database: a rejected token must never reach one
            forge(),
            allowed_token_types={security.SESSION_TOKEN_TYPE},
        )

    assert excinfo.value.status_code == 401


def test_short_secret_key_still_verifies_but_warns(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "short-key")

    with pytest.warns(jwt.InsecureKeyLengthWarning):
        token = security.create_access_token(
            "alice",
            token_type=security.SESSION_TOKEN_TYPE,
            scopes=[security.WEB_SESSION_SCOPE],
            expires_delta=timedelta(minutes=5),
            token_version=0,
        )
    with pytest.warns(jwt.InsecureKeyLengthWarning):
        decoded = security.decode_access_token(token)

    assert decoded["sub"] == "alice"


def test_generated_keyring_keys_do_not_warn(monkeypatch, tmp_path):
    monkeypatch.delenv("SECRET_KEY", raising=False)

    class _StubPathManager:
        user_data_directory = tmp_path

    monkeypatch.setattr(security, "path_manager", _StubPathManager())

    with warnings.catch_warnings():
        warnings.simplefilter("error", jwt.InsecureKeyLengthWarning)
        token = security.create_access_token(
            "alice",
            token_type=security.SESSION_TOKEN_TYPE,
            scopes=[security.WEB_SESSION_SCOPE],
            expires_delta=timedelta(minutes=5),
            token_version=0,
        )
        assert security.decode_access_token(token)["sub"] == "alice"
