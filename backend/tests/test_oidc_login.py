"""OpenID Connect sign-in against a fake identity provider.

The provider runs on an httpx MockTransport: discovery, JWKS, token and
userinfo endpoints, with ID tokens signed by a freshly generated RSA key. The
tests drive the real routes end to end, from ``/login/oidc/start`` through the
provider's redirect to ``/login/oidc/callback``, with the state cookie the
browser would carry.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlmodel import select

from backend.core.oidc_config import reset_oidc_settings_cache
from backend.models.user import User
from backend.models.user_identity import UserIdentity
from backend.services import oidc_service
from backend.tests.sso_sharing_support import build_harness

ISSUER = "https://idp.test/application/o/nojoin/"
CLIENT_ID = "nojoin-client"
CLIENT_SECRET = "client-secret-value"
WEB = "https://localhost:14443"


@dataclass
class FakeProvider:
    key: rsa.RSAPrivateKey = field(
        default_factory=lambda: rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
    )
    kid: str = "key-1"
    subject: str = "sub-alice"
    username: str = "alice"
    email: Optional[str] = "alice@example.org"
    name: Optional[str] = "Alice Example"
    groups: Optional[list[str]] = field(default_factory=lambda: ["nojoin-users"])
    userinfo_groups: Optional[list[str]] = None
    userinfo_sub: Optional[str] = None
    claim_overrides: dict[str, Any] = field(default_factory=dict)
    header_overrides: dict[str, Any] = field(default_factory=dict)
    sign_with_secret: bool = False
    issued_codes: dict[str, dict[str, str]] = field(default_factory=dict)
    token_requests: list[dict[str, str]] = field(default_factory=list)

    def jwks(self) -> dict[str, Any]:
        public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        public.update({"kid": self.kid, "use": "sig", "alg": "RS256"})
        return {"keys": [public]}

    def authorize(self, authorization_url: str) -> tuple[str, str]:
        """Play the provider's login page: return (code, state) for the callback."""
        params = {
            k: v[0] for k, v in parse_qs(urlparse(authorization_url).query).items()
        }
        assert params["response_type"] == "code"
        assert params["client_id"] == CLIENT_ID
        assert params["code_challenge_method"] == "S256"
        assert params["redirect_uri"] == f"{WEB}/api/v1/login/oidc/callback"
        code = f"code-{len(self.issued_codes)}"
        self.issued_codes[code] = params
        return code, params["state"]

    def _id_token(self, nonce: str) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": ISSUER,
            "sub": self.subject,
            "aud": CLIENT_ID,
            "iat": now,
            "exp": now + 300,
            "nonce": nonce,
            "preferred_username": self.username,
        }
        if self.email:
            claims["email"] = self.email
        if self.name:
            claims["name"] = self.name
        if self.groups is not None:
            claims["groups"] = self.groups
        claims.update(self.claim_overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        if self.sign_with_secret:
            return jwt.encode(
                claims, CLIENT_SECRET, algorithm="HS256", headers={"kid": self.kid}
            )
        headers = {"kid": self.kid, **self.header_overrides}
        return jwt.encode(claims, self.key, algorithm="RS256", headers=headers)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": "https://idp.test/authorize",
                    "token_endpoint": "https://idp.test/token",
                    "userinfo_endpoint": "https://idp.test/userinfo",
                    "jwks_uri": "https://idp.test/jwks",
                },
            )
        if path == "/jwks":
            return httpx.Response(200, json=self.jwks())
        if path == "/token":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.token_requests.append(form)
            expected_basic = base64.b64encode(
                f"{CLIENT_ID}:{CLIENT_SECRET}".encode()
            ).decode()
            if request.headers.get("authorization") != f"Basic {expected_basic}":
                return httpx.Response(401, json={"error": "invalid_client"})
            params = self.issued_codes.pop(form.get("code", ""), None)
            if params is None:
                return httpx.Response(400, json={"error": "invalid_grant"})
            verifier = form["code_verifier"]
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            if challenge != params["code_challenge"]:
                return httpx.Response(
                    400, json={"error": "invalid_grant", "pkce": True}
                )
            return httpx.Response(
                200,
                json={
                    "access_token": "provider-access-token",
                    "token_type": "Bearer",
                    "id_token": self._id_token(params["nonce"]),
                },
            )
        if path == "/userinfo":
            info: dict[str, Any] = {"sub": self.userinfo_sub or self.subject}
            if self.userinfo_groups is not None:
                info["groups"] = self.userinfo_groups
            return httpx.Response(200, json=info)
        return httpx.Response(404)


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    fake = FakeProvider()
    monkeypatch.setenv("NOJOIN_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("NOJOIN_OIDC_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("NOJOIN_OIDC_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setenv("NOJOIN_OIDC_DISPLAY_NAME", "Authentik")
    monkeypatch.setenv("WEB_APP_URL", WEB)
    monkeypatch.delenv("NOJOIN_PASSWORD_LOGIN", raising=False)
    monkeypatch.setattr(
        oidc_service,
        "http_client_factory",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)),
    )
    reset_oidc_settings_cache()
    oidc_service.clear_caches()
    yield fake
    reset_oidc_settings_cache()
    oidc_service.clear_caches()


