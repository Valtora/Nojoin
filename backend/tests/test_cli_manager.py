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
from backend.models.cli_oauth import CliOAuthCredential, CliUsageDaily
from backend.processing.cli.env_scrub import (
    CODEX_SCRUBBED_ENV_VARS,
    OAUTH_TOKEN_ENV_VAR,
    SCRUBBED_ENV_VARS,
    codex_child_env,
    scrubbed_environ,
    subscription_env_payload,
)
from backend.processing.cli.manager import (
    CliConversationManager,
    CliOAuthUnavailableError,
    CliUsageLimitError,
    _apply_result_usage,
    _as_int,
    _classify_sdk_error,
    _rate_limit_reading,
    _rate_limit_rejection,
    _RateLimitReading,
    _TurnUsage,
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
    last_utilization FLOAT,
    last_rate_limit_status VARCHAR(32),
    last_rate_limit_type VARCHAR(32),
    last_rate_limit_at TIMESTAMP,
    CONSTRAINT uq_cli_oauth_credential_user_provider UNIQUE (user_id, provider)
)
"""

_CREATE_CLI_USAGE_DAILY = """
CREATE TABLE cli_usage_daily (
    id INTEGER PRIMARY KEY,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    user_id BIGINT NOT NULL,
    provider VARCHAR(32) NOT NULL,
    usage_date DATE NOT NULL,
    input_tokens BIGINT NOT NULL DEFAULT 0,
    output_tokens BIGINT NOT NULL DEFAULT 0,
    cache_read_input_tokens BIGINT NOT NULL DEFAULT 0,
    cache_creation_input_tokens BIGINT NOT NULL DEFAULT 0,
    request_count INTEGER NOT NULL DEFAULT 0,
    total_cost_usd FLOAT NOT NULL DEFAULT 0,
    CONSTRAINT uq_cli_usage_daily_user_provider_date UNIQUE (user_id, provider, usage_date)
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
        conn.execute(text(_CREATE_CLI_USAGE_DAILY))
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


def test_codex_child_env_excludes_openai_keys_and_sets_home(monkeypatch):
    """The Codex scrub: OPENAI_API_KEY / CODEX_API_KEY must never reach the
    subprocess (they would bill the API instead of the subscription), CODEX_HOME
    is injected, and unrelated vars pass through."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-install-openai")
    monkeypatch.setenv("CODEX_API_KEY", "sk-codex")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.internal")
    monkeypatch.setenv("HARMLESS_SENTINEL", "keep-me")

    env = codex_child_env("/data/cli-oauth/1/codex")

    for var in CODEX_SCRUBBED_ENV_VARS:
        assert var not in env, f"{var} leaked into the Codex subprocess env"
    assert env["CODEX_HOME"] == "/data/cli-oauth/1/codex"
    assert env["HARMLESS_SENTINEL"] == "keep-me"
    # os.environ itself is untouched (nothing removed in place).
    assert os.environ["OPENAI_API_KEY"] == "sk-install-openai"


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


# --- token-usage accounting ---


def _read_usage(engine) -> list[CliUsageDaily]:
    with Session(engine) as session:
        return session.exec(
            select(CliUsageDaily).where(CliUsageDaily.user_id == 1)
        ).all()


def test_apply_result_usage_reads_tokens_and_cost():
    turn = _TurnUsage()
    message = SimpleNamespace(
        usage={
            "input_tokens": 120,
            "output_tokens": 45,
            "cache_read_input_tokens": 10,
            "cache_creation_input_tokens": 5,
        },
        total_cost_usd=0.0123,
    )
    _apply_result_usage(turn, message)
    assert turn.usage is not None and turn.usage["input_tokens"] == 120
    assert turn.total_cost_usd == 0.0123
    assert turn.has_data


def test_apply_result_usage_ignores_missing_fields():
    turn = _TurnUsage()
    _apply_result_usage(turn, SimpleNamespace(usage=None, total_cost_usd=None))
    assert turn.usage is None
    assert turn.total_cost_usd is None
    assert not turn.has_data


def test_rate_limit_reading_captures_any_status():
    warning = SimpleNamespace(
        rate_limit_info=SimpleNamespace(
            status="allowed_warning", utilization=0.83, rate_limit_type="five_hour"
        )
    )
    reading = _rate_limit_reading(warning)
    assert reading is not None
    assert reading.status == "allowed_warning"
    assert reading.utilization == 0.83
    assert reading.rate_limit_type == "five_hour"
    # Not a RateLimitEvent -> None (unlike _rate_limit_rejection, this keeps
    # non-rejection events, but still returns None when there is no info).
    assert _rate_limit_reading(SimpleNamespace()) is None


def test_as_int_coerces_defensively():
    assert _as_int(7) == 7
    assert _as_int(None) == 0
    assert _as_int("nope") == 0
    assert _as_int(-4) == 0


def test_record_usage_increments_daily_rollup(monkeypatch):
    engine = _sqlite_engine()
    _seed(engine, expires_at=utc_now() + timedelta(hours=2))
    monkeypatch.setattr(
        "backend.processing.cli.manager.get_sync_session", lambda: Session(engine)
    )
    manager = CliConversationManager()
    usage = {
        "input_tokens": 100,
        "output_tokens": 40,
        "cache_read_input_tokens": 8,
        "cache_creation_input_tokens": 2,
    }
    manager._record_usage(
        1,
        _TurnUsage(
            usage=usage,
            total_cost_usd=0.01,
            reading=_RateLimitReading("allowed_warning", "five_hour", 0.5),
        ),
    )
    # A second turn the same day accumulates into the same (user, provider, date)
    # row rather than creating a duplicate.
    manager._record_usage(1, _TurnUsage(usage=usage, total_cost_usd=0.01))

    rows = _read_usage(engine)
    assert len(rows) == 1
    row = rows[0]
    assert row.input_tokens == 200
    assert row.output_tokens == 80
    assert row.cache_read_input_tokens == 16
    assert row.cache_creation_input_tokens == 4
    assert row.request_count == 2
    assert row.total_cost_usd == pytest.approx(0.02)

    # The latest rate-limit reading landed on the credential row.
    cred = _read(engine)
    assert cred.last_utilization == 0.5
    assert cred.last_rate_limit_status == "allowed_warning"
    assert cred.last_rate_limit_type == "five_hour"
    assert cred.last_rate_limit_at is not None


def test_record_usage_without_data_is_noop(monkeypatch):
    engine = _sqlite_engine()
    _seed(engine, expires_at=utc_now() + timedelta(hours=2))
    monkeypatch.setattr(
        "backend.processing.cli.manager.get_sync_session", lambda: Session(engine)
    )
    CliConversationManager()._record_usage(1, _TurnUsage())
    assert _read_usage(engine) == []


def test_codex_login_parses_verification_url_and_code():
    """The pty-output parsing is the fragile bit — lock it to real codex output."""
    from backend.processing.cli import codex_login

    # Captured verbatim from `codex login --device-auth` under a pty (ANSI-coded).
    sample = (
        "\x1b[90mOpenAI's command-line coding agent\x1b[0m\n"
        "1. Open this link in your browser and sign in\n"
        "   \x1b[94mhttps://auth.openai.com/codex/device\x1b[0m\n"
        "2. Enter this one-time code \x1b[90m(expires in 15 minutes)\x1b[0m\n"
        "\x1b[94mXES2-55YBM\x1b[0m\n"
    )
    stripped = codex_login._strip_ansi(sample)
    assert (
        codex_login._URL.search(stripped).group(0)
        == "https://auth.openai.com/codex/device"
    )
    assert codex_login._CODE.search(stripped).group(1) == "XES2-55YBM"


def test_codex_model_catalog_parse_filters_and_sorts():
    """The model picker's live catalogue: keep visibility=list, order by priority."""
    from backend.processing.cli.codex_models import _parse_catalog

    raw = (
        '{"models":['
        '{"slug":"gpt-5.4","display_name":"GPT-5.4","visibility":"list","priority":16},'
        '{"slug":"hidden","display_name":"H","visibility":"hide","priority":2},'
        '{"slug":"gpt-5.6-sol","display_name":"GPT-5.6-Sol","visibility":"list","priority":1}'
        "]}"
    )
    assert _parse_catalog(raw) == [
        {"id": "gpt-5.6-sol", "label": "GPT-5.6-Sol"},
        {"id": "gpt-5.4", "label": "GPT-5.4"},
    ]
    # Unparseable output -> empty (the API then serves the curated fallback).
    assert _parse_catalog("not json at all") == []
