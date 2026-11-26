"""OpenID Connect sign-in settings, read from the environment.

SSO is on when ``NOJOIN_OIDC_ISSUER``, ``NOJOIN_OIDC_CLIENT_ID`` and
``NOJOIN_OIDC_CLIENT_SECRET`` are all set. Setting only some of them is a
configuration error: :func:`get_oidc_settings` raises, so the api refuses to
start instead of running with a half-configured login.

``NOJOIN_PASSWORD_LOGIN`` decides who may still sign in with a password:

* ``enabled`` (default): everyone with a local account, as before SSO existed.
* ``owner_only``: only accounts with the owner role. Keeps a break-glass login
  for when the identity provider is unreachable.
* ``disabled``: nobody. Every sign-in goes through the identity provider.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from typing import Optional

from backend.utils.config_manager import get_trusted_web_origin

ISSUER_ENV = "NOJOIN_OIDC_ISSUER"
CLIENT_ID_ENV = "NOJOIN_OIDC_CLIENT_ID"
CLIENT_SECRET_ENV = "NOJOIN_OIDC_CLIENT_SECRET"
DISPLAY_NAME_ENV = "NOJOIN_OIDC_DISPLAY_NAME"
SCOPES_ENV = "NOJOIN_OIDC_SCOPES"
USERNAME_CLAIM_ENV = "NOJOIN_OIDC_USERNAME_CLAIM"
GROUPS_CLAIM_ENV = "NOJOIN_OIDC_GROUPS_CLAIM"
ALLOWED_GROUPS_ENV = "NOJOIN_OIDC_ALLOWED_GROUPS"
ADMIN_GROUPS_ENV = "NOJOIN_OIDC_ADMIN_GROUPS"
AUTO_CREATE_ENV = "NOJOIN_OIDC_AUTO_CREATE"
PASSWORD_LOGIN_ENV = "NOJOIN_PASSWORD_LOGIN"

OIDC_CALLBACK_PATH = "/api/v1/login/oidc/callback"

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


class OIDCConfigurationError(RuntimeError):
    """The OIDC environment variables are inconsistent."""


class PasswordLoginPolicy(str, Enum):
    ENABLED = "enabled"
    OWNER_ONLY = "owner_only"
    DISABLED = "disabled"


@dataclass(frozen=True)
class OIDCSettings:
    issuer: str
    client_id: str
    client_secret: str
    display_name: str
    scopes: tuple[str, ...]
    username_claim: str
    groups_claim: str
    allowed_groups: frozenset[str]
    admin_groups: frozenset[str]
    auto_create: bool

    @property
    def redirect_uri(self) -> str:
        return f"{get_trusted_web_origin()}{OIDC_CALLBACK_PATH}"

    @property
    def discovery_url(self) -> str:
        return f"{self.issuer.rstrip('/')}/.well-known/openid-configuration"


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _parse_list(value: str) -> frozenset[str]:
    return frozenset(item.strip() for item in value.split(",") if item.strip())


def _parse_bool(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    raise OIDCConfigurationError(f"{name} must be true or false, not {raw!r}.")


@lru_cache(maxsize=1)
def get_oidc_settings() -> Optional[OIDCSettings]:
    """The OIDC settings, or ``None`` when SSO is not configured."""
    issuer = _env(ISSUER_ENV)
    client_id = _env(CLIENT_ID_ENV)
    client_secret = _env(CLIENT_SECRET_ENV)
    present = [bool(issuer), bool(client_id), bool(client_secret)]
    if not any(present):
        return None
    if not all(present):
        missing = [
            name
            for name, value in (
                (ISSUER_ENV, issuer),
                (CLIENT_ID_ENV, client_id),
                (CLIENT_SECRET_ENV, client_secret),
            )
            if not value
        ]
        raise OIDCConfigurationError(
            "OIDC sign-in is partly configured. Also set " + ", ".join(missing) + "."
        )
    if not issuer.startswith("https://") and not issuer.startswith("http://localhost"):
        raise OIDCConfigurationError(f"{ISSUER_ENV} must be an https:// URL.")

    scopes = tuple(_env(SCOPES_ENV).split()) or ("openid", "profile", "email", "groups")
    if "openid" not in scopes:
        scopes = ("openid", *scopes)

    return OIDCSettings(
        issuer=issuer,
        client_id=client_id,
        client_secret=client_secret,
        display_name=_env(DISPLAY_NAME_ENV) or "SSO",
        scopes=scopes,
        username_claim=_env(USERNAME_CLAIM_ENV) or "preferred_username",
        groups_claim=_env(GROUPS_CLAIM_ENV) or "groups",
        allowed_groups=_parse_list(_env(ALLOWED_GROUPS_ENV)),
        admin_groups=_parse_list(_env(ADMIN_GROUPS_ENV)),
        auto_create=_parse_bool(AUTO_CREATE_ENV, True),
    )


@lru_cache(maxsize=1)
def get_password_login_policy() -> PasswordLoginPolicy:
    raw = _env(PASSWORD_LOGIN_ENV).lower() or PasswordLoginPolicy.ENABLED.value
    try:
        return PasswordLoginPolicy(raw)
    except ValueError:
        allowed = ", ".join(policy.value for policy in PasswordLoginPolicy)
        raise OIDCConfigurationError(
            f"{PASSWORD_LOGIN_ENV} must be one of {allowed}, not {raw!r}."
        ) from None


def reset_oidc_settings_cache() -> None:
    """Forget cached settings. For tests that change the environment."""
    get_oidc_settings.cache_clear()
    get_password_login_policy.cache_clear()


def validate_sign_in_configuration() -> None:
    """Fail api startup on a sign-in configuration that cannot work.

    Raises :class:`OIDCConfigurationError` for partial OIDC settings, an unknown
    password policy, or password login turned off with no SSO to replace it.
    """
    reset_oidc_settings_cache()
    settings = get_oidc_settings()
    policy = get_password_login_policy()
    if settings is None and policy is PasswordLoginPolicy.DISABLED:
        raise OIDCConfigurationError(
            f"{PASSWORD_LOGIN_ENV}=disabled needs OIDC sign-in configured, "
            "or nobody could sign in."
        )
