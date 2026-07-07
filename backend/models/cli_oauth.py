from datetime import date, datetime
from enum import Enum
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlmodel import Field

from backend.models.base import BaseDBModel


class CliOAuthProvider(str, Enum):
    """Subscription-CLI providers Nojoin can route inference through.

    Only Claude Code is supported in the first cut; Codex/OpenAI is deferred.
    """

    CLAUDE_CODE = "claude_code"


class CliOAuthCredentialStatus(str, Enum):
    ACTIVE = "active"
    NEEDS_REAUTH = "needs_reauth"
    REVOKED = "revoked"


class CliOAuthCredential(BaseDBModel, table=True):
    """A user's subscription-CLI OAuth credential (e.g. Claude Pro/Max).

    One row per ``(user_id, provider)``. The bearer/refresh tokens are stored
    encrypted at rest via ``encrypt_secret`` (never in ``User.settings``, which
    is unencrypted JSONB), mirroring ``CalendarConnection``. The subprocess that
    drives the CLI reads the decrypted credential into a per-user
    ``CLAUDE_CONFIG_DIR``; nothing here is surfaced to the API unmasked.
    """

    __tablename__ = "cli_oauth_credentials"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "provider",
            name="uq_cli_oauth_credential_user_provider",
        ),
    )

    user_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    provider: str = Field(
        default=CliOAuthProvider.CLAUDE_CODE.value,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    status: str = Field(
        default=CliOAuthCredentialStatus.ACTIVE.value,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    access_token_encrypted: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    refresh_token_encrypted: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    token_expires_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )
    # Populated only if the device-code flow issues a per-client id we must
    # replay on refresh; left nullable because not every flow needs it.
    oauth_client_id: Optional[str] = Field(
        default=None, sa_column=Column(String(512), nullable=True)
    )
    last_refreshed_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )
    # Best-effort parse of the CLI's rate-limit error text; advisory only, used
    # to surface a reset time in Settings and to skip doomed spawns.
    usage_limited_until: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )
    # Latest-known rate-limit reading from the SDK's ``RateLimitEvent`` (advisory).
    # ``last_utilization`` is the fraction (0.0-1.0) of the current window
    # consumed; the CLI emits the event only on status transitions, so this is an
    # opportunistic "most recent" value, not a guaranteed per-call reading. A
    # subscription exposes no absolute remaining-token count. Surfaced in the
    # admin CLI usage panel's quota column.
    last_utilization: Optional[float] = Field(
        default=None, sa_column=Column(Float, nullable=True)
    )
    last_rate_limit_status: Optional[str] = Field(
        default=None, sa_column=Column(String(32), nullable=True)
    )
    last_rate_limit_type: Optional[str] = Field(
        default=None, sa_column=Column(String(32), nullable=True)
    )
    last_rate_limit_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )


class CliUsageDaily(BaseDBModel, table=True):
    """Per-user, per-day rollup of subscription-CLI token usage.

    One row per ``(user_id, provider, usage_date)`` (UTC date), upserted by the
    worker-io CLI manager after each successful inference and read by the admin
    usage panel. Stores only aggregate token counts — never prompt or response
    content. A subscription is flat-rate, so ``total_cost_usd`` is the SDK's
    notional API-equivalent figure: stored for reference, never surfaced as money.
    """

    __tablename__ = "cli_usage_daily"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "provider",
            "usage_date",
            name="uq_cli_usage_daily_user_provider_date",
        ),
    )

    user_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    provider: str = Field(
        default=CliOAuthProvider.CLAUDE_CODE.value,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    usage_date: date = Field(sa_column=Column(Date, nullable=False, index=True))
    input_tokens: int = Field(
        default=0,
        sa_column=Column(BigInteger, nullable=False, server_default="0"),
    )
    output_tokens: int = Field(
        default=0,
        sa_column=Column(BigInteger, nullable=False, server_default="0"),
    )
    cache_read_input_tokens: int = Field(
        default=0,
        sa_column=Column(BigInteger, nullable=False, server_default="0"),
    )
    cache_creation_input_tokens: int = Field(
        default=0,
        sa_column=Column(BigInteger, nullable=False, server_default="0"),
    )
    request_count: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    total_cost_usd: float = Field(
        default=0.0,
        sa_column=Column(Float, nullable=False, server_default="0"),
    )
