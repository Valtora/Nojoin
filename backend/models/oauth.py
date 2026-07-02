"""OAuth 2.1 authorization-server state for the MCP connector.

Nojoin acts as its own OAuth 2.1 authorization server so MCP clients
(claude.ai custom connectors, Claude Code, and other spec-compliant clients)
can connect with nothing but the server URL. Clients self-register via
Dynamic Client Registration (RFC 7591), obtain single-use PKCE-bound
authorization codes, and exchange them for short-lived MCP access JWTs plus
rotating refresh tokens.

Authorization codes and refresh tokens are stored as SHA-256 hashes only;
the plaintext secret is returned to the client once and never persisted.
"""

from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlmodel import Field, SQLModel

from backend.utils.time import utc_now


class OAuthClient(SQLModel, table=True):
    __tablename__ = "oauth_clients"

    client_id: str = Field(primary_key=True, max_length=64)
    client_name: Optional[str] = Field(default=None, max_length=256)
    # JSON-encoded list of exact redirect URIs registered by the client.
    redirect_uris: str = Field(sa_column=sa.Column(sa.Text(), nullable=False))
    token_endpoint_auth_method: str = Field(default="none", max_length=32)
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=sa.Column(sa.DateTime(), nullable=False),
    )
    last_used_at: Optional[datetime] = Field(
        default=None,
        sa_column=sa.Column(sa.DateTime(), nullable=True),
    )


class OAuthAuthorizationCode(SQLModel, table=True):
    __tablename__ = "oauth_authorization_codes"

    code_hash: str = Field(primary_key=True, max_length=64)
    client_id: str = Field(
        sa_column=sa.Column(
            sa.String(64),
            sa.ForeignKey("oauth_clients.client_id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    user_id: int = Field(
        sa_column=sa.Column(
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    redirect_uri: str = Field(sa_column=sa.Column(sa.Text(), nullable=False))
    scope: str = Field(max_length=256)
    code_challenge: str = Field(max_length=128)
    code_challenge_method: str = Field(default="S256", max_length=16)
    resource: Optional[str] = Field(
        default=None, sa_column=sa.Column(sa.Text(), nullable=True)
    )
    expires_at: datetime = Field(
        sa_column=sa.Column(sa.DateTime(), nullable=False, index=True)
    )
    used_at: Optional[datetime] = Field(
        default=None,
        sa_column=sa.Column(sa.DateTime(), nullable=True),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=sa.Column(sa.DateTime(), nullable=False),
    )


class OAuthRefreshToken(SQLModel, table=True):
    __tablename__ = "oauth_refresh_tokens"

    token_hash: str = Field(primary_key=True, max_length=64)
    # A grant groups every rotation of the same user consent. Revoking a
    # grant invalidates every refresh token in the family at once.
    grant_id: str = Field(max_length=64, index=True)
    client_id: str = Field(
        sa_column=sa.Column(
            sa.String(64),
            sa.ForeignKey("oauth_clients.client_id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    user_id: int = Field(
        sa_column=sa.Column(
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    scope: str = Field(max_length=256)
    resource: Optional[str] = Field(
        default=None, sa_column=sa.Column(sa.Text(), nullable=True)
    )
    expires_at: datetime = Field(
        sa_column=sa.Column(sa.DateTime(), nullable=False, index=True)
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=sa.Column(sa.DateTime(), nullable=False),
    )
    last_used_at: Optional[datetime] = Field(
        default=None,
        sa_column=sa.Column(sa.DateTime(), nullable=True),
    )
    revoked_at: Optional[datetime] = Field(
        default=None,
        sa_column=sa.Column(sa.DateTime(), nullable=True),
    )
