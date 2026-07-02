"""OAuth 2.1 authorization-server logic for the Nojoin MCP connector.

Implements the pieces required for spec-compliant MCP clients (claude.ai
custom connectors, Claude Code) to connect with only the server URL:

- Dynamic Client Registration (RFC 7591) for public clients.
- PKCE-bound (S256) single-use authorization codes.
- Access-token issuance as short-lived ``mcp``-type JWTs on the existing
  signing keyring, so ``token_version`` bumps and the ``jti`` denylist
  revoke connector access exactly like sessions.
- Rotating opaque refresh tokens grouped into grants. Reuse of a rotated
  refresh token revokes the whole grant family (RFC 9700 guidance).

Plaintext codes and refresh tokens are never stored; only SHA-256 hashes.
"""

import hashlib
import json
import logging
import secrets
import uuid
from base64 import urlsafe_b64encode
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Optional
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.core import security
from backend.models.oauth import (
    OAuthAuthorizationCode,
    OAuthClient,
    OAuthRefreshToken,
)
from backend.models.user import User
from backend.utils.config_manager import get_trusted_web_origin
from backend.utils.time import utc_now

logger = logging.getLogger(__name__)

AUTHORIZATION_CODE_TTL_SECONDS = 60
REFRESH_TOKEN_TTL_DAYS = 180
MAX_REDIRECT_URIS = 8
SUPPORTED_SCOPES = {security.MCP_READ_SCOPE}
DEFAULT_SCOPE = security.MCP_READ_SCOPE

_LOOPBACK_HOSTNAMES = {"localhost", "127.0.0.1", "::1"}


class OAuthError(Exception):
    """Protocol-level OAuth error mapped to an RFC 6749 error response."""

    def __init__(self, error: str, description: str, status_code: int = 400):
        super().__init__(description)
        self.error = error
        self.description = description
        self.status_code = status_code


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _new_opaque_secret() -> str:
    return secrets.token_urlsafe(48)


def canonical_origin() -> str:
    return get_trusted_web_origin().rstrip("/")


def mcp_resource_url() -> str:
    return f"{canonical_origin()}/mcp"


def build_protected_resource_metadata() -> dict[str, Any]:
    return {
        "resource": mcp_resource_url(),
        "authorization_servers": [canonical_origin()],
        "scopes_supported": sorted(SUPPORTED_SCOPES),
        "bearer_methods_supported": ["header"],
    }


def build_authorization_server_metadata() -> dict[str, Any]:
    origin = canonical_origin()
    return {
        "issuer": origin,
        "authorization_endpoint": f"{origin}/oauth/authorize",
        "token_endpoint": f"{origin}/api/v1/oauth/token",
        "registration_endpoint": f"{origin}/api/v1/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": sorted(SUPPORTED_SCOPES),
    }


def _validate_redirect_uri_shape(uri: str) -> None:
    parsed = urlparse(uri)
    if parsed.scheme == "https" and parsed.hostname:
        return
    # OAuth 2.1 permits plain HTTP only for loopback redirect URIs
    # (native clients binding an ephemeral local port).
    if (
        parsed.scheme == "http"
        and parsed.hostname
        and parsed.hostname.lower() in _LOOPBACK_HOSTNAMES
    ):
        return
    raise OAuthError(
        "invalid_client_metadata",
        f"redirect_uri must be HTTPS or a loopback HTTP URI: {uri}",
    )


def normalise_scope(scope: Optional[str]) -> str:
    if not scope or not scope.strip():
        return DEFAULT_SCOPE
    requested = set(scope.split())
    unsupported = requested - SUPPORTED_SCOPES
    if unsupported:
        raise OAuthError(
            "invalid_scope",
            f"Unsupported scope(s): {' '.join(sorted(unsupported))}",
        )
    return " ".join(sorted(requested))