@pytest.fixture
async def harness():
    async for built in build_harness():
        yield built


async def _sign_in(
    client: httpx.AsyncClient, provider: FakeProvider, next_path: str = "/recordings"
):
    start = await client.get("/api/v1/login/oidc/start", params={"next": next_path})
    assert start.status_code == 303, start.text
    code, state = provider.authorize(start.headers["location"])
    return await client.get(
        "/api/v1/login/oidc/callback", params={"code": code, "state": state}
    )


async def _user(harness, **where) -> Optional[User]:
    async with harness.session_maker() as session:
        query = select(User)
        for key, value in where.items():
            query = query.where(getattr(User, key) == value)
        return (await session.execute(query)).scalar_one_or_none()


async def _linked(
    harness, subject: str = "sub-alice"
) -> Optional[tuple[User, UserIdentity]]:
    """The account linked to ``subject``, with its identity row."""
    async with harness.session_maker() as session:
        row = (
            await session.execute(
                select(User, UserIdentity)
                .join(UserIdentity, UserIdentity.user_id == User.id)
                .where(UserIdentity.subject == subject)
            )
        ).first()
        return (row[0], row[1]) if row else None


def _error_of(response: httpx.Response) -> Optional[str]:
    location = response.headers.get("location", "")
    values = parse_qs(urlparse(location).query).get("error")
    return values[0] if values else None


@pytest.mark.anyio
async def test_login_methods_describe_sso(harness, provider) -> None:
    async with harness.client() as client:
        body = (await client.get("/api/v1/login/methods")).json()
    assert body["oidc"] == {"enabled": True, "display_name": "Authentik"}
    assert body["password"] is True


@pytest.mark.anyio
async def test_first_sign_in_creates_account_and_session(harness, provider) -> None:
    async with harness.client() as client:
        response = await _sign_in(client, provider, "/recordings/abc")
        assert response.status_code == 303
        assert response.headers["location"] == "/recordings/abc"
        assert "access_token" in client.cookies
        me = await client.get("/api/v1/users/me", headers={"Origin": WEB})
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["username"] == "alice"
    assert body["auth_source"] == "oidc"
    assert body["display_name"] == "Alice Example"
    assert body["oidc_groups"] == ["nojoin-users"]
    assert body["oidc_linked"] is True
    assert body["role"] == "user"
    # PKCE: the verifier sent to the token endpoint matched the challenge.
    assert provider.token_requests and provider.token_requests[0]["code_verifier"]


@pytest.mark.anyio
async def test_second_sign_in_reuses_the_account_and_refreshes_groups(
    harness, provider
) -> None:
    async with harness.client() as client:
        await _sign_in(client, provider)
    provider.groups = ["nojoin-users", "research"]
    provider.username = "renamed-at-provider"
    async with harness.client() as client:
        await _sign_in(client, provider)
    user, identity = await _linked(harness)
    assert user.username == "alice"
    assert identity.groups == ["nojoin-users", "research"]


