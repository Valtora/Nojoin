"""OpenID Connect authorization-code flow with PKCE, as a relying party.

Built on httpx and PyJWT only. The steps:

1. :func:`begin_login` makes ``state``, ``nonce`` and a PKCE verifier, and
   returns the provider's authorization URL plus a signed state token. The
   endpoint stores the token in a short-lived HttpOnly cookie, which ties the
   flow to the browser that started it.
2. :func:`complete_login` checks the returned ``state`` against that token,
   exchanges the code (sending the verifier), and verifies the ID token:
   signature against the provider's JWKS (asymmetric algorithms only),
   ``iss``, ``aud``/``azp``, ``exp``/``iat`` and ``nonce``. Groups missing from
   the ID token are read from the userinfo endpoint, whose ``sub`` must match.

Discovery documents and key sets are cached in memory for an hour; a token
signed with an unknown ``kid`` triggers one refetch, which covers key rotation.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable, Optional
from urllib.parse import urlencode

import httpx
import jwt

from backend.core import security
from backend.core.oidc_config import OIDCSettings

logger = logging.getLogger(__name__)

STATE_TOKEN_TYPE = "oidc_state"
STATE_TOKEN_LIFETIME = timedelta(minutes=10)
METADATA_TTL_SECONDS = 60 * 60
HTTP_TIMEOUT_SECONDS = 10.0
CLOCK_SKEW_SECONDS = 60
ALLOWED_ID_TOKEN_ALGORITHMS = [
    "RS256",
    "RS384",
    "RS512",
    "PS256",
    "PS384",
    "PS512",
    "ES256",
    "ES384",
    "ES512",
    "EdDSA",
]


class OIDCError(Exception):
    """The sign-in failed. ``code`` is a short machine-readable reason that the
    login page turns into a message; the exception text is for the log only."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LoginStart:
    authorization_url: str
    state_token: str


@dataclass(frozen=True)
class PendingLogin:
    """What the state cookie carries between the redirect and the callback."""

    state: str
    nonce: str
    code_verifier: str
    next_path: str
    mode: str
    link_user_id: Optional[int]


def _default_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=False)


# Tests replace this with a client on an httpx.MockTransport.
http_client_factory: Callable[[], httpx.AsyncClient] = _default_http_client

_metadata_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_jwks_cache: dict[str, tuple[float, jwt.PyJWKSet]] = {}