async def register_client(db: AsyncSession, metadata: dict[str, Any]) -> dict[str, Any]:
    """Dynamic Client Registration (RFC 7591) for public PKCE clients."""
    redirect_uris = metadata.get("redirect_uris")
    if not isinstance(redirect_uris, list) or not redirect_uris:
        raise OAuthError(
            "invalid_client_metadata", "redirect_uris is required and must be a list."
        )
    if len(redirect_uris) > MAX_REDIRECT_URIS:
        raise OAuthError(
            "invalid_client_metadata",
            f"At most {MAX_REDIRECT_URIS} redirect_uris are allowed.",
        )
    normalised_uris: list[str] = []
    for uri in redirect_uris:
        if not isinstance(uri, str) or not uri.strip():
            raise OAuthError(
                "invalid_client_metadata", "redirect_uris entries must be strings."
            )
        cleaned = uri.strip()
        _validate_redirect_uri_shape(cleaned)
        normalised_uris.append(cleaned)

    auth_method = metadata.get("token_endpoint_auth_method", "none")
    if auth_method != "none":
        raise OAuthError(
            "invalid_client_metadata",
            "Only public clients (token_endpoint_auth_method 'none') are supported.",
        )

    grant_types = metadata.get("grant_types") or ["authorization_code"]
    allowed_grants = {"authorization_code", "refresh_token"}
    if not set(grant_types).issubset(allowed_grants):
        raise OAuthError(
            "invalid_client_metadata",
            "Only authorization_code and refresh_token grant types are supported.",
        )

    response_types = metadata.get("response_types") or ["code"]
    if set(response_types) != {"code"}:
        raise OAuthError(
            "invalid_client_metadata", "Only the 'code' response type is supported."
        )

    client_name = metadata.get("client_name")
    if client_name is not None:
        if not isinstance(client_name, str):
            raise OAuthError("invalid_client_metadata", "client_name must be a string.")
        client_name = client_name.strip()[:256] or None

    client = OAuthClient(
        client_id=uuid.uuid4().hex,
        client_name=client_name,
        redirect_uris=json.dumps(normalised_uris),
        token_endpoint_auth_method="none",
    )
    db.add(client)
    await db.commit()

    logger.info(
        "Registered OAuth client %s (%s) with %d redirect URI(s).",
        client.client_id,
        client_name or "unnamed",
        len(normalised_uris),
    )
    return {
        "client_id": client.client_id,
        "client_name": client_name,
        "redirect_uris": normalised_uris,
        "token_endpoint_auth_method": "none",
        "grant_types": sorted(set(grant_types)),
        "response_types": ["code"],
        "client_id_issued_at": int(client.created_at.timestamp()),
    }


async def get_client(db: AsyncSession, client_id: str) -> Optional[OAuthClient]:
    if not client_id:
        return None
    result = await db.execute(
        select(OAuthClient).where(OAuthClient.client_id == client_id)
    )
    return result.scalar_one_or_none()


def client_redirect_uris(client: OAuthClient) -> list[str]:
    try:
        uris = json.loads(client.redirect_uris)
    except (TypeError, json.JSONDecodeError):
        return []
    return [uri for uri in uris if isinstance(uri, str)]


@dataclass(frozen=True)
class AuthorizationRequest:
    """The client-supplied parameters of an /authorize request."""

    client: OAuthClient
    redirect_uri: str
    response_type: str = "code"
    scope: Optional[str] = None
    code_challenge: Optional[str] = None
    code_challenge_method: Optional[str] = None
    resource: Optional[str] = None


def validate_authorization_request(request: AuthorizationRequest) -> str:
    """Validate an /authorize request. Returns the normalised scope."""
    if request.redirect_uri not in client_redirect_uris(request.client):
        raise OAuthError(
            "invalid_request", "redirect_uri is not registered for this client."
        )
    if request.response_type != "code":
        raise OAuthError(
            "unsupported_response_type", "Only response_type=code is supported."
        )
    if not request.code_challenge:
        raise OAuthError("invalid_request", "code_challenge is required (PKCE).")
    if (request.code_challenge_method or "S256") != "S256":
        raise OAuthError(
            "invalid_request", "Only the S256 code_challenge_method is supported."
        )
    return normalise_scope(request.scope)


