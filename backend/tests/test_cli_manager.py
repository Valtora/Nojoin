"""Unit tests for the CLI OAuth conversation manager.

Covers the two behaviours that must not silently regress: the subprocess env
scrub (the security invariant that keeps a worker ANTHROPIC_API_KEY from
out-ranking the user's subscription token) and the on-demand token refresh
lifecycle. Neither touches the Claude Agent SDK, so these run without the io
image — the SDK is imported lazily inside the manager's inference methods only.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from backend.core.encryption import decrypt_secret, encrypt_secret

# Register every ORM model so SQLAlchemy can configure the mapper reached via the
# credential's user_id FK.
from backend.models import registry  # noqa: F401
from backend.models.cli_oauth import CliOAuthCredential
from backend.processing.cli.env_scrub import (
    OAUTH_TOKEN_ENV_VAR,
    SCRUBBED_ENV_VARS,
    scrubbed_environ,
    subscription_env_payload,
)
from backend.processing.cli.manager import (
    CliConversationManager,
    CliOAuthUnavailableError,
    CliUsageLimitError,
    _classify_sdk_error,
    _rate_limit_rejection,
    _usage_limit_message,
)
from backend.services.cli_oauth import oauth
from backend.services.cli_oauth.oauth import OAuthTokens
from backend.utils.time import utc_now

_CREATE_CLI_OAUTH_CREDENTIALS = """
CREATE TABLE cli_oauth_credentials (
    id INTEGER PRIMARY KEY,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    user_id BIGINT NOT NULL,
    provider VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    access_token_encrypted TEXT,
    refresh_token_encrypted TEXT,
    token_expires_at TIMESTAMP,
    oauth_client_id VARCHAR(512),
    last_refreshed_at TIMESTAMP,
    usage_limited_until TIMESTAMP,
    CONSTRAINT uq_cli_oauth_credential_user_provider UNIQUE (user_id, provider)
)
"""


def _sqlite_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        conn.execute(text(_CREATE_CLI_OAUTH_CREDENTIALS))
    return engine


def _seed(  # noqa: PLR0913 - test fixture builder; keyword-only knobs
    engine,
    *,
    expires_at,
    access="access-1",
    refresh="refresh-1",
    status="active",
    usage_limited_until=None,
):
    with Session(engine) as session:
        session.add(
            CliOAuthCredential(
                user_id=1,
                provider="claude_code",
                status=status,
                access_token_encrypted=encrypt_secret(access) if access else None,
                refresh_token_encrypted=encrypt_secret(refresh) if refresh else None,
                token_expires_at=expires_at,
                usage_limited_until=usage_limited_until,
            )
        )
        session.commit()


def _read(engine) -> CliOAuthCredential:
    with Session(engine) as session:
        return session.exec(
            select(CliOAuthCredential).where(CliOAuthCredential.user_id == 1)
        ).first()


# --- env scrub (the security invariant) ---


def test_scrub_removes_key_auth_and_injects_token_through_sdk_merge(monkeypatch):
    """Simulate the SDK's env merge and assert none of the key-auth vars survive."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-install-key")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "auth-tok")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
    monkeypatch.setenv("CLAUDE_CODE_USE_FOUNDRY", "1")
    monkeypatch.setenv("HARMLESS_SENTINEL", "keep-me")

    token = "sk-ant-oat01-user"
    with scrubbed_environ():
        payload = subscription_env_payload(token)
        # Reproduce claude_agent_sdk .../transport/subprocess_cli.py: the worker's
        # os.environ (minus CLAUDECODE) is the base, overlaid by options.env.
        inherited = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        effective = {**inherited, "CLAUDE_CODE_ENTRYPOINT": "sdk-py", **payload}

        assert effective[OAUTH_TOKEN_ENV_VAR] == token
        for var in SCRUBBED_ENV_VARS:
            assert var not in effective, f"{var} leaked into the subprocess env"
        assert effective["HARMLESS_SENTINEL"] == "keep-me"

    # Restored after the block; the token is not left in the worker env.
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-install-key"
    assert os.environ["CLAUDE_CODE_USE_BEDROCK"] == "1"
    assert OAUTH_TOKEN_ENV_VAR not in os.environ


def test_scrub_leaves_absent_vars_absent(monkeypatch):
    for var in SCRUBBED_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    with scrubbed_environ():
        for var in SCRUBBED_ENV_VARS:
            assert var not in os.environ
    for var in SCRUBBED_ENV_VARS:
        assert var not in os.environ


# --- token refresh lifecycle ---


def test_fresh_token_used_without_refresh(monkeypatch):
    engine = _sqlite_engine()
    _seed(engine, expires_at=utc_now() + timedelta(hours=2))
    monkeypatch.setattr(
        "backend.processing.cli.manager.get_sync_session", lambda: Session(engine)
    )

    async def _must_not_refresh(_):
        raise AssertionError("refresh must not be called for a fresh token")

    monkeypatch.setattr(oauth, "refresh_tokens", _must_not_refresh)

    assert CliConversationManager()._resolve_access_token(1) == "access-1"


def test_expired_token_is_refreshed_and_rotation_persisted(monkeypatch):
    engine = _sqlite_engine()
    _seed(
        engine,
        expires_at=utc_now() - timedelta(minutes=1),
        access="access-old",
        refresh="refresh-old",
    )
    monkeypatch.setattr(
        "backend.processing.cli.manager.get_sync_session", lambda: Session(engine)
    )

    async def _refresh(refresh_token):
        assert refresh_token == "refresh-old"
        return OAuthTokens(
            access_token="access-new",
            refresh_token="refresh-new",
            expires_in=28800,
            scope="user:inference",
        )

    monkeypatch.setattr(oauth, "refresh_tokens", _refresh)

    assert CliConversationManager()._resolve_access_token(1) == "access-new"
    cred = _read(engine)
    assert decrypt_secret(cred.access_token_encrypted) == "access-new"
    assert decrypt_secret(cred.refresh_token_encrypted) == "refresh-new"
    assert cred.status == "active"
    assert cred.token_expires_at > utc_now()