@pytest.mark.anyio
async def test_never_attaches_to_a_local_account_with_the_same_name(
    harness, provider
) -> None:
    local = await harness.add_user("alice", role="owner")
    async with harness.client() as client:
        await _sign_in(client, provider)
    created, _ = await _linked(harness)
    assert created.id != local.id
    assert created.username == "alice-2"
    async with harness.session_maker() as session:
        links = (
            await session.execute(
                select(UserIdentity).where(UserIdentity.user_id == local.id)
            )
        ).all()
    assert links == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"claim_overrides": {"aud": "someone-else"}}, "invalid_token"),
        ({"claim_overrides": {"iss": "https://evil.test/"}}, "invalid_token"),
        ({"claim_overrides": {"exp": int(time.time()) - 3600}}, "invalid_token"),
        ({"claim_overrides": {"nonce": "replayed"}}, "invalid_token"),
        (
            {"claim_overrides": {"aud": [CLIENT_ID, "other"], "azp": "other"}},
            "invalid_token",
        ),
        ({"sign_with_secret": True}, "invalid_token"),
        ({"header_overrides": {"kid": "unknown-key"}}, "invalid_token"),
    ],
)
async def test_rejects_bad_id_tokens(harness, provider, change, expected) -> None:
    for key, value in change.items():
        setattr(provider, key, value)
    async with harness.client() as client:
        response = await _sign_in(client, provider)
        assert "access_token" not in client.cookies
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login?")
    assert _error_of(response) == expected
    assert await _linked(harness) is None


@pytest.mark.anyio
async def test_rejects_unsigned_tokens(harness, provider, monkeypatch) -> None:
    def unsigned(nonce: str) -> str:
        header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=")
        now = int(time.time())
        payload = base64.urlsafe_b64encode(
            json.dumps(
                {
                    "iss": ISSUER,
                    "sub": "x",
                    "aud": CLIENT_ID,
                    "iat": now,
                    "exp": now + 60,
                    "nonce": nonce,
                }
            ).encode()
        ).rstrip(b"=")
        return (header + b"." + payload + b".").decode()

    monkeypatch.setattr(provider, "_id_token", unsigned)
    async with harness.client() as client:
        response = await _sign_in(client, provider)
    assert _error_of(response) == "invalid_token"


@pytest.mark.anyio
async def test_state_must_match_the_browser_cookie(harness, provider) -> None:
    async with harness.client() as attacker:
        start = await attacker.get("/api/v1/login/oidc/start")
        code, state = provider.authorize(start.headers["location"])
    # The victim's browser has no state cookie (or another one): the attacker's
    # code cannot be planted in it.
    async with harness.client() as victim:
        await victim.get("/api/v1/login/oidc/start")
        response = await victim.get(
            "/api/v1/login/oidc/callback", params={"code": code, "state": state}
        )
        assert "access_token" not in victim.cookies
    assert _error_of(response) == "sso_state"


@pytest.mark.anyio
async def test_next_cannot_leave_the_site(harness, provider) -> None:
    for unsafe in ("//evil.example/x", "https://evil.example", "/\\evil.example"):
        async with harness.client() as client:
            response = await _sign_in(client, provider, unsafe)
        assert response.headers["location"] == "/"


@pytest.mark.anyio
async def test_allowed_groups_gate_sign_in(harness, provider, monkeypatch) -> None:
    monkeypatch.setenv("NOJOIN_OIDC_ALLOWED_GROUPS", "nojoin-users")
    reset_oidc_settings_cache()
    provider.groups = ["staff"]
    async with harness.client() as client:
        response = await _sign_in(client, provider)
    assert _error_of(response) == "sso_not_allowed"
    assert await _linked(harness) is None


@pytest.mark.anyio
async def test_groups_fall_back_to_userinfo(harness, provider) -> None:
    provider.groups = None
    provider.userinfo_groups = ["from-userinfo"]
    async with harness.client() as client:
        await _sign_in(client, provider)
    _, identity = await _linked(harness)
    assert identity.groups == ["from-userinfo"]