async def create_authorization_code(
    db: AsyncSession,
    *,
    request: AuthorizationRequest,
    user: User,
    scope: str,
) -> str:
    code = _new_opaque_secret()
    record = OAuthAuthorizationCode(
        code_hash=_hash_secret(code),
        client_id=request.client.client_id,
        user_id=user.id,
        redirect_uri=request.redirect_uri,
        scope=scope,
        code_challenge=request.code_challenge,
        code_challenge_method="S256",
        resource=request.resource,
        expires_at=utc_now() + timedelta(seconds=AUTHORIZATION_CODE_TTL_SECONDS),
    )
    db.add(record)
    await db.commit()
    return code


def _verify_pkce(code_challenge: str, code_verifier: str) -> bool:
    if not (43 <= len(code_verifier) <= 128):
        return False
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(computed, code_challenge)


def _build_access_token(user: User, *, client_id: str, scope: str) -> tuple[str, int]:
    expires_in = security.MCP_TOKEN_EXPIRE_MINUTES * 60
    token = security.create_access_token(
        user.username,
        token_type=security.MCP_TOKEN_TYPE,
        scopes=scope.split(),
        token_version=user.token_version,
        extra_claims={
            "client_id": client_id,
            # Custom claim (not "aud": python-jose rejects tokens carrying
            # "aud" unless every decode call passes an audience option).
            "res": mcp_resource_url(),
        },
    )
    return token, expires_in


@dataclass(frozen=True)
class RefreshGrant:
    """Identity of a consent grant, shared by every rotation of its tokens."""

    grant_id: str
    client_id: str
    user_id: int
    scope: str
    resource: Optional[str]


async def _issue_refresh_token(db: AsyncSession, grant: RefreshGrant) -> str:
    refresh_token = _new_opaque_secret()
    db.add(
        OAuthRefreshToken(
            token_hash=_hash_secret(refresh_token),
            grant_id=grant.grant_id,
            client_id=grant.client_id,
            user_id=grant.user_id,
            scope=grant.scope,
            resource=grant.resource,
            expires_at=utc_now() + timedelta(days=REFRESH_TOKEN_TTL_DAYS),
        )
    )
    return refresh_token


async def _load_user(db: AsyncSession, user_id: int) -> User:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise OAuthError("invalid_grant", "The user account is not available.", 400)
    return user


async def exchange_authorization_code(
    db: AsyncSession,
    *,
    code: str,
    client_id: str,
    redirect_uri: str,
    code_verifier: str,
) -> dict[str, Any]:
    client = await get_client(db, client_id)
    if client is None:
        raise OAuthError("invalid_client", "Unknown client.", 401)

    result = await db.execute(
        select(OAuthAuthorizationCode).where(
            OAuthAuthorizationCode.code_hash == _hash_secret(code)
        )
    )
    record = result.scalar_one_or_none()
    if record is None or record.client_id != client_id:
        raise OAuthError("invalid_grant", "Unknown or expired authorization code.")
    if record.used_at is not None:
        # Single-use enforcement: a replayed code is a strong signal of
        # interception, so drop the code outright.
        await db.delete(record)
        await db.commit()
        raise OAuthError("invalid_grant", "Authorization code already used.")
    if record.expires_at <= utc_now().replace(tzinfo=None):
        raise OAuthError("invalid_grant", "Authorization code has expired.")
    if record.redirect_uri != redirect_uri:
        raise OAuthError("invalid_grant", "redirect_uri does not match.")
    if not code_verifier or not _verify_pkce(record.code_challenge, code_verifier):
        raise OAuthError("invalid_grant", "PKCE verification failed.")

    record.used_at = utc_now()
    user = await _load_user(db, record.user_id)

    refresh_token = await _issue_refresh_token(
        db,
        RefreshGrant(
            grant_id=uuid.uuid4().hex,
            client_id=client_id,
            user_id=user.id,
            scope=record.scope,
            resource=record.resource,
        ),
    )
    client.last_used_at = utc_now()
    await db.commit()

    access_token, expires_in = _build_access_token(
        user, client_id=client_id, scope=record.scope
    )
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "refresh_token": refresh_token,
        "scope": record.scope,
    }