def clear_caches() -> None:
    _metadata_cache.clear()
    _jwks_cache.clear()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def pkce_challenge(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def safe_next_path(value: Optional[str]) -> str:
    """A same-origin path to return to after sign-in, or ``/``.

    Accepts only an absolute path: it must start with one ``/`` and contain no
    backslash or control character, so ``//evil.example``, ``/\\evil.example``
    and ``https://evil.example`` all fall back to ``/``.
    """
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    if "\\" in value or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        return "/"
    return value[:2048]


async def _get_json(url: str, *, headers: Optional[dict[str, str]] = None) -> Any:
    async with http_client_factory() as client:
        try:
            response = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise OIDCError("provider_unreachable", f"GET {url} failed: {exc}") from exc
    if response.status_code != 200:
        raise OIDCError(
            "provider_error", f"GET {url} returned HTTP {response.status_code}"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise OIDCError("provider_error", f"GET {url} did not return JSON") from exc


async def get_provider_metadata(settings: OIDCSettings) -> dict[str, Any]:
    cached = _metadata_cache.get(settings.issuer)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    metadata = await _get_json(settings.discovery_url)
    if not isinstance(metadata, dict):
        raise OIDCError("provider_error", "Discovery document is not an object")
    # OIDC Discovery 1.0 section 4.3: the issuer in the document must be exactly
    # the configured one, or tokens from another tenant could pass the iss check.
    if metadata.get("issuer") != settings.issuer:
        raise OIDCError(
            "provider_error",
            f"Discovery issuer {metadata.get('issuer')!r} does not match "
            f"configured issuer {settings.issuer!r}",
        )
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not isinstance(metadata.get(key), str):
            raise OIDCError("provider_error", f"Discovery document has no {key}")
    _metadata_cache[settings.issuer] = (
        time.monotonic() + METADATA_TTL_SECONDS,
        metadata,
    )
    return metadata


async def _get_jwks(jwks_uri: str, *, refresh: bool = False) -> jwt.PyJWKSet:
    cached = _jwks_cache.get(jwks_uri)
    if cached and not refresh and cached[0] > time.monotonic():
        return cached[1]
    data = await _get_json(jwks_uri)
    try:
        key_set = jwt.PyJWKSet.from_dict(data)
    except jwt.PyJWTError as exc:
        raise OIDCError("provider_error", f"Unusable JWKS at {jwks_uri}") from exc
    _jwks_cache[jwks_uri] = (time.monotonic() + METADATA_TTL_SECONDS, key_set)
    return key_set


async def _signing_key_for(id_token: str, jwks_uri: str) -> jwt.PyJWK:
    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError as exc:
        raise OIDCError("invalid_token", "Malformed ID token") from exc
    if header.get("alg") not in ALLOWED_ID_TOKEN_ALGORITHMS:
        raise OIDCError(
            "invalid_token", f"ID token algorithm {header.get('alg')!r} not allowed"
        )
    kid = header.get("kid")
    for refresh in (False, True):
        key_set = await _get_jwks(jwks_uri, refresh=refresh)
        candidates = [key for key in key_set.keys if kid is None or key.key_id == kid]
        if len(candidates) == 1:
            return candidates[0]
        if kid is None and len(candidates) > 1:
            raise OIDCError(
                "invalid_token", "ID token has no kid and the JWKS has several keys"
            )
    raise OIDCError("invalid_token", f"No JWKS key matches kid {kid!r}")


def _make_state_token(pending: PendingLogin) -> str:
    claims: dict[str, Any] = {
        "nonce": pending.nonce,
        "cv": pending.code_verifier,
        "next": pending.next_path,
        "mode": pending.mode,
    }
    if pending.link_user_id is not None:
        claims["link_uid"] = pending.link_user_id
    return security.create_access_token(
        pending.state,
        token_type=STATE_TOKEN_TYPE,
        expires_delta=STATE_TOKEN_LIFETIME,
        extra_claims=claims,
    )


def read_state_token(
    token: Optional[str], returned_state: Optional[str]
) -> PendingLogin:
    if not token or not returned_state:
        raise OIDCError("sso_state", "Missing state cookie or state parameter")
    try:
        payload = security.decode_access_token(token)
    except jwt.PyJWTError as exc:
        raise OIDCError("sso_state", "State cookie failed verification") from exc
    if payload.get("token_type") != STATE_TOKEN_TYPE:
        raise OIDCError("sso_state", "State cookie is not a state token")
    if not secrets.compare_digest(str(payload.get("sub", "")), returned_state):
        raise OIDCError("sso_state", "State parameter does not match the cookie")
    mode = payload.get("mode")
    if mode not in ("login", "link"):
        raise OIDCError("sso_state", "Unknown sign-in mode")
    link_uid = payload.get("link_uid")
    return PendingLogin(
        state=returned_state,
        nonce=str(payload["nonce"]),
        code_verifier=str(payload["cv"]),
        next_path=safe_next_path(payload.get("next")),
        mode=mode,
        link_user_id=link_uid if isinstance(link_uid, int) else None,
    )


async def begin_login(
    settings: OIDCSettings,
    *,
    next_path: Optional[str],
    mode: str = "login",
    link_user_id: Optional[int] = None,
) -> LoginStart:
    metadata = await get_provider_metadata(settings)
    pending = PendingLogin(
        state=secrets.token_urlsafe(32),
        nonce=secrets.token_urlsafe(32),
        code_verifier=secrets.token_urlsafe(64),
        next_path=safe_next_path(next_path),
        mode=mode,
        link_user_id=link_user_id,
    )
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.client_id,
            "redirect_uri": settings.redirect_uri,
            "scope": " ".join(settings.scopes),
            "state": pending.state,
            "nonce": pending.nonce,
            "code_challenge": pkce_challenge(pending.code_verifier),
            "code_challenge_method": "S256",
        }
    )
    endpoint = metadata["authorization_endpoint"]
    separator = "&" if "?" in endpoint else "?"
    return LoginStart(
        authorization_url=f"{endpoint}{separator}{query}",
        state_token=_make_state_token(pending),
    )