@pytest.mark.anyio
async def test_userinfo_with_another_subject_is_refused(harness, provider) -> None:
    provider.groups = None
    provider.userinfo_groups = ["admins"]
    provider.userinfo_sub = "someone-else"
    async with harness.client() as client:
        response = await _sign_in(client, provider)
    assert _error_of(response) == "invalid_token"


@pytest.mark.anyio
async def test_auto_create_off_requires_a_linked_account(
    harness, provider, monkeypatch
) -> None:
    monkeypatch.setenv("NOJOIN_OIDC_AUTO_CREATE", "false")
    reset_oidc_settings_cache()
    async with harness.client() as client:
        response = await _sign_in(client, provider)
    assert _error_of(response) == "sso_no_account"


@pytest.mark.anyio
async def test_admin_group_grants_and_revokes_admin(
    harness, provider, monkeypatch
) -> None:
    monkeypatch.setenv("NOJOIN_OIDC_ADMIN_GROUPS", "nojoin-admins")
    reset_oidc_settings_cache()
    provider.groups = ["nojoin-admins"]
    async with harness.client() as client:
        await _sign_in(client, provider)
    admin, _ = await _linked(harness)
    assert admin.role == "admin"

    provider.groups = ["nojoin-users"]
    async with harness.client() as client:
        await _sign_in(client, provider)
    demoted, _ = await _linked(harness)
    assert demoted.role == "user"
    # Sessions minted while the user was an admin stop working.
    assert demoted.token_version > admin.token_version


@pytest.mark.anyio
async def test_owner_role_is_never_changed_by_groups(
    harness, provider, monkeypatch
) -> None:
    monkeypatch.setenv("NOJOIN_OIDC_ADMIN_GROUPS", "nojoin-admins")
    reset_oidc_settings_cache()
    owner = await harness.add_user("job", role="owner", is_superuser=True)
    async with harness.client(owner) as client:
        linked = await client.post("/api/v1/login/oidc/link")
        assert linked.status_code == 200, linked.text
        url = linked.json()["authorization_url"]
        assert "prompt=login" in url
        code, state = provider.authorize(url)
        response = await client.get(
            "/api/v1/login/oidc/callback", params={"code": code, "state": state}
        )
    assert response.headers["location"] == "/settings/profile?sso_linked=1"
    after, identity = await _linked(harness)
    assert after.id == owner.id
    assert after.role == "owner"
    assert identity.created_account is False

    # Signing in through the provider now lands on the same account.
    async with harness.client() as client:
        await _sign_in(client, provider)
        me = await client.get("/api/v1/users/me", headers={"Origin": WEB})
    assert me.json()["id"] == owner.id


@pytest.mark.anyio
async def test_cannot_link_an_identity_already_used_elsewhere(
    harness, provider
) -> None:
    async with harness.client() as client:
        await _sign_in(client, provider)
    other = await harness.add_user("bob")
    async with harness.client(other) as client:
        url = (await client.post("/api/v1/login/oidc/link")).json()["authorization_url"]
        code, state = provider.authorize(url)
        response = await client.get(
            "/api/v1/login/oidc/callback", params={"code": code, "state": state}
        )
    assert "sso_error=sso_identity_in_use" in response.headers["location"]
    linked_user, _ = await _linked(harness)
    assert linked_user.id != other.id


@pytest.mark.anyio
async def test_link_start_needs_a_signed_in_user(harness, provider) -> None:
    async with harness.client() as client:
        response = await client.post("/api/v1/login/oidc/link")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_routes_404_when_sso_is_not_configured(harness, monkeypatch) -> None:
    monkeypatch.delenv("NOJOIN_OIDC_ISSUER", raising=False)
    monkeypatch.delenv("NOJOIN_OIDC_CLIENT_ID", raising=False)
    monkeypatch.delenv("NOJOIN_OIDC_CLIENT_SECRET", raising=False)
    reset_oidc_settings_cache()
    async with harness.client() as client:
        assert (await client.get("/api/v1/login/oidc/start")).status_code == 404
        methods = (await client.get("/api/v1/login/methods")).json()
    assert methods["oidc"]["enabled"] is False