async def refresh_access_token(
    db: AsyncSession,
    *,
    refresh_token: str,
    client_id: str,
) -> dict[str, Any]:
    client = await get_client(db, client_id)
    if client is None:
        raise OAuthError("invalid_client", "Unknown client.", 401)

    result = await db.execute(
        select(OAuthRefreshToken).where(
            OAuthRefreshToken.token_hash == _hash_secret(refresh_token)
        )
    )
    record = result.scalar_one_or_none()
    if record is None or record.client_id != client_id:
        raise OAuthError("invalid_grant", "Unknown refresh token.")

    now_naive = utc_now().replace(tzinfo=None)
    if record.revoked_at is not None:
        # Rotated-token reuse: revoke the entire grant family (RFC 9700).
        await revoke_grant(db, grant_id=record.grant_id)
        logger.warning(
            "Refresh token reuse detected for grant %s (client %s); grant revoked.",
            record.grant_id,
            client_id,
        )
        raise OAuthError("invalid_grant", "Refresh token has been revoked.")
    if record.expires_at <= now_naive:
        raise OAuthError("invalid_grant", "Refresh token has expired.")

    user = await _load_user(db, record.user_id)

    record.revoked_at = utc_now()
    record.last_used_at = utc_now()
    new_refresh_token = await _issue_refresh_token(
        db,
        RefreshGrant(
            grant_id=record.grant_id,
            client_id=client_id,
            user_id=user.id,
            scope=record.scope,
            resource=record.resource,
        ),
    )
    client.last_used_at = utc_now()
    await db.commit()

    access_token, expires_in = _build_access_token(
        user, client_id=client_id, scope=record.scope
    )
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "refresh_token": new_refresh_token,
        "scope": record.scope,
    }


async def revoke_grant(db: AsyncSession, *, grant_id: str) -> int:
    result = await db.execute(
        select(OAuthRefreshToken).where(OAuthRefreshToken.grant_id == grant_id)
    )
    revoked = 0
    for token in result.scalars():
        if token.revoked_at is None:
            token.revoked_at = utc_now()
            revoked += 1
    await db.commit()
    return revoked


async def list_active_grants(db: AsyncSession, *, user_id: int) -> list[dict[str, Any]]:
    """Active consent grants for the user's Connected Apps settings view."""
    now_naive = utc_now().replace(tzinfo=None)
    result = await db.execute(
        select(OAuthRefreshToken, OAuthClient)
        .join(OAuthClient, OAuthClient.client_id == OAuthRefreshToken.client_id)
        .where(OAuthRefreshToken.user_id == user_id)
        .where(OAuthRefreshToken.revoked_at.is_(None))  # type: ignore[union-attr]
        .where(OAuthRefreshToken.expires_at > now_naive)
    )
    grants: dict[str, dict[str, Any]] = {}
    for token, client in result.all():
        entry = grants.setdefault(
            token.grant_id,
            {
                "grant_id": token.grant_id,
                "client_name": client.client_name or "Unnamed client",
                "scope": token.scope,
                "created_at": token.created_at,
                "last_used_at": token.last_used_at,
            },
        )
        if token.created_at < entry["created_at"]:
            entry["created_at"] = token.created_at
        if token.last_used_at and (
            entry["last_used_at"] is None or token.last_used_at > entry["last_used_at"]
        ):
            entry["last_used_at"] = token.last_used_at
    return sorted(grants.values(), key=lambda g: g["created_at"])


async def revoke_grant_for_user(
    db: AsyncSession, *, grant_id: str, user_id: int
) -> bool:
    result = await db.execute(
        select(OAuthRefreshToken)
        .where(OAuthRefreshToken.grant_id == grant_id)
        .where(OAuthRefreshToken.user_id == user_id)
        .limit(1)
    )
    if result.scalar_one_or_none() is None:
        return False
    await revoke_grant(db, grant_id=grant_id)
    return True