def test_refresh_failure_sets_needs_reauth_and_raises(monkeypatch):
    engine = _sqlite_engine()
    _seed(engine, expires_at=utc_now() - timedelta(minutes=1), refresh="refresh-old")
    monkeypatch.setattr(
        "backend.processing.cli.manager.get_sync_session", lambda: Session(engine)
    )

    async def _boom(_):
        raise oauth.CliOAuthExchangeError("429 headless lockout")

    monkeypatch.setattr(oauth, "refresh_tokens", _boom)

    with pytest.raises(CliOAuthUnavailableError):
        CliConversationManager()._resolve_access_token(1)
    assert _read(engine).status == "needs_reauth"


def test_expired_without_refresh_token_sets_needs_reauth(monkeypatch):
    engine = _sqlite_engine()
    _seed(engine, expires_at=utc_now() - timedelta(minutes=1), refresh=None)
    monkeypatch.setattr(
        "backend.processing.cli.manager.get_sync_session", lambda: Session(engine)
    )

    with pytest.raises(CliOAuthUnavailableError):
        CliConversationManager()._resolve_access_token(1)
    assert _read(engine).status == "needs_reauth"


def test_missing_credential_raises(monkeypatch):
    engine = _sqlite_engine()
    monkeypatch.setattr(
        "backend.processing.cli.manager.get_sync_session", lambda: Session(engine)
    )
    with pytest.raises(CliOAuthUnavailableError):
        CliConversationManager()._resolve_access_token(999)


def test_revoked_credential_raises(monkeypatch):
    engine = _sqlite_engine()
    _seed(engine, expires_at=utc_now() + timedelta(hours=2), status="revoked")
    monkeypatch.setattr(
        "backend.processing.cli.manager.get_sync_session", lambda: Session(engine)
    )
    with pytest.raises(CliOAuthUnavailableError):
        CliConversationManager()._resolve_access_token(1)


# --- usage-limit handling (M5) ---


def test_skip_when_usage_limited_raises_with_reset(monkeypatch):
    engine = _sqlite_engine()
    reset = utc_now() + timedelta(hours=1)
    _seed(
        engine,
        expires_at=utc_now() + timedelta(hours=2),
        usage_limited_until=reset,
    )
    monkeypatch.setattr(
        "backend.processing.cli.manager.get_sync_session", lambda: Session(engine)
    )
    with pytest.raises(CliUsageLimitError) as exc_info:
        CliConversationManager()._resolve_access_token(1)
    assert exc_info.value.resets_at == reset


def test_stale_usage_limit_is_ignored(monkeypatch):
    engine = _sqlite_engine()
    _seed(
        engine,
        expires_at=utc_now() + timedelta(hours=2),
        usage_limited_until=utc_now() - timedelta(minutes=1),
    )
    monkeypatch.setattr(
        "backend.processing.cli.manager.get_sync_session", lambda: Session(engine)
    )
    # Past its reset -> not limited; resolves the token normally.
    assert CliConversationManager()._resolve_access_token(1) == "access-1"


def test_persist_usage_limited_sets_column(monkeypatch):
    engine = _sqlite_engine()
    _seed(engine, expires_at=utc_now() + timedelta(hours=2))
    monkeypatch.setattr(
        "backend.processing.cli.manager.get_sync_session", lambda: Session(engine)
    )
    reset = utc_now() + timedelta(hours=3)
    CliConversationManager()._persist_usage_limited(1, reset)
    assert _read(engine).usage_limited_until == reset


def test_persist_usage_limited_noop_without_reset(monkeypatch):
    engine = _sqlite_engine()
    _seed(engine, expires_at=utc_now() + timedelta(hours=2))
    monkeypatch.setattr(
        "backend.processing.cli.manager.get_sync_session", lambda: Session(engine)
    )
    CliConversationManager()._persist_usage_limited(1, None)
    assert _read(engine).usage_limited_until is None


def test_rate_limit_rejection_parses_reset():
    epoch = 1_900_000_000
    message = SimpleNamespace(
        rate_limit_info=SimpleNamespace(
            status="rejected", resets_at=epoch, rate_limit_type="five_hour"
        )
    )
    dt, rl_type = _rate_limit_rejection(message)
    assert rl_type == "five_hour"
    assert dt == datetime.fromtimestamp(epoch, tz=timezone.utc).replace(tzinfo=None)


def test_rate_limit_non_rejection_returns_none():
    warning = SimpleNamespace(
        rate_limit_info=SimpleNamespace(
            status="allowed_warning", resets_at=1, rate_limit_type="five_hour"
        )
    )
    assert _rate_limit_rejection(warning) is None
    assert _rate_limit_rejection(SimpleNamespace()) is None  # not a RateLimitEvent


def test_classify_sdk_error_maps_limits():
    assert isinstance(
        _classify_sdk_error(Exception("HTTP 429 rate limit")), CliUsageLimitError
    )
    assert isinstance(
        _classify_sdk_error(Exception("quota exceeded")), CliUsageLimitError
    )
    generic = _classify_sdk_error(Exception("connection reset by peer"))
    assert isinstance(generic, CliOAuthUnavailableError)
    assert not isinstance(generic, CliUsageLimitError)


def test_usage_limit_message_includes_reset_and_window():
    message = _usage_limit_message(datetime(2026, 7, 7, 15, 30), "five_hour")
    assert "usage limit" in message.lower()
    assert "five_hour" in message
    assert "15:30" in message