async def _exchange_code(
    settings: OIDCSettings, metadata: dict[str, Any], code: str, code_verifier: str
) -> dict[str, Any]:
    async with http_client_factory() as client:
        try:
            response = await client.post(
                metadata["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": settings.redirect_uri,
                    "code_verifier": code_verifier,
                },
                auth=(settings.client_id, settings.client_secret),
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise OIDCError(
                "provider_unreachable", f"Token request failed: {exc}"
            ) from exc
    if response.status_code != 200:
        raise OIDCError(
            "token_exchange_failed",
            f"Token endpoint returned HTTP {response.status_code}",
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise OIDCError("token_exchange_failed", "Token response is not JSON") from exc
    if not isinstance(body, dict) or not isinstance(body.get("id_token"), str):
        raise OIDCError("token_exchange_failed", "Token response has no id_token")
    return body


async def _verify_id_token(
    settings: OIDCSettings, metadata: dict[str, Any], id_token: str, nonce: str
) -> dict[str, Any]:
    signing_key = await _signing_key_for(id_token, metadata["jwks_uri"])
    try:
        claims = jwt.decode(
            id_token,
            signing_key,
            algorithms=ALLOWED_ID_TOKEN_ALGORITHMS,
            audience=settings.client_id,
            issuer=settings.issuer,
            leeway=CLOCK_SKEW_SECONDS,
            options={"require": ["iss", "sub", "aud", "exp", "iat"]},
        )
    except jwt.PyJWTError as exc:
        raise OIDCError("invalid_token", f"ID token rejected: {exc}") from exc
    audience = claims.get("aud")
    if isinstance(audience, list) and len(audience) > 1:
        if claims.get("azp") != settings.client_id:
            raise OIDCError("invalid_token", "ID token azp does not name this client")
    elif "azp" in claims and claims["azp"] != settings.client_id:
        raise OIDCError("invalid_token", "ID token azp does not name this client")
    token_nonce = claims.get("nonce")
    if not isinstance(token_nonce, str) or not secrets.compare_digest(
        token_nonce, nonce
    ):
        raise OIDCError("invalid_token", "ID token nonce does not match")
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise OIDCError("invalid_token", "ID token has no subject")
    return claims


async def _userinfo(
    metadata: dict[str, Any], access_token: str, subject: str
) -> dict[str, Any]:
    endpoint = metadata.get("userinfo_endpoint")
    if not isinstance(endpoint, str):
        return {}
    info = await _get_json(
        endpoint, headers={"Authorization": f"Bearer {access_token}"}
    )
    if not isinstance(info, dict):
        return {}
    # OIDC Core 5.3.2: userinfo is only trustworthy when its sub matches.
    if info.get("sub") != subject:
        raise OIDCError("invalid_token", "Userinfo subject does not match the ID token")
    return info


@dataclass(frozen=True)
class VerifiedIdentity:
    issuer: str
    subject: str
    username: Optional[str]
    email: Optional[str]
    display_name: Optional[str]
    groups: tuple[str, ...]


def _claim_groups(value: Any) -> Optional[tuple[str, ...]]:
    if value is None:
        return None
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return None
    groups = sorted(
        {item.strip() for item in value if isinstance(item, str) and item.strip()}
    )
    return tuple(groups)


def _str_claim(claims: dict[str, Any], name: str) -> Optional[str]:
    value = claims.get(name)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


async def complete_login(
    settings: OIDCSettings, pending: PendingLogin, code: Optional[str]
) -> VerifiedIdentity:
    if not code:
        raise OIDCError("sso_cancelled", "Callback carried no authorization code")
    metadata = await get_provider_metadata(settings)
    tokens = await _exchange_code(settings, metadata, code, pending.code_verifier)
    claims = await _verify_id_token(
        settings, metadata, tokens["id_token"], pending.nonce
    )

    groups = _claim_groups(claims.get(settings.groups_claim))
    needs_userinfo = groups is None or any(
        _str_claim(claims, name) is None
        for name in (settings.username_claim, "email", "name")
    )
    access_token = tokens.get("access_token")
    if needs_userinfo and isinstance(access_token, str) and access_token:
        info = await _userinfo(metadata, access_token, claims["sub"])
        merged = {**info, **{k: v for k, v in claims.items() if v not in (None, "")}}
        if groups is None:
            groups = _claim_groups(info.get(settings.groups_claim))
        claims = merged

    return VerifiedIdentity(
        issuer=settings.issuer,
        subject=claims["sub"],
        username=_str_claim(claims, settings.username_claim),
        email=_str_claim(claims, "email"),
        display_name=_str_claim(claims, "name"),
        groups=groups or (),
    )
